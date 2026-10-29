"""Glue between the store, the ACs and the schedule: status cache, polling,
rule execution and Shabbat enforcement."""

import collections
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import client, schedule, zmanim
from .store import Store

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None


def load_tz(name):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo


class Hub:
    def __init__(self, store: Store):
        self.store = store
        self.status = {}  # device id -> {"online", "state", "error", "updated"}
        self.log = collections.deque(maxlen=300)
        self.pool = ThreadPoolExecutor(max_workers=8)
        self._stop = threading.Event()
        self._last_enforce = 0.0

    # --- helpers ---------------------------------------------------------
    @property
    def tz(self):
        return load_tz(self.store.settings().get("timezone", "Asia/Jerusalem"))

    def now(self):
        return datetime.now(self.tz)

    def event(self, msg, level="info"):
        self.log.appendleft({"ts": time.time(), "level": level, "msg": msg})
        print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)

    def _device_or_raise(self, dev_id):
        dev = self.store.device(dev_id)
        if dev is None:
            raise KeyError("מזגן לא קיים")
        return dev

    # --- status ----------------------------------------------------------
    def refresh(self, dev_id):
        dev = self._device_or_raise(dev_id)
        try:
            state = client.get_status(dev["host"], dev["port"])
            self.status[dev_id] = {"online": True, "state": state, "error": None, "updated": time.time()}
        except client.ACError as exc:
            prev = self.status.get(dev_id, {})
            self.status[dev_id] = {"online": False, "state": prev.get("state"), "error": str(exc),
                                   "updated": time.time()}
        return self.status[dev_id]

    def refresh_all(self):
        devices = self.store.devices()
        list(self.pool.map(lambda d: self.refresh(d["id"]), devices))

    def devices_view(self):
        out = []
        for dev in self.store.devices():
            info = dict(dev)
            info.update(self.status.get(dev["id"], {"online": None, "state": None, "error": None}))
            out.append(info)
        return out

    # --- control ---------------------------------------------------------
    def control(self, dev_id, wanted, source="ידני"):
        dev = self._device_or_raise(dev_id)
        current = (self.status.get(dev_id) or {}).get("state")
        try:
            state = client.apply_state(dev["host"], dev["port"], wanted, current)
        except client.ACError as exc:
            self.status[dev_id] = {"online": False, "state": current, "error": str(exc), "updated": time.time()}
            self.event("%s: %s נכשל - %s" % (dev["name"], describe(wanted), exc), "error")
            raise
        self.status[dev_id] = {"online": True, "state": state, "error": None, "updated": time.time()}
        self.event("%s: %s (%s)" % (dev["name"], describe(wanted), source))
        return self.status[dev_id]

    def control_many(self, dev_ids, wanted, source="ידני"):
        def one(dev_id):
            try:
                return dev_id, self.control(dev_id, wanted, source), None
            except (client.ACError, KeyError) as exc:
                return dev_id, None, str(exc)
        return {d: {"status": s, "error": e} for d, s, e in self.pool.map(one, dev_ids)}

    # --- rules -----------------------------------------------------------
    def save_rule(self, rule):
        saved = self.store.put_rule(rule)
        # Never fire retroactively for times that passed before the rule was saved.
        self.store.set_rule_last_fired(saved["id"], time.time())
        return saved

    def run_rule(self, rule, source=None):
        devices = rule.get("devices", [])
        result = self.control_many(devices, rule.get("action", {}), source or "תזמון: %s" % rule.get("name", ""))
        return result

    def rules_view(self):
        now = self.now()
        settings = self.store.settings()
        out = []
        for rule in self.store.rules():
            nxt = schedule.next_occurrence(rule, now, settings, self.tz)
            rule["next"] = nxt.isoformat() if nxt else None
            out.append(rule)
        return out

    def shabbat_view(self):
        settings = self.store.settings()
        now = self.now()
        start, end = zmanim.shabbat_window(now, settings, self.tz)
        desired = schedule.shabbat_desired_states(self.store.rules(), now, settings, self.tz)
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "active": start <= now < end,
            "now": now.isoformat(),
            "enforce": bool(settings.get("shabbat_enforce")),
            "desired": {k: {"action": v[0], "rule": v[1].get("name"), "since": v[2].isoformat()}
                        for k, v in desired.items()},
        }

    def tick(self):
        """Fire due rules and keep the Shabbat program enforced."""
        settings = self.store.settings()
        tz = self.tz
        now = datetime.now(tz)
        rules = self.store.rules()
        last = {r["id"]: self.store.rule_last_fired(r["id"]) for r in rules}
        for rule, occ in schedule.due_rules(rules, now, last, settings, tz):
            self.store.set_rule_last_fired(rule["id"], occ.timestamp())
            self.event("מפעיל תזמון '%s' (%s)" % (rule.get("name", ""), occ.strftime("%a %H:%M")))
            self.run_rule(rule)
            if rule.get("delete_after"):
                self.store.delete_rule(rule["id"])

        interval = max(2, int(settings.get("enforce_interval_min", 10))) * 60
        if settings.get("shabbat_enforce") and time.time() - self._last_enforce >= interval:
            self._last_enforce = time.time()
            self.enforce_shabbat(rules, now, settings, tz)

    def enforce_shabbat(self, rules, now, settings, tz):
        """Re-apply the Shabbat program to ACs that drifted from it, e.g. after
        a power cut (the units come back off) or a restart of this server."""
        for dev_id, (action, rule, when) in schedule.shabbat_desired_states(rules, now, settings, tz).items():
            if self.store.device(dev_id) is None:
                continue
            st = self.refresh(dev_id)
            state = st.get("state")
            if not st.get("online") or state is None:
                continue
            if not matches(state, action):
                self.event("אכיפת שבת: '%s' לא במצב הנכון, מחזיר" % rule.get("name", ""), "warn")
                try:
                    self.control(dev_id, action, "אכיפת שבת")
                except client.ACError:
                    pass

    # --- background loops ------------------------------------------------
    def start(self):
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _scheduler_loop(self):
        while not self._stop.wait(15):
            try:
                self.tick()
            except Exception as exc:  # keep the scheduler alive no matter what
                self.event("שגיאה בתזמון: %s" % exc, "error")

    def _poll_loop(self):
        self._stop.wait(1)
        while not self._stop.is_set():
            try:
                self.refresh_all()
            except Exception as exc:
                self.event("שגיאה בקריאת מצב: %s" % exc, "error")
            self._stop.wait(max(10, int(self.store.settings().get("poll_interval_sec", 30))))


