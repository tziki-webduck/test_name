"""JSON file persistence for devices, rules and settings."""

import copy
import json
import os
import threading
import uuid

from .zmanim import CITIES

DEFAULT_CITY = "ירושלים"

DEFAULTS = {
    "devices": [],
    "rules": [],
    "settings": {
        "city": DEFAULT_CITY,
        "lat": CITIES[DEFAULT_CITY][0],
        "lon": CITIES[DEFAULT_CITY][1],
        "candle_minutes": CITIES[DEFAULT_CITY][2],
        "havdalah_mode": "tzeit",
        "havdalah_minutes": 0,
        "timezone": "Asia/Jerusalem",
        "shabbat_enforce": True,
        "enforce_interval_min": 10,
        "poll_interval_sec": 30,
    },
    "rule_state": {},
}


def new_id():
    return uuid.uuid4().hex[:10]


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            for key, value in loaded.items():
                if key == "settings":
                    self.data["settings"].update(value)
                else:
                    self.data[key] = value

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)

    # --- devices -------------------------------------------------------
    def devices(self):
        with self.lock:
            return copy.deepcopy(self.data["devices"])

    def device(self, dev_id):
        with self.lock:
            for d in self.data["devices"]:
                if d["id"] == dev_id:
                    return copy.deepcopy(d)
        return None

    def add_device(self, name, host, port=8888):
        with self.lock:
            for d in self.data["devices"]:
                if d["host"] == host and int(d.get("port", 8888)) == int(port):
                    return copy.deepcopy(d)
            dev = {"id": new_id(), "name": name or host, "host": host, "port": int(port)}
            self.data["devices"].append(dev)
            self.save()
            return copy.deepcopy(dev)

    def update_device(self, dev_id, fields):
        with self.lock:
            for d in self.data["devices"]:
                if d["id"] == dev_id:
                    for key in ("name", "host", "port"):
                        if key in fields:
                            d[key] = int(fields[key]) if key == "port" else str(fields[key]).strip()
                    self.save()
                    return copy.deepcopy(d)
        return None

    def delete_device(self, dev_id):
        with self.lock:
            self.data["devices"] = [d for d in self.data["devices"] if d["id"] != dev_id]
            for rule in self.data["rules"]:
                rule["devices"] = [x for x in rule.get("devices", []) if x != dev_id]
            self.save()

    # --- rules ---------------------------------------------------------
    def rules(self):
        with self.lock:
            return copy.deepcopy(self.data["rules"])

    def put_rule(self, rule):
        with self.lock:
            rule = copy.deepcopy(rule)
            rule.setdefault("id", new_id())
            rules = self.data["rules"]
            for i, r in enumerate(rules):
                if r["id"] == rule["id"]:
                    rules[i] = rule
                    break
            else:
                rules.append(rule)
            self.save()
            return copy.deepcopy(rule)

    def delete_rule(self, rule_id):
        with self.lock:
            self.data["rules"] = [r for r in self.data["rules"] if r["id"] != rule_id]
            self.data["rule_state"].pop(rule_id, None)
            self.save()

    def rule_last_fired(self, rule_id):
        with self.lock:
            return self.data["rule_state"].get(rule_id)

    def set_rule_last_fired(self, rule_id, ts):
        with self.lock:
            self.data["rule_state"][rule_id] = ts
            self.save()

    # --- settings ------------------------------------------------------
    def settings(self):
        with self.lock:
            return copy.deepcopy(self.data["settings"])

    def update_settings(self, fields):
        with self.lock:
            settings = self.data["settings"]
            fields = dict(fields)
            city = fields.get("city")
            if city and city in CITIES and city != settings.get("city"):
                # A new city brings its own coordinates and candle lighting custom.
                lat, lon, candle = CITIES[city]
                fields.update(lat=lat, lon=lon, candle_minutes=candle)
            for key in DEFAULTS["settings"]:
                if key in fields:
                    settings[key] = fields[key]
            self.save()
            return copy.deepcopy(settings)
