"""OpenAI-kompatible Schicht: laya vorn, Ollama als Rückfallebene.

Home Assistant spricht seine Conversation Agents über `/v1/chat/completions` an und
schickt dabei seine Intent-Tools (`HassTurnOn`, `HassLightSet`, ...) im `tools`-Feld
mit. Diese Schicht beantwortet einen Befehl mit einem `tool_calls`-Block, wenn laya
sicher genug ist -- sonst geht die unveränderte Anfrage an Ollama.

Bewusst defensiv: es wird nur ein Tool aufgerufen, das der Client auch angeboten hat,
und nur mit Parametern, die in dessen JSON-Schema stehen. So überlebt das Mapping
Versionsunterschiede in Home Assistant, statt an einem umbenannten Feld zu zerbrechen.
"""
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import schema

# laya-Aktion -> Kandidaten-Tools in absteigender Präferenz.
# Mehrere Namen, weil Home Assistant sie über die Versionen umbenannt hat.
ACTION_TOOLS: Dict[str, List[str]] = {
    "an":       ["HassTurnOn"],
    "aus":      ["HassTurnOff"],
    "hoch":     ["HassTurnOn"],
    "runter":   ["HassTurnOff"],
    "heller":   ["HassLightSet", "HassTurnOn"],
    "wärmer":   ["HassClimateSetTemperature", "HassSetTemperature", "HassTurnOn"],
    "frage":    ["HassGetState", "GetLiveContext"],
    "wechseln": [],          # kein Intent-Gegenstück -> Rückfallebene
}


