#!/usr/bin/env python3
"""AC Control - local web app for Hisense / Tornado ACs with the AEH-W4A1 module.

Run on an always-on machine in the home network (old Android phone with
Termux, Raspberry Pi, PC) and open http://<that machine>:8080 on your phone.

    python3 server.py [--port 8080] [--data data/config.json]

Only the Python standard library is needed. Set AC_PIN=1234 to require a PIN.
"""

import argparse
import json
import mimetypes
import os
import re
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from acctl import client, zmanim  # noqa: E402
from acctl.hub import Hub  # noqa: E402
from acctl.store import Store  # noqa: E402

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
CONTROL_KEYS = ("power", "mode", "temp", "fan", "swing_v", "swing_h", "turbo", "eco", "sleep", "display")


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def clean_action(raw):
    if not isinstance(raw, dict):
        raise ApiError(400, "פעולה לא תקינה")
    action = {k: raw[k] for k in CONTROL_KEYS if k in raw and raw[k] is not None and raw[k] != ""}
    if "temp" in action:
        try:
            action["temp"] = int(action["temp"])
        except (TypeError, ValueError):
            raise ApiError(400, "טמפרטורה לא תקינה")
    if "sleep" in action:
        action["sleep"] = int(action["sleep"])
    return action


def clean_rule(raw, store):
    if not isinstance(raw, dict):
        raise ApiError(400, "תזמון לא תקין")
    trig = raw.get("trigger") or {}
    kind = trig.get("type")
    if kind == "weekly":
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(trig.get("time", "")))
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ApiError(400, "שעה לא תקינה")
        days = sorted(set(int(d) for d in trig.get("days", []) if 0 <= int(d) <= 6))
        if not days:
            raise ApiError(400, "יש לבחור לפחות יום אחד")
        trigger = {"type": "weekly", "time": trig["time"], "days": days}
    elif kind in ("shabbat_start", "shabbat_end"):
        trigger = {"type": kind, "offset": int(trig.get("offset", 0))}
    elif kind == "once":
        try:
            datetime.fromisoformat(str(trig.get("at"))[:16])
        except ValueError:
            raise ApiError(400, "יש לבחור תאריך ושעה")
        trigger = {"type": "once", "at": str(trig["at"])[:16]}
    else:
        raise ApiError(400, "סוג תזמון לא מוכר")
    known = {d["id"] for d in store.devices()}
    devices = [d for d in raw.get("devices", []) if d in known]
    if not devices:
        raise ApiError(400, "יש לבחור לפחות מזגן אחד")
    action = clean_action(raw.get("action") or {})
    if "power" not in action:
        raise ApiError(400, "יש לבחור הדלקה או כיבוי")
    rule = {
        "name": str(raw.get("name") or "").strip()[:60] or "תזמון",
        "enabled": bool(raw.get("enabled", True)),
        "devices": devices,
        "trigger": trigger,
        "action": action,
        "shabbat": bool(raw.get("shabbat", False)),
        "delete_after": bool(raw.get("delete_after", kind == "once")),
    }
    if raw.get("id"):
        rule["id"] = str(raw["id"])
    return rule


class Handler(BaseHTTPRequestHandler):
    hub: Hub = None
    pin = None
    server_version = "ACControl/1.0"

    def log_message(self, fmt, *args):  # quiet access log
        pass

    # --- plumbing ----------------------------------------------------------
    def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise ApiError(400, "JSON לא תקין")

    def _dispatch(self, method):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            if method != "GET":
                return self._send(405, {"error": "method not allowed"})
            return self._static(path)
        if self.pin and self.headers.get("X-PIN") != self.pin:
            return self._send(401, {"error": "נדרש קוד"})
        try:
            result = self._api(method, path)
            self._send(200, result if result is not None else {"ok": True})
        except ApiError as exc:
            self._send(exc.status, {"error": str(exc)})
        except KeyError as exc:
            self._send(404, {"error": str(exc).strip("'\"")})
        except client.ACError as exc:
            self._send(502, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self._send(400, {"error": "ערך לא תקין: %s" % exc})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        full = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not full.startswith(WEB_DIR) or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    # --- API ---------------------------------------------------------------
    def _api(self, method, path):
        hub, store = self.hub, self.hub.store
        parts = path.strip("/").split("/")[1:]  # drop "api"

        if parts == ["devices"]:
            if method == "GET":
                return hub.devices_view()
            if method == "POST":
                body = self._body()
                host = str(body.get("host", "")).strip()
                if not re.match(r"^[\w.\-]+$", host):
                    raise ApiError(400, "כתובת IP לא תקינה")
                dev = store.add_device(str(body.get("name", "")).strip(), host, int(body.get("port") or 8888))
                hub.refresh(dev["id"])
                return dev

        if len(parts) >= 2 and parts[0] == "devices":
            dev_id = parts[1]
            if len(parts) == 2 and method == "PUT":
                dev = store.update_device(dev_id, self._body())
                if dev is None:
                    raise KeyError("מזגן לא קיים")
                return dev
            if len(parts) == 2 and method == "DELETE":
                store.delete_device(dev_id)
                hub.status.pop(dev_id, None)
                return None
            if parts[2:] == ["refresh"] and method == "POST":
                return hub.refresh(dev_id)
            if parts[2:] == ["control"] and method == "POST":
                return hub.control(dev_id, clean_action(self._body()))

        if parts == ["control"] and method == "POST":
            body = self._body()
            ids = body.get("devices") or [d["id"] for d in store.devices()]
            return hub.control_many(ids, clean_action(body.get("action") or {}))

        if parts == ["scan"] and method == "POST":
            body = self._body()
            nets = body.get("networks") or None
            found = client.discover(nets)
            known = {(d["host"], int(d["port"])) for d in store.devices()}
            for f in found:
                f["known"] = (f["host"], f["port"]) in known
            return {"networks": [str(n) for n in (nets or client.local_networks())], "found": found}

        if parts == ["rules"]:
            if method == "GET":
                return hub.rules_view()
            if method == "POST":
                return hub.save_rule(clean_rule(self._body(), store))

        if len(parts) >= 2 and parts[0] == "rules":
            rule_id = parts[1]
            rule = next((r for r in store.rules() if r["id"] == rule_id), None)
            if rule is None:
                raise KeyError("תזמון לא קיים")
            if len(parts) == 2 and method == "PUT":
                body = self._body()
                body["id"] = rule_id
                return hub.save_rule(clean_rule(body, store))
            if len(parts) == 2 and method == "DELETE":
                store.delete_rule(rule_id)
                return None
            if parts[2:] == ["run"] and method == "POST":
                return hub.run_rule(rule, "הפעלה ידנית של '%s'" % rule.get("name", ""))

        if parts == ["settings"]:
            if method == "GET":
                return {"settings": store.settings(), "cities": list(zmanim.CITIES)}
            if method == "PUT":
                return {"settings": store.update_settings(self._body()), "cities": list(zmanim.CITIES)}

        if parts == ["shabbat"] and method == "GET":
            return hub.shabbat_view()

        if parts == ["log"] and method == "GET":
            return list(hub.log)

        raise ApiError(404, "לא נמצא")


def main():
    parser = argparse.ArgumentParser(description="AC Control server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "config.json"))
    args = parser.parse_args()

    hub = Hub(Store(args.data))
    Handler.hub = hub
    Handler.pin = os.environ.get("AC_PIN") or None
    hub.start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    nets = ", ".join(str(n) for n in client.local_networks()) or "?"
    hub.event("השרת פועל על פורט %d (רשתות: %s)" % (args.port, nets))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()


if __name__ == "__main__":
    main()
