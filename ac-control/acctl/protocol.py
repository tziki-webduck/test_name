"""XM protocol used by the Hisense AEH-W4A1 Wi-Fi module (Hisense / Tornado ACs).

The module listens on TCP port 8888. Frames look like:

    F4 F5 | 00 40 LEN 00 00 01 01 FE 01 00 00 CMD SUB PAYLOAD... | CHK_HI CHK_LO | F4 FB

LEN counts the bytes after itself up to the checksum, the checksum is the 16-bit
sum of everything between the F5 and the checksum, and any 0xF4 byte inside the
frame is escaped as F4 F4.

The update commands below were captured from the official app (via the
pyaehw4a1 project) and are reproduced byte for byte; tests/test_protocol.py
checks them against the original frames.
"""

START = b"\xF4\xF5"
END = b"\xF4\xFB"
PORT = 8888

CMD_UPDATE = 0x65  # 101
CMD_STATUS = 0x66  # 102

# Update command payload: 31 bytes, byte 8 is always 0x04 in the captures.
_BASE_UPDATE = {8: 0x04}

# Each update command is a set of payload bytes that differ from the base.
UPDATE_PATCHES = {
    "on": {3: 0x0C},
    "off": {2: 0x01, 3: 0x04, 9: 0x01, 14: 0x01, 17: 0x01, 18: 0x55, 20: 0x01},
    "mode_cool": {1: 0x01, 2: 0x01, 3: 0x50, 4: 0x35, 16: 0x10, 17: 0x01, 18: 0x04, 20: 0x10},
    "mode_heat": {1: 0x01, 2: 0x01, 3: 0x30, 4: 0x2F, 16: 0x10, 17: 0x01, 18: 0x04, 20: 0x10},
    "mode_fan": {1: 0x07, 2: 0x01, 3: 0x10, 4: 0x33, 16: 0x10, 17: 0x01, 18: 0x04, 20: 0x10},
    "mode_dry": {1: 0x01, 2: 0x01, 3: 0x70, 4: 0x33, 16: 0x10, 17: 0x01, 18: 0x04, 20: 0x10},
    "speed_auto": {1: 0x01, 2: 0x01, 18: 0x04, 20: 0x10},
    "speed_mute": {1: 0x03, 2: 0x01, 18: 0x04, 20: 0x30},
    "speed_low": {1: 0x05, 2: 0x01, 18: 0x04, 20: 0x10},
    "speed_med": {1: 0x07, 2: 0x01, 18: 0x04, 20: 0x10},
    "speed_max": {1: 0x09, 2: 0x01, 18: 0x04, 20: 0x10},
    "turbo_on": {1: 0x01, 2: 0x01, 18: 0x5C, 20: 0x10},
    "turbo_off": {1: 0x01, 2: 0x01, 18: 0x54, 20: 0x10},
    "energysave_on": {18: 0x34},
    "energysave_off": {18: 0x14},
    "display_on": {21: 0xC0},
    "display_off": {21: 0x40},
    "sleep_off": {1: 0x01, 2: 0x01, 18: 0x04, 20: 0x10},
    "sleep_1": {1: 0x05, 2: 0x03, 18: 0x04, 20: 0x10},
    "sleep_2": {1: 0x05, 2: 0x05, 18: 0x04, 20: 0x10},
    "sleep_3": {1: 0x05, 2: 0x07, 18: 0x04, 20: 0x10},
    "sleep_4": {1: 0x05, 2: 0x09, 18: 0x04, 20: 0x10},
    "vert_dir": {16: 0x01, 17: 0x40},
    "vert_swing": {16: 0x01, 17: 0xC0},
    "hor_dir": {17: 0x10, 22: 0x14},
    "hor_swing": {17: 0x30, 22: 0x14},
}
for _t in range(16, 33):
    UPDATE_PATCHES["temp_%d_C" % _t] = {4: _t * 2 + 1}

TEMP_MIN, TEMP_MAX = 16, 32

MODES = {0: "fan", 1: "heat", 2: "cool", 3: "dry", 4: "auto"}
FAN_SPEEDS = {0: "auto", 1: "mute", 2: "low", 3: "med", 4: "max"}

# (start bit, length) inside the status 102/0 data block, 1-based like the
# XM protocol header the offsets come from.
STATUS_FIELDS = {
    "wind_status": (1, 8),
    "sleep_status": (9, 7),
    "mode_status": (17, 4),
    "run_status": (21, 1),
    "indoor_temperature_setting": (25, 8),
    "indoor_temperature_status": (33, 8),
    "indoor_humidity_status": (57, 8),
    "temperature_fahrenheit": (87, 1),
    "up_down": (153, 1),
    "left_right": (154, 1),
    "low_power": (157, 1),
    "efficient": (159, 1),
    "display_led": (170, 1),
    "outdoor_temperature": (225, 8),
}


