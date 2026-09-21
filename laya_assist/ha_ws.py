"""Home-Assistant-WebSocket-Client -- gerade so viel, wie fuer Assist noetig ist.

Die Assist-Freigabe einer Entitaet steht nicht in der REST-API: sie liegt in
`.storage/homeassistant.exposed_entities` und ist nur ueber die WebSocket-API
abfragbar. Dasselbe gilt fuer die Sprach-Aliase -- `config/entity_registry/list`
liefert sie nicht mit, `config/entity_registry/get_entries` schon.

Darum hier ein minimaler RFC-6455-Client auf stdlib-Basis: keine zusaetzliche
Abhaengigkeit, und mehr als "verbinden, drei Kommandos, schliessen" braucht es
nicht. Der Token muss von einem Admin-Benutzer stammen, sonst verweigert HA die
Registry-Kommandos.
"""
import base64
import json
import os
import socket
import ssl
import struct
from typing import Any, Dict, List, Optional, Tuple

TIMEOUT = 15.0
MAX_FRAME = 32 * 1024 * 1024  # Registry-Antworten sind gross, aber nicht beliebig


class HAWebSocketError(RuntimeError):
    pass


def _split_url(base_url: str) -> Tuple[str, int, bool, str]:
    """http://host:8123/pfad -> (host, port, tls, pfad)"""
    url = base_url.strip().rstrip("/")
    scheme, _, rest = url.partition("://")
    if not rest:
        scheme, rest = "http", url
    tls = scheme in ("https", "wss")
    host, _, path = rest.partition("/")
    port = 443 if tls else 80
    if host.startswith("["):                      # IPv6-Literal
        addr, _, tail = host[1:].partition("]")
        host = addr
        if tail.startswith(":"):
            port = int(tail[1:])
    elif ":" in host:
        host, _, p = host.rpartition(":")
        port = int(p)
    return host, port, tls, ("/" + path if path else "")


class _Socket:
    """Ein WebSocket, so weit ausprogrammiert wie hier gebraucht: Text-Frames,
    Client-Maskierung, kein Continuation-Handling (HA sendet ganze Frames)."""

    def __init__(self, base_url: str):
        host, port, tls, prefix = _split_url(base_url)
        raw = socket.create_connection((host, port), timeout=TIMEOUT)
        if tls:
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)
        self.sock = raw
        self.buf = bytearray()
        self._handshake(host, port, tls, prefix)

    def _handshake(self, host: str, port: int, tls: bool, prefix: str):
        hostport = host if port in (80, 443) else "%s:%d" % (host, port)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s/api/websocket HTTP/1.1\r\n"
               "Host: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
               % (prefix, hostport, key))
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise HAWebSocketError("Verbindung beim Handshake abgebrochen")
            head += chunk
        header, _, rest = head.partition(b"\r\n\r\n")
        status = header.split(b"\r\n", 1)[0].decode("latin-1")
        if " 101" not in status:
            raise HAWebSocketError("WebSocket-Upgrade abgelehnt: %s" % status)
        self.buf.extend(rest)

    # ---- Frames

    def send(self, obj: dict):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        out = bytearray([0x81])
        if n < 126:
            out.append(0x80 | n)
        elif n < 65536:
            out.append(0x80 | 126)
            out += struct.pack(">H", n)
        else:
            out.append(0x80 | 127)
            out += struct.pack(">Q", n)
        out += mask
        out += bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(out))

    def _need(self, n: int):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise HAWebSocketError("Verbindung vorzeitig geschlossen")
            self.buf.extend(chunk)

    def recv(self) -> dict:
        while True:
            self._need(2)
            opcode = self.buf[0] & 0x0F
            length = self.buf[1] & 0x7F
            offset = 2
            if length == 126:
                self._need(4)
                length = struct.unpack(">H", bytes(self.buf[2:4]))[0]
                offset = 4
            elif length == 127:
                self._need(10)
                length = struct.unpack(">Q", bytes(self.buf[2:10]))[0]
                offset = 10
            if length > MAX_FRAME:
                raise HAWebSocketError("Frame zu gross (%d Bytes)" % length)
            self._need(offset + length)
            payload = bytes(self.buf[offset:offset + length])
            del self.buf[:offset + length]
            if opcode == 0x1:
                return json.loads(payload)
            if opcode == 0x9:                      # Ping -> Pong
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
            elif opcode == 0x8:
                raise HAWebSocketError("Verbindung von HA geschlossen")

    def close(self):
        try:
            self.sock.sendall(b"\x88\x80" + os.urandom(4))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class _Session:
    def __init__(self, base_url: str, token: str):
        self.ws = _Socket(base_url)
        self._id = 0
        hello = self.ws.recv()
        if hello.get("type") != "auth_required":
            raise HAWebSocketError("unerwarteter Handshake: %r" % hello.get("type"))
        self.ws.send({"type": "auth", "access_token": token})
        ack = self.ws.recv()
        if ack.get("type") != "auth_ok":
            raise HAWebSocketError("Anmeldung abgelehnt: %s"
                                   % ack.get("message", ack.get("type")))
        self.version = ack.get("ha_version", "")

    def cmd(self, **kwargs) -> Any:
        self._id += 1
        mid = self._id
        self.ws.send(dict(id=mid, **kwargs))
        while True:
            msg = self.ws.recv()
            if msg.get("id") != mid or msg.get("type") != "result":
                continue                            # Events anderer Abos ignorieren
            if not msg.get("success"):
                err = msg.get("error") or {}
                raise HAWebSocketError("%s: %s" % (kwargs.get("type"),
                                                   err.get("message") or err.get("code")))
            return msg.get("result")

    def close(self):
        self.ws.close()


def _clean(values) -> List[str]:
    """Aliase saeubern: HA laesst leere Eintraege zu, die hier nur stoeren."""
    out = []
    for v in values or []:
        v = (v or "").strip() if isinstance(v, str) else ""
        if v and v not in out:
            out.append(v)
    return out


def fetch_assist_entities(base_url: str, token: str,
                          assistant: str = "conversation"
                          ) -> Dict[str, Dict[str, Any]]:
    """Die fuer Assist freigegebenen Entitaeten samt Aliasen.

    Rueckgabe: entity_id -> {"aliases": [...], "name": <Registry-Name oder "">}.
    Wirft HAWebSocketError, wenn HA nicht erreichbar ist oder der Token die
    Registry-Kommandos nicht darf.
    """
    ses = _Session(base_url, token)
    try:
        exposed = (ses.cmd(type="homeassistant/expose_entity/list") or {}
                   ).get("exposed_entities") or {}
        ids = [eid for eid, flags in exposed.items()
               if (flags or {}).get(assistant)]
        if not ids:
            return {}
        entries = ses.cmd(type="config/entity_registry/get_entries",
                          entity_ids=ids) or {}
    finally:
        ses.close()

    out: Dict[str, Dict[str, Any]] = {}
    for eid in ids:
        entry = entries.get(eid) or {}
        out[eid] = {
            "aliases": _clean(entry.get("aliases")),
            "name": (entry.get("name") or "").strip(),
        }
    return out
