#!/usr/bin/env python3
"""Simulated AEH-W4A1 AC, for trying the app without real units.

    python3 tools/fake_ac.py --hosts 127.0.0.2,127.0.0.3     # Linux: any 127.x works
    python3 tools/fake_ac.py --host 127.0.0.1 --ports 9001,9002
"""

import argparse
import os
import socketserver
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from acctl import protocol  # noqa: E402


class FakeAC:
    def __init__(self, room_temp=27):
        self.lock = threading.Lock()
        self.state = {"run": 0, "mode": 2, "wind": 0, "sleep": 0, "temp": 24, "room": room_temp,
                      "up_down": 0, "left_right": 0, "eco": 0, "turbo": 0, "display": 1}
        self.received = []

    def apply(self, payload):
        s = self.state
        b = payload
        if b[3] & 0x04:
            s["run"] = 1 if b[3] & 0x08 else 0
        if b[3] & 0x10:
            s["mode"] = (b[3] >> 4) >> 1
        if b[1] & 1:
            s["wind"] = b[1] >> 1
        if b[2] & 1:
            s["sleep"] = b[2] >> 1
        if b[4] & 1:
            s["temp"] = b[4] >> 1
        if b[17] & 0x40:
            s["up_down"] = 1 if b[17] & 0x80 else 0
        if b[17] & 0x10:
            s["left_right"] = 1 if b[17] & 0x20 else 0
        if b[18] & 0x40:
            s["turbo"] = 1 if b[18] & 0x08 else 0
        if b[18] & 0x10:
            s["eco"] = 1 if b[18] & 0x20 else 0
        if b[21] & 0x40:
            s["display"] = 1 if b[21] & 0x80 else 0

    def status_frame(self):
        bits = ["0"] * (61 * 8)

        def put(name, value):
            start, length = protocol.STATUS_FIELDS[name]
            bits[start - 1:start - 1 + length] = list(format(value, "0%db" % length))

        s = self.state
        put("wind_status", s["wind"])
        put("sleep_status", s["sleep"])
        put("mode_status", s["mode"])
        put("run_status", s["run"])
        put("indoor_temperature_setting", s["temp"])
        put("indoor_temperature_status", s["room"])
        put("up_down", s["up_down"])
        put("left_right", s["left_right"])
        put("low_power", s["eco"])
        put("efficient", s["turbo"])
        put("display_led", s["display"])
        put("outdoor_temperature", 31)
        data = bytes(int("".join(bits[i:i + 8]), 2) for i in range(0, len(bits), 8))
        body = bytearray([0x01, 0x40, 0, 0x01, 0x00, 0xFE, 0x01, 0x01, 0x01, 0x01, 0x00, 0x66, 0x00, 0x01]) + data
        body[2] = len(body) - 3
        checksum = sum(body) & 0xFFFF
        return protocol.START + protocol.escape(bytes(body) + checksum.to_bytes(2, "big")) + protocol.END

    def ack_frame(self):
        body = bytearray([0x01, 0x40, 0, 0x01, 0x00, 0xFE, 0x01, 0x01, 0x01, 0x01, 0x00, 0x65, 0x00, 0x01])
        body[2] = len(body) - 3
        checksum = sum(body) & 0xFFFF
        return protocol.START + protocol.escape(bytes(body) + checksum.to_bytes(2, "big")) + protocol.END

    def handle(self, data):
        if data.startswith(b"AT+XMV"):
            return b"+XMV:4.4.6\r\n"
        cmd, sub, payload, ok = protocol.frame_body(data)
        with self.lock:
            self.received.append((cmd, sub, bytes(payload)))
            if cmd == protocol.CMD_STATUS:
                return self.status_frame()
            if cmd == protocol.CMD_UPDATE:
                self.apply(payload)
                return self.ack_frame()
        return b""


def serve(ac, host, port):
    class H(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            data = b""
            try:
                while True:
                    chunk = self.request.recv(256)
                    if not chunk:
                        return
                    data += chunk
                    if data.startswith(b"AT+XMV") or (data.startswith(protocol.START) and data.endswith(protocol.END)):
                        break
                self.request.sendall(ac.handle(data))
            except OSError:
                pass

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = S((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hosts", default="127.0.0.1")
    p.add_argument("--ports", default=str(protocol.PORT))
    a = p.parse_args()
    servers = []
    for host in a.hosts.split(","):
        for port in a.ports.split(","):
            servers.append(serve(FakeAC(), host.strip(), int(port)))
            print("fake AC on %s:%s" % (host.strip(), port))
    threading.Event().wait()


if __name__ == "__main__":
    main()
