import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import fake_ac  # noqa: E402
from acctl import client, protocol, schedule, zmanim  # noqa: E402
from acctl.hub import Hub  # noqa: E402
from acctl.store import Store  # noqa: E402

TZ = ZoneInfo("Asia/Jerusalem")
JLM = {"lat": 31.778, "lon": 35.235, "candle_minutes": 40, "havdalah_mode": "tzeit", "havdalah_minutes": 0}


class ProtocolTest(unittest.TestCase):
    def test_frames_match_captures_from_official_app(self):
        with open(os.path.join(HERE, "vectors.json")) as f:
            vectors = json.load(f)
        for name, hexframe in vectors.items():
            frame = protocol.status_request() if name == "status_102_0" else protocol.update_frame(name)
            self.assertEqual(frame.hex(), hexframe, name)

    def test_escaped_checksum_roundtrip(self):
        frame = protocol.update_frame("temp_16_C")  # checksum 0x01F4 must be escaped
        self.assertTrue(frame.endswith(b"\x01\xF4\xF4\xF4\xFB"))
        cmd, sub, payload, ok = protocol.frame_body(frame)
        self.assertTrue(ok)
        self.assertEqual((cmd, sub, payload[4]), (0x65, 0, 33))

    def test_read_frame_handles_split_packets(self):
        frame = protocol.update_frame("temp_16_C")
        chunks = [frame[:5], frame[5:47], frame[47:]]
        self.assertEqual(protocol.read_frame(lambda n: chunks.pop(0) if chunks else b""), frame)


class CommandsTest(unittest.TestCase):
    def test_turn_on_with_mode_and_temp(self):
        cur = {"power": False, "mode": "cool", "target_temp": 24, "fan": "auto"}
        self.assertEqual(client.commands_for(cur, {"power": True, "mode": "heat", "temp": 22}),
                         ["on", "mode_heat", "temp_22_C", "speed_auto"])

    def test_skips_what_already_matches(self):
        cur = {"power": True, "mode": "cool", "target_temp": 24, "fan": "low"}
        self.assertEqual(client.commands_for(cur, {"power": True, "mode": "cool", "temp": 24}), [])
        self.assertEqual(client.commands_for(cur, {"temp": 40}), ["temp_32_C"])
        self.assertEqual(client.commands_for(cur, {"power": False}), ["off"])


class ZmanimTest(unittest.TestCase):
    def test_jerusalem_times(self):
        start, end = zmanim.shabbat_window(datetime(2026, 10, 29, 12, tzinfo=TZ), JLM, TZ)
        self.assertEqual((start.month, start.day), (10, 30))
        self.assertEqual((end.month, end.day), (10, 31))
        # Published times for that week: candle lighting 16:12, havdalah ~17:27
        self.assertLess(abs((start - datetime(2026, 10, 30, 16, 12, tzinfo=TZ)).total_seconds()), 180)
        self.assertLess(abs((end - datetime(2026, 10, 31, 17, 27, tzinfo=TZ)).total_seconds()), 300)

    def test_during_shabbat_returns_current_window(self):
        start, end = zmanim.shabbat_window(datetime(2026, 10, 31, 12, tzinfo=TZ), JLM, TZ)
        self.assertEqual(start.day, 30)
        self.assertTrue(zmanim.is_shabbat(datetime(2026, 10, 31, 12, tzinfo=TZ), JLM, TZ))
        self.assertFalse(zmanim.is_shabbat(datetime(2026, 10, 31, 20, tzinfo=TZ), JLM, TZ))