class ProtocolError(Exception):
    pass


def escape(data):
    return data.replace(b"\xF4", b"\xF4\xF4")


def unescape_frame(frame):
    """Strip the escaping from a full frame (start and end markers kept)."""
    if not frame.startswith(START):
        raise ProtocolError("frame does not start with F4F5: %s" % frame[:8].hex())
    end = frame.find(END, 2)
    while end != -1:
        # An F4 FB pair preceded by an odd number of F4 bytes belongs to the data.
        run = 0
        i = end - 1
        while i >= 2 and frame[i] == 0xF4:
            run += 1
            i -= 1
        if run % 2 == 0:
            break
        end = frame.find(END, end + 1)
    if end == -1:
        raise ProtocolError("frame is not terminated: %s" % frame.hex())
    inner = frame[2:end].replace(b"\xF4\xF4", b"\xF4")
    return START + inner + END


def build_frame(cmd, sub, payload):
    body = bytes([0x00, 0x40, 0]) + bytes([0x00, 0x00, 0x01, 0x01, 0xFE, 0x01, 0x00, 0x00, cmd, sub]) + bytes(payload)
    body = bytearray(body)
    body[2] = len(body) - 3
    checksum = sum(body) & 0xFFFF
    return START + escape(bytes(body) + checksum.to_bytes(2, "big")) + END


def update_frame(name):
    if name not in UPDATE_PATCHES:
        raise ProtocolError("unknown command %r" % name)
    payload = [0] * 31
    for idx, val in list(_BASE_UPDATE.items()) + list(UPDATE_PATCHES[name].items()):
        payload[idx] = val
    return build_frame(CMD_UPDATE, 0x00, payload)


def status_request():
    return build_frame(CMD_STATUS, 0x00, [0x00, 0x00])


def frame_body(frame):
    """Return (cmd, sub, data, checksum_ok) of a received frame."""
    frame = unescape_frame(frame)
    body = frame[2:-4]
    if len(body) < 14:
        raise ProtocolError("frame too short: %s" % frame.hex())
    checksum_ok = (sum(body) & 0xFFFF) == int.from_bytes(frame[-4:-2], "big")
    return body[11], body[12], body[13:], checksum_ok


def parse_status(frame):
    """Decode a 102/0 status answer into a friendly dict."""
    cmd, sub, data, _ = frame_body(frame)
    if (cmd, sub) != (CMD_STATUS, 0x00):
        raise ProtocolError("not a status answer: %d/%d" % (cmd, sub))
    # The answer data starts with a 0x01 result byte.
    data = data[1:]
    bits = "".join("{:08b}".format(b) for b in data)

    def field(name):
        start, length = STATUS_FIELDS[name]
        chunk = bits[start - 1:start - 1 + length]
        if len(chunk) < length:
            return None
        return int(chunk, 2)

    sleep = field("sleep_status") or 0
    outdoor = field("outdoor_temperature")
    humidity = field("indoor_humidity_status")
    return {
        "power": field("run_status") == 1,
        "mode": MODES.get(field("mode_status"), "unknown"),
        "fan": FAN_SPEEDS.get(field("wind_status"), "auto"),
        "target_temp": field("indoor_temperature_setting"),
        "room_temp": field("indoor_temperature_status"),
        "outdoor_temp": outdoor if outdoor and outdoor < 80 else None,
        "humidity": humidity if humidity and humidity <= 100 else None,
        "fahrenheit": field("temperature_fahrenheit") == 1,
        "swing_v": field("up_down") == 1,
        "swing_h": field("left_right") == 1,
        "eco": field("low_power") == 1,
        "turbo": field("efficient") == 1,
        "sleep": sleep if sleep <= 4 else 0,
        "display": field("display_led") == 1,
    }


def read_frame(recv):
    """Read one frame using recv(n) -> bytes. Handles partial TCP reads."""
    buf = b""
    while True:
        chunk = recv(256)
        if not chunk:
            break
        buf += chunk
        start = buf.find(START)
        if start == -1:
            continue
        try:
            unescape_frame(buf[start:])
        except ProtocolError:
            continue
        return buf[start:]
    raise ProtocolError("connection closed before a full frame arrived (%d bytes)" % len(buf))