def last_user_message(messages: List[Dict[str, Any]]) -> str:
    """Letzte Nutzeräußerung; Inhalt kann String oder Content-Part-Liste sein."""
    for m in reversed(messages or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c.strip()
        if isinstance(c, list):
            parts = [p.get("text", "") for p in c if isinstance(p, dict)]
            return " ".join(t for t in parts if t).strip()
    return ""


def ends_with_tool_result(messages: List[Dict[str, Any]]) -> bool:
    """Schickt der Client ein Tool-Ergebnis zurück und erwartet nur noch Text?"""
    for m in reversed(messages or []):
        role = m.get("role")
        if role == "tool":
            return True
        if role in ("user", "system"):
            return False
    return False


# Wieviel Text pro Kontexteintrag hoechstens mitgeht. laya hat fuer den State rund
# 320 Token (max_len 512 minus Fragenkopf); lange Saetze wuerden den Befehl selbst
# aus dem Fenster schieben, und der steht am Anfang.
CONTEXT_CHARS = 120


def history(messages: List[Dict[str, Any]], limit: int) -> List[Dict[str, str]]:
    """Die letzten `limit` Aeusserungen VOR der aktuellen, aelteste zuerst.

    Gezaehlt wird in Einträgen, nicht in Runden: eine Nutzeraeusserung und die
    Antwort darauf sind zwei. Tool- und System-Nachrichten bleiben draussen -- die
    sind fuer die Geraetewahl Rauschen und kosten nur Token.
    """
    if limit <= 0:
        return []
    msgs = list(messages or [])
    for i in range(len(msgs) - 1, -1, -1):          # die aktuelle Aeusserung selbst
        if msgs[i].get("role") == "user":           # gehoert nicht in den Kontext
            msgs = msgs[:i]
            break
    out: List[Dict[str, str]] = []
    for m in reversed(msgs):
        if len(out) >= limit:
            break
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(part.get("text", "") for part in content
                               if isinstance(part, dict))
        text = (content or "").strip()
        if not text:
            continue                                 # z. B. reine tool_calls-Antworten
        out.append({"rolle": "nutzer" if role == "user" else "assistent",
                    "text": text[:CONTEXT_CHARS]})
    return list(reversed(out))


# Tools, deren Ergebnis keine Formulierung braucht: geschaltet ist geschaltet.
# Alles andere (HassGetState, GetLiveContext, ...) liefert Inhalt, der vorgelesen
# werden will -- dort ist ein festes "Erledigt." die falsche Antwort.
SWITCHING_TOOLS = {"HassTurnOn", "HassTurnOff", "HassLightSet",
                   "HassClimateSetTemperature", "HassSetTemperature"}


def answered_tools(messages: List[Dict[str, Any]]) -> List[str]:
    """Basisnamen der Tools, deren Ergebnis am Ende der Unterhaltung steht."""
    for m in reversed(messages or []):
        calls = m.get("tool_calls")
        if m.get("role") == "assistant" and calls:
            return [(c.get("function") or {}).get("name", "").rsplit("__", 1)[-1]
                    for c in calls]
        if m.get("role") == "user":
            break
    return []


def result_needs_words(messages: List[Dict[str, Any]]) -> bool:
    """Muss das Tool-Ergebnis vorgelesen werden, oder reicht eine Quittung?"""
    names = answered_tools(messages)
    if not names:
        return True                    # unbekannt -> lieber formulieren lassen
    return any(n not in SWITCHING_TOOLS for n in names)


def declared_tools(body: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Name -> Funktionsdefinition aus dem `tools`-Feld der Anfrage."""
    out: Dict[str, Dict[str, Any]] = {}
    for t in body.get("tools") or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if t.get("type") == "function" else t
        if isinstance(fn, dict) and fn.get("name"):
            out[fn["name"]] = fn
    return out


def resolve_tool(tools: Dict[str, Dict[str, Any]], base: str) -> Optional[str]:
    """Findet den tatsächlichen Tool-Namen zu einem Basisnamen.

    Home Assistant meldet seine Intents mit Komponentenpräfix an
    (`intent__HassTurnOn`, `light__HassLightSet`). Ein exakter Vergleich geht
    deshalb immer daneben -- verglichen wird der Teil hinter dem letzten `__`.
    """
    if base in tools:
        return base
    for name in tools:
        if name.rsplit("__", 1)[-1] == base:
            return name
    return None


def _coerce(value: Any, prop: Dict[str, Any]) -> Any:
    """Passt einen Wert an den im Schema deklarierten Typ an.

    Nötig, weil dieselben Felder je nach Tool anders typisiert sind: `domain` ist
    bei HassTurnOn ein Array, anderswo ein String.
    """
    t = (prop or {}).get("type")
    if t == "array":
        return value if isinstance(value, list) else [value]
    if t == "integer" and isinstance(value, (int, float)):
        return int(round(value))
    if t == "number" and isinstance(value, (int, float)):
        return float(value)
    if t == "string" and not isinstance(value, str):
        return str(value)
    return value


def _schema_properties(fn: Dict[str, Any]) -> Tuple[set, set]:
    params = fn.get("parameters") or {}
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    return set(props.keys()), required


def fit_arguments(fn: Dict[str, Any], candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Beschneidet Argumente auf das, was das Tool-Schema kennt.

    Gibt None zurück, wenn ein Pflichtfeld danach fehlen würde -- dann ist das Tool
    für diesen Befehl nicht benutzbar und die Rückfallebene übernimmt.
    """
    allowed, required = _schema_properties(fn)
    props = (fn.get("parameters") or {}).get("properties") or {}
    if not allowed:                      # Tool ohne Schema: Argumente unverändert
        args = {k: v for k, v in candidate.items() if v is not None}
    else:
        args = {k: _coerce(v, props.get(k))
                for k, v in candidate.items() if k in allowed and v is not None}
    if required - set(args):
        return None
    return args


def build_tool_call(answers: Dict[str, Any], toolcall: Dict[str, Any],
                    entities: Dict[str, Dict[str, str]], areas: Dict[str, str],
                    tools: Dict[str, Dict[str, Any]], threshold: float,
                    text: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
    """Baut einen OpenAI-Tool-Call aus laya-Antworten.

    Rückgabe: (tool_call oder None, Begründung). Die Begründung landet im Log und in
    der WebUI, damit nachvollziehbar bleibt, warum etwas an Ollama ging.
    """
    if not tools:
        return None, "Client hat keine Tools angeboten"

    kind = toolcall.get("kind")
    if kind not in ("service_call", "query"):
        return None, "laya liefert keinen ausführbaren Call (%s)" % kind

    conf = toolcall.get("confidence")
    if conf is not None and conf < threshold:
        return None, "Confidence %.2f unter Schwelle %.2f" % (conf, threshold)

    action = answers["action"]["choice"]
    names = ACTION_TOOLS.get(action) or []
    if not names:
        return None, "Aktion %r hat kein Intent-Gegenstück" % action

    device_key = answers["device"]["choice"]
    entity = entities.get(device_key)
    area_key = answers["area"]["choice"]
    area_conf = answers["area"]["confidence"]

    candidate: Dict[str, Any] = {}
    if entity is not None and _area_statt_geraet(entity, area_key, area_conf,
                                                 threshold, text, areas):
        # Die Geraetefrage verwechselt aehnlich benannte Geraete quer durch die
        # Wohnung, die Bereichsfrage liegt dabei bei 1,00. Nennt der Befehl klar
        # einen Raum und liegt das gewaehlte Geraet woanders, zielen wir auf
        # "dieser Typ in diesem Raum" -- das loest Home Assistant selbst auf.
        candidate["area"] = _area_label(area_key, areas)
        candidate["domain"] = entity.get("domain")
    elif entity is not None:
        # Nur der Name. Home Assistant sucht die Schnittmenge aus allen Angaben:
        # ein falsch geratener Bereich laesst den Treffer ins Leere laufen, obwohl
        # der Name allein das Geraet eindeutig benennt. Der Bereich kommt nur zum
        # Zug, wenn gar kein Geraet bestimmt werden konnte.
        # `assist_name` ist der Name, unter dem Home Assistant die Entitaet beim
        # Katalogaufbau tatsaechlich gefunden hat -- meist ein Alias. Der
        # Anzeigename ist nicht immer ansprechbar: bei doppelten Namen ist er
        # mehrdeutig, und manche kennt der Intent-Matcher gar nicht. Dann laeuft
        # der Tool-Call ins Leere, Home Assistant meldet trotzdem "ausgefuehrt".
        candidate["name"] = (entity.get("assist_name") or entity.get("name")
                             or entity["entity_id"])
        candidate["domain"] = entity.get("domain")
    elif area_key and area_key != "unklar":
        candidate["area"] = _area_label(area_key, areas)
    else:
        return None, "kein Gerät und kein Bereich bestimmbar"

    data = toolcall.get("data") or {}
    if "brightness_pct" in data:
        candidate["brightness"] = data["brightness_pct"]
    if "temperature" in data:
        candidate["temperature"] = data["temperature"]

    for base in names:
        actual = resolve_tool(tools, base)
        if actual is None:
            continue
        args = fit_arguments(tools[actual], candidate)
        if args is None:
            continue
        return {
            "id": "call_" + uuid.uuid4().hex[:20],
            "type": "function",
            "function": {"name": actual,
                         "arguments": json.dumps(args, ensure_ascii=False)},
        }, "laya -> %s%s" % (actual, " (über den Raum)" if "area" in args else "")

    return None, ("keines der Tools %s wurde angeboten (vorhanden: %s)"
                  % (names, ", ".join(sorted(tools)[:6]) or "keine"))


def _area_statt_geraet(entity: Dict[str, Any], area_key: str, area_conf: float,
                       threshold: float, text: str, areas: Dict[str, str]) -> bool:
    """Widerspricht das gewaehlte Geraet dem sicher erkannten Raum?

    Nur wenn der Raum im Befehl auch vorkommt -- die Bereichsfrage antwortet
    sonst auch ohne Anhaltspunkt mit voller Confidence (schema.area_named).
    """
    if not area_key or area_key == "unklar" or area_conf < threshold:
        return False
    if not schema.area_named(text, area_key, areas):
        return False
    eigen = entity.get("area") or ""
    return bool(eigen) and eigen != area_key


def _area_label(key: str, areas: Dict[str, str]) -> str:
    """Bereichsschlüssel in die Schreibweise bringen, die HA als Bereichsnamen kennt.

    Beim Katalog aus Home Assistant steht in `areas` bereits der echte Bereichsname;
    nur beim handgepflegten Katalog muss aus dem Schlüssel einer gebaut werden.
    """
    if not key:
        return ""
    label = areas.get(key)
    if label and "," not in label and not label.lower().startswith("bereich "):
        return label
    return key.replace("_", " ").title()


# ------------------------------------------------------------------ Antwortformate

def completion(model: str, content: Optional[str] = None,
               tool_calls: Optional[List[Dict[str, Any]]] = None,
               extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
        msg["content"] = content       # darf None sein, wenn nur Tools kommen
    out = {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": msg,
                     "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    if extra:
        out["laya"] = extra
    return out


def to_sse(payload: Dict[str, Any]) -> bytes:
    """Nicht-gestreamte Antwort als einzelnen SSE-Block ausliefern.

    Reicht für Clients, die `stream: true` setzen: sie bekommen einen Delta-Chunk mit
    dem vollständigen Inhalt und danach [DONE]. Echtes Token-Streaming gibt es hier
    nicht -- bei ~200 ms Antwortzeit bringt es auch nichts.
    """
    choice = payload["choices"][0]
    msg = choice["message"]
    delta: Dict[str, Any] = {"role": "assistant"}
    if msg.get("content") is not None:
        delta["content"] = msg["content"]
    if msg.get("tool_calls"):
        delta["tool_calls"] = [
            {"index": i, **tc} for i, tc in enumerate(msg["tool_calls"])]
    head = {"id": payload["id"], "object": "chat.completion.chunk",
            "created": payload["created"], "model": payload["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    tail = {"id": payload["id"], "object": "chat.completion.chunk",
            "created": payload["created"], "model": payload["model"],
            "choices": [{"index": 0, "delta": {},
                         "finish_reason": choice["finish_reason"]}]}
    lines = [b"data: " + json.dumps(head, ensure_ascii=False).encode(), b"\n\n",
             b"data: " + json.dumps(tail, ensure_ascii=False).encode(), b"\n\n",
             b"data: [DONE]\n\n"]
    return b"".join(lines)