class ScheduleTest(unittest.TestCase):
    def rule(self, trigger, power=True, shabbat=False, rid="r1"):
        return {"id": rid, "name": rid, "enabled": True, "devices": ["d1"], "trigger": trigger,
                "action": {"power": power}, "shabbat": shabbat}

    def test_weekly_uses_sunday_based_days(self):
        r = self.rule({"type": "weekly", "time": "07:30", "days": [0]})  # Sunday
        nxt = schedule.next_occurrence(r, datetime(2026, 10, 29, 12, tzinfo=TZ), JLM, TZ)
        self.assertEqual(nxt, datetime(2026, 11, 1, 7, 30, tzinfo=TZ))

    def test_due_once_and_not_again(self):
        r = self.rule({"type": "weekly", "time": "07:30", "days": list(range(7))})
        now = datetime(2026, 10, 29, 7, 31, tzinfo=TZ)
        due = schedule.due_rules([r], now, {}, JLM, TZ)
        self.assertEqual(len(due), 1)
        self.assertEqual(schedule.due_rules([r], now, {"r1": due[0][1].timestamp()}, JLM, TZ), [])
        # Too late (server was down): skipped.
        self.assertEqual(schedule.due_rules([r], now + timedelta(hours=1), {}, JLM, TZ), [])

    def test_shabbat_relative_rules(self):
        r = self.rule({"type": "shabbat_start", "offset": -30})
        nxt = schedule.next_occurrence(r, datetime(2026, 10, 29, 12, tzinfo=TZ), JLM, TZ)
        start, _ = zmanim.shabbat_window(datetime(2026, 10, 29, 12, tzinfo=TZ), JLM, TZ)
        self.assertEqual(nxt, start - timedelta(minutes=30))

    def test_shabbat_desired_state(self):
        rules = [
            self.rule({"type": "shabbat_start", "offset": -30}, True, True, "a"),
            self.rule({"type": "weekly", "time": "02:00", "days": [6]}, False, True, "b"),
            self.rule({"type": "weekly", "time": "11:00", "days": [6]}, True, True, "c"),
            self.rule({"type": "weekly", "time": "06:00", "days": [6]}, False, False, "not-shabbat"),
        ]
        at = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=TZ)  # noqa: E731
        self.assertEqual(schedule.shabbat_desired_states(rules, at(29, 12), JLM, TZ), {})
        self.assertEqual(schedule.shabbat_desired_states(rules, at(30, 20), JLM, TZ)["d1"][1]["id"], "a")
        self.assertEqual(schedule.shabbat_desired_states(rules, at(31, 7), JLM, TZ)["d1"][1]["id"], "b")
        self.assertEqual(schedule.shabbat_desired_states(rules, at(31, 12), JLM, TZ)["d1"][1]["id"], "c")


class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ac = fake_ac.FakeAC()
        cls.srv = fake_ac.serve(cls.ac, "127.0.0.1", 0)
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_hub_control_and_enforcement(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Store(os.path.join(tmp, "c.json")))
            dev = hub.store.add_device("סלון", "127.0.0.1", self.port)
            st = hub.control(dev["id"], {"power": True, "mode": "cool", "temp": 23, "fan": "low"})
            self.assertEqual((st["state"]["power"], st["state"]["mode"], st["state"]["target_temp"],
                              st["state"]["fan"]), (True, "cool", 23, "low"))

            # A Shabbat program that wants the AC on; simulate a power cut turning it off.
            rule = hub.save_rule({"name": "on", "devices": [dev["id"]], "shabbat": True,
                                  "trigger": {"type": "shabbat_start", "offset": -30},
                                  "action": {"power": True, "mode": "cool", "temp": 23}})
            client.send("127.0.0.1", self.port, "off")
            settings = dict(hub.store.settings(), **JLM)
            hub.enforce_shabbat([rule], datetime(2026, 10, 30, 21, tzinfo=TZ), settings, TZ)
            self.assertEqual(self.ac.state["run"], 1)
            self.assertEqual(self.ac.state["temp"], 23)

    def test_discovery(self):
        found = client.discover(["127.0.0.1/32"], port=self.port)
        self.assertEqual([f["host"] for f in found], ["127.0.0.1"])


if __name__ == "__main__":
    unittest.main()
