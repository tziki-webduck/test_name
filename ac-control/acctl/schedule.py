"""When do rules fire.

A rule looks like:

    {
      "id": "...", "name": "...", "enabled": true,
      "devices": ["<device id>", ...],
      "trigger": {"type": "weekly", "time": "07:30", "days": [0, 1, 2]}     # 0 = Sunday
               | {"type": "shabbat_start", "offset": -30}                 # minutes from candle lighting
               | {"type": "shabbat_end", "offset": 15}                    # minutes from havdalah
               | {"type": "once", "at": "2026-10-02T18:00"},              # local time
      "action": {"power": true, "mode": "cool", "temp": 24, "fan": "auto"},
      "shabbat": true,          # part of the Shabbat program (kept enforced during Shabbat)
      "delete_after": false     # remove after firing (one-off timers)
    }
"""

from datetime import datetime, timedelta

from . import zmanim


def _parse_hhmm(text):
    hh, mm = str(text).split(":")[:2]
    return int(hh), int(mm)


def sunday_index(day):
    """Day of week with Sunday = 0 ... Saturday = 6 (the Israeli week)."""
    return (day.weekday() + 1) % 7


def occurrences(rule, since, until, settings, tz):
    """All firing times of `rule` in [since, until] as aware datetimes."""
    trig = rule.get("trigger") or {}
    kind = trig.get("type")
    out = []
    if kind == "weekly":
        hh, mm = _parse_hhmm(trig.get("time", "00:00"))
        days = set(int(d) for d in trig.get("days", range(7)))
        day = since.astimezone(tz).date() - timedelta(days=1)
        last = until.astimezone(tz).date() + timedelta(days=1)
        while day <= last:
            if sunday_index(day) in days:
                when = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
                if since <= when <= until:
                    out.append(when)
            day += timedelta(days=1)
    elif kind in ("shabbat_start", "shabbat_end"):
        offset = timedelta(minutes=int(trig.get("offset", 0)))
        # Walk Shabbatot from a week before `since` until after `until`.
        ref = since - timedelta(days=8)
        while ref <= until + timedelta(days=8):
            start, end = zmanim.shabbat_window(ref, settings, tz)
            when = (start if kind == "shabbat_start" else end) + offset
            if since <= when <= until and when not in out:
                out.append(when)
            ref = end + timedelta(minutes=1)
    elif kind == "once":
        try:
            when = datetime.fromisoformat(trig["at"])
        except (KeyError, ValueError):
            return out
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz)
        if since <= when <= until:
            out.append(when)
    return sorted(out)


def last_occurrence(rule, now, lookback, settings, tz):
    occ = occurrences(rule, now - lookback, now, settings, tz)
    return occ[-1] if occ else None


def next_occurrence(rule, now, settings, tz, horizon=timedelta(days=15)):
    occ = occurrences(rule, now + timedelta(seconds=1), now + horizon, settings, tz)
    return occ[0] if occ else None


def due_rules(rules, now, last_fired, settings, tz, grace=timedelta(minutes=10)):
    """Rules that should fire now: [(rule, occurrence)].

    `last_fired` maps rule id -> timestamp of the occurrence it last fired for.
    Occurrences older than `grace` are skipped (e.g. the server was off).
    """
    due = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        occ = last_occurrence(rule, now, grace, settings, tz)
        if occ is None:
            continue
        prev = last_fired.get(rule["id"])
        if prev is None or prev < occ.timestamp():
            due.append((rule, occ))
    return due


def shabbat_desired_states(rules, now, settings, tz):
    """During Shabbat: the state each device should be in according to the
    Shabbat program, i.e. the latest Shabbat rule action that already fired
    since shortly before candle lighting. Returns {device_id: (action, rule, when)}.
    """
    start, end = zmanim.shabbat_window(now, settings, tz)
    if not (start - timedelta(hours=3) <= now < end + timedelta(minutes=30)):
        return {}
    since = start - timedelta(hours=3)
    desired = {}
    for rule in rules:
        if not rule.get("enabled", True) or not rule.get("shabbat"):
            continue
        occ = occurrences(rule, since, now, settings, tz)
        if not occ:
            continue
        when = occ[-1]
        for dev_id in rule.get("devices", []):
            if dev_id not in desired or desired[dev_id][2] < when:
                desired[dev_id] = (rule.get("action", {}), rule, when)
    return desired