def matches(state, action):
    if "power" in action and action["power"] is not None and bool(state.get("power")) != bool(action["power"]):
        return False
    if action.get("power") is False:
        return True
    if action.get("mode") and state.get("mode") != action["mode"]:
        return False
    if action.get("temp") is not None and state.get("target_temp") != int(action["temp"]):
        return False
    return True


MODE_NAMES = {"cool": "קירור", "heat": "חימום", "fan": "מאוורר", "dry": "ייבוש"}
FAN_NAMES = {"auto": "אוטומטי", "mute": "שקט", "low": "נמוך", "med": "בינוני", "max": "גבוה"}


def describe(action):
    parts = []
    if action.get("power") is True:
        parts.append("הדלקה")
    elif action.get("power") is False:
        return "כיבוי"
    if action.get("mode"):
        parts.append(MODE_NAMES.get(action["mode"], action["mode"]))
    if action.get("temp") is not None:
        parts.append("%s°" % action["temp"])
    if action.get("fan"):
        parts.append("מאוורר " + FAN_NAMES.get(action["fan"], action["fan"]))
    for key, name in (("turbo", "טורבו"), ("eco", "חסכון"), ("swing_v", "נדנוד אנכי"),
                      ("swing_h", "נדנוד אופקי"), ("display", "תצוגה")):
        if key in action and action[key] is not None:
            parts.append("%s %s" % (name, "פעיל" if action[key] else "כבוי"))
    if "sleep" in action and action["sleep"] is not None:
        parts.append("שינה %s" % (action["sleep"] or "כבוי"))
    return ", ".join(parts) or "ללא שינוי"
