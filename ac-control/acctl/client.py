"""Talking to the ACs over the local network.

The Wi-Fi module is slow and handles one connection at a time, so every
exchange opens a short TCP connection, is serialized per AC with a lock and is
retried a few times before giving up.
"""

import ipaddress
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import protocol

CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 3.0
RETRIES = 4
COMMAND_GAP = 0.35  # seconds between consecutive commands to the same AC

_locks = {}
_locks_guard = threading.Lock()


class ACError(Exception):
    pass


def _lock_for(host, port):
    with _locks_guard:
        return _locks.setdefault((host, port), threading.Lock())


def _exchange_once(host, port, frame):
    with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT) as sock:
        sock.settimeout(READ_TIMEOUT)
        sock.sendall(frame)
        return protocol.read_frame(sock.recv)


def exchange(host, port, frame, retries=RETRIES):
    last = None
    with _lock_for(host, port):
        for attempt in range(retries):
            try:
                answer = _exchange_once(host, port, frame)
                time.sleep(COMMAND_GAP)
                return answer
            except (OSError, protocol.ProtocolError) as exc:
                last = exc
                time.sleep(0.4 * (attempt + 1))
    raise ACError("המזגן %s לא עונה (%s)" % (host, last))


def probe(host, port=protocol.PORT, timeout=1.0):
    """Return the XM firmware version if an AEH-W4A1 module answers on host."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout + 1)
            sock.sendall(b"AT+XMV")
            data = b""
            while b"\n" not in data and len(data) < 128:
                chunk = sock.recv(128)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return None
    if b"+XMV:" not in data:
        return None
    return data.split(b"+XMV:", 1)[1].strip().decode("ascii", "replace")


def get_status(host, port=protocol.PORT):
    answer = exchange(host, port, protocol.status_request())
    try:
        return protocol.parse_status(answer)
    except protocol.ProtocolError as exc:
        raise ACError("תשובה לא תקינה מהמזגן %s: %s" % (host, exc))


def send(host, port, name):
    exchange(host, port, protocol.update_frame(name))


def commands_for(current, wanted):
    """Translate a wanted state into the list of protocol commands to send.

    Only fields present in `wanted` are touched; fields already matching the
    current state (when it is known) are skipped.
    """
    cur = current or {}
    cmds = []

    def differs(key):
        return key in wanted and wanted[key] is not None and cur.get(key) != wanted[key]

    if wanted.get("power") is False:
        if cur.get("power") is not False:
            cmds.append("off")
        return cmds

    turning_on = wanted.get("power") is True and cur.get("power") is not True
    if turning_on:
        cmds.append("on")
    if differs("mode") or (turning_on and wanted.get("mode")):
        if wanted["mode"] not in ("cool", "heat", "fan", "dry"):
            raise ACError("מצב לא נתמך: %s" % wanted["mode"])
        cmds.append("mode_" + wanted["mode"])
        # The mode frames carry a fixed temperature and fan speed, so resend
        # the wanted (or previous) ones after a mode change.
        if wanted.get("temp") is None and cur.get("target_temp"):
            wanted = dict(wanted, temp=cur["target_temp"])
        if wanted.get("fan") is None and cur.get("fan"):
            wanted = dict(wanted, fan=cur["fan"])
        cur = dict(cur, target_temp=None, fan=None)
    if wanted.get("temp") is not None and cur.get("target_temp") != wanted["temp"]:
        temp = int(round(float(wanted["temp"])))
        temp = max(protocol.TEMP_MIN, min(protocol.TEMP_MAX, temp))
        cmds.append("temp_%d_C" % temp)
    if differs("fan"):
        if wanted["fan"] not in ("auto", "mute", "low", "med", "max"):
            raise ACError("מהירות מאוורר לא נתמכת: %s" % wanted["fan"])
        cmds.append("speed_" + wanted["fan"])
    if differs("swing_v"):
        cmds.append("vert_swing" if wanted["swing_v"] else "vert_dir")
    if differs("swing_h"):
        cmds.append("hor_swing" if wanted["swing_h"] else "hor_dir")
    if differs("turbo"):
        cmds.append("turbo_on" if wanted["turbo"] else "turbo_off")
    if differs("eco"):
        cmds.append("energysave_on" if wanted["eco"] else "energysave_off")
    if differs("sleep"):
        level = int(wanted["sleep"])
        cmds.append("sleep_%d" % level if 1 <= level <= 4 else "sleep_off")
    if differs("display"):
        cmds.append("display_on" if wanted["display"] else "display_off")
    return cmds


def apply_state(host, port, wanted, current=None):
    """Bring the AC to `wanted`. Returns the fresh status read afterwards."""
    if current is None:
        try:
            current = get_status(host, port)
        except ACError:
            current = None  # still try to send the commands blindly
    for name in commands_for(current, wanted):
        send(host, port, name)
    return get_status(host, port)


def local_networks():
    """IPv4 /24 networks of this machine (best effort, no dependencies)."""
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    # The address used to reach the internet is the most reliable one.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    nets = []
    for addr in addrs:
        if addr.startswith("127."):
            continue
        net = ipaddress.IPv4Network(addr + "/24", strict=False)
        if net not in nets:
            nets.append(net)
    return nets


def discover(networks=None, port=protocol.PORT, timeout=0.8, workers=96):
    """Scan the given networks (default: this machine's /24) for ACs."""
    if networks is None:
        networks = local_networks()
    hosts = [str(h) for net in networks for h in ipaddress.IPv4Network(net, strict=False).hosts()]
    found = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for host, version in zip(hosts, pool.map(lambda h: probe(h, port, timeout), hosts)):
            if version is not None:
                found.append({"host": host, "port": port, "version": version})
    return found
