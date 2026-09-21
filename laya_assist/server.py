"""HTTP-Server: WebUI, JSON-API und der OpenAI-kompatible Endpunkt fuer Home Assistant.

Start:  laya-assist  [--port 8080] [--device mps|cpu] [--preload]
        python -m laya_assist  [...]
"""
import argparse
import json
import os
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from . import ha
from . import oai
from . import ollama
from . import schema as hschema
from . import settings

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CONFIG_PATH = os.path.join(settings.project_root(), "config.json")

# ------------------------------------------------------------------ Modellhalter


class Engine:
    """Haelt den laya-Router und misst jede Phase einzeln."""

    def __init__(self, device: Optional[str] = None, preload: bool = False,
                 preload_names: Optional[List[str]] = None):
        self.device = device
        # Metal vertraegt keine gleichzeitigen Forward-Passes aus mehreren Threads:
        # zwei parallele Anfragen reissen sonst den Prozess mit einer Assertion in
        # IOGPUMetalCommandBuffer ab. Alle Inferenz laeuft daher serialisiert.
        self.infer_lock = threading.Lock()
        # Checkpoints laden ist Minuten- und Gigabyte-Arbeit. Es bekommt ein
        # eigenes Schloss: zwei Anfragen sollen denselben Checkpoint nicht
        # doppelt laden, aber ein laufender Ladevorgang darf die Inferenz auf dem
        # bereits geladenen Modell nicht aufhalten.
        self.load_lock = threading.Lock()
        # Nur laden, was auch benutzt wird: jeder Checkpoint kostet Minuten beim
        # Start und mehrere GB im Speicher.
        self.preload_names = preload_names or ["multilingual"]
        self.lock = threading.Lock()
        self.router = None
        self.loading = False
        self.load_ms = None
        self.error = None
        self.laya_version = None
        self.torch_version = None
        if preload:
            self.ensure(preload_all=True)

    def ensure(self, preload_all: bool = False):
        with self.lock:
            if self.router is not None:
                return self.router
            self.loading = True
            t0 = time.perf_counter()
            import torch
            import laya
            from laya import Router
            self.laya_version = laya.__version__
            self.torch_version = torch.__version__
            self.router = Router(device=self.device)
            if preload_all:
                self.router.preload(self.preload_names)
            self.load_ms = (time.perf_counter() - t0) * 1000.0
            self.loading = False
            return self.router

    def info(self) -> Dict[str, Any]:
        out = {
            # Der Router existiert schon, waehrend preload() noch laeuft -- erst
            # beides zusammen heisst benutzbar.
            "loaded": self.router is not None and not self.loading,
            "loading": self.loading,
            "load_ms": round(self.load_ms, 1) if self.load_ms else None,
            "laya": self.laya_version,
            "torch": self.torch_version,
            "device_requested": self.device,
            "checkpoints": [],
            "device_actual": None,
        }
        if self.router is not None:
            out["checkpoints"] = list(self.router.loaded)
            for name in self.router.loaded:
                agent = self.router._agents.get(name)
                if agent is not None:
                    out["device_actual"] = str(agent.device)
                    break
        return out

    def predict(self, text: str, questions: Dict[str, Any],
                model: Optional[str] = None,
                context: Optional[Dict[str, Any]] = None,
                context_for: Optional[Any] = None,
                allow_load: bool = True) -> Dict[str, Any]:
        """Fragen beantworten -- optional mit Kontext, optional nur fuer manche Fragen.

        `context_for` grenzt ein, welche Fragen den Kontext sehen. Das ist keine
        Feinheit: die Geraetefrage braucht ihn ("mach ihn wieder aus"), die
        Aktionsfrage wird von ihm falsch gefuehrt, weil in der Vorgeschichte die
        umgekehrte Aktion steht. Beide Gruppen laufen als je ein Batch; die Zahl
        der Sequenzen bleibt gleich, nur der Aufruf passiert zweimal.

        `allow_load=False` verbietet, fuer diese Anfrage einen Checkpoint
        nachzuladen. Auf dem Sprachweg ist das Pflicht: dort wartet jemand vor
        einem Lautsprecher, und ein Ladevorgang dauert Minuten. Es wird dann auf
        einem bereits geladenen Modell geantwortet, vermerkt in `routing`.
        """
        router = self.ensure()
        # `command` steht bewusst zuerst: laya schneidet den State am Ende ab,
        # wenn er zu lang wird -- dann faellt Kontext weg, nicht der Befehl.
        state: Dict[str, Any] = {"command": text}
        state_ctx = dict(state)
        if context:
            state_ctx.update(context)

        if context and context_for is not None:
            wanted = set(context_for)
            groups = [({k: v for k, v in questions.items() if k in wanted}, state_ctx),
                      ({k: v for k, v in questions.items() if k not in wanted}, state)]
        elif context:
            groups = [(questions, state_ctx)]
        else:
            groups = [(questions, state)]
        groups = [(q, st) for q, st in groups if q]

        t0 = time.perf_counter()
        decision = router.route(state, questions, model=model)
        t_route = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        wanted = decision["model"]
        if not allow_load and wanted not in router.loaded:
            ersatz = self.preload_names[0] if self.preload_names[0] in router.loaded \
                else (list(router.loaded)[0] if router.loaded else wanted)
            decision = dict(decision, model=ersatz,
                            reason="%s (statt %r -- nicht geladen, und auf diesem "
                                   "Weg wird nicht nachgeladen)"
                                   % (decision.get("reason", ""), wanted))
        with self.load_lock:
            agent = router.load(decision["model"])
        t_load = (time.perf_counter() - t1) * 1000.0

        with self.infer_lock:
            t2 = time.perf_counter()
            result: Dict[str, Any] = {}
            for qs, st in groups:
                part = agent.system_one(st, qs)
                if not result:
                    result = part
                else:
                    result["answers"].update(part["answers"])
                    result["usage"]["input_tokens"] += part["usage"]["input_tokens"]
            t_infer = (time.perf_counter() - t2) * 1000.0

        # Reihenfolge der Fragen wiederherstellen -- die Oberflaeche zeigt sie so an.
        result["answers"] = {k: result["answers"][k] for k in questions
                             if k in result["answers"]}
        result["routing"] = dict(decision)
        result["timing"] = {
            "route_ms": round(t_route, 3),
            "load_ms": round(t_load, 3),
            "infer_ms": round(t_infer, 3),
            "total_ms": round(t_route + t_load + t_infer, 3),
        }
        result["device"] = str(agent.device)
        return result


ENGINE: Engine = None          # in main() gesetzt
# Gemessen auf 12 deutschen Befehlen: multilingual 12/12, english 7/12 (mit zwei
# Umkehrungen "ein"->turn_off). Der Router schickt kurze deutsche Befehle aber nach
# english, weil die Spracherkennung sie fuer Englisch haelt -- darum ein fester Default
# statt Routing. Mit "router" im Modell-Feld laesst sich das pro Request aufheben.
DEFAULT_MODEL = "multilingual"

STATE = {"ctx_entries": 2,
         "entities": dict(hschema.DEFAULT_ENTITIES),
         "areas": dict(hschema.DEFAULT_AREAS),
         "ha_url": "", "ha_token": "", "default_model": DEFAULT_MODEL,
         # Rueckfallebene: greift, wenn laya keinen sicheren Toolcall liefert
         "fb_enabled": False, "fb_url": "http://localhost:11434",
         "fb_model": "", "fb_threshold": 0.6, "fb_timeout": 120.0,
         "save_token": False}

# Letzte Entscheidungen der OpenAI-Route, damit in der WebUI sichtbar wird, was
# Home Assistant tatsaechlich geschickt bekommt und warum.
TRACE: List[Dict[str, Any]] = []

# Letzte /v1-Rohanfrage, damit nachvollziehbar ist, welche Tools der Client
# tatsaechlich anbietet -- Namen und Schemata unterscheiden sich je nach
# Home-Assistant-Integration.
LAST_REQUEST: Dict[str, Any] = {}

# Obergrenze fuer die Kontexteintraege. laya hat fuer den State rund 320 Token;
# mehr als acht kurze Aeusserungen passen da nicht sinnvoll hinein.
CONTEXT_MAX = 8


# ------------------------------------------------------------------ Konfiguration

# Der zuletzt geladene Dateiinhalt, damit beim Speichern nur die Abschnitte
# ueberschrieben werden, die die Oberflaeche wirklich verwaltet (server: bleibt
# so erhalten, auch wenn er zur Laufzeit nicht angefasst wird).
CONFIG: Dict[str, Any] = {}


def save_config():
    """Laufzeitzustand nach project.yaml schreiben, Token getrennt behandeln."""
    global CONFIG
    CONFIG = settings.from_state(STATE, CONFIG or settings.load())
    settings.save(CONFIG)
    settings.save_token(STATE.get("ha_token", ""), STATE.get("save_token", False))


def load_config():
    global CONFIG
    CONFIG = settings.load()
    STATE.update(settings.to_state(CONFIG))
    token = settings.load_token()
    if token:
        STATE["ha_token"] = token
    if not STATE["entities"]:
        STATE["entities"] = dict(hschema.DEFAULT_ENTITIES)
        STATE["areas"] = dict(hschema.DEFAULT_AREAS)
    print("  project.yaml: %d Entities, Modell %s, Fallback %s, Token %s"
          % (len(STATE["entities"]), STATE["default_model"],
             STATE["fb_model"] or "aus", "geladen" if token else "nicht gespeichert"))


# ------------------------------------------------------------------ Handler-Logik

def _questions() -> Dict[str, Any]:
    return hschema.build_questions(STATE["entities"], STATE["areas"])


def api_health(_: dict) -> dict:
    info = ENGINE.info()
    info["entities"] = len(STATE["entities"])
    info["areas"] = len(STATE["areas"])
    info["ha_configured"] = bool(STATE["ha_url"] and STATE["ha_token"])
    info["default_model"] = STATE["default_model"]
    info["fallback"] = {"enabled": STATE["fb_enabled"], "model": STATE["fb_model"]}
    info["ha_url"] = STATE["ha_url"]
    info["save_token"] = STATE["save_token"]
    info["ctx_entries"] = STATE["ctx_entries"]
    return info


def api_schema(_: dict) -> dict:
    return {"entities": STATE["entities"], "areas": STATE["areas"],
            "questions": _questions()}


def api_schema_set(body: dict) -> dict:
    ents = body.get("entities")
    areas = body.get("areas")
    if not isinstance(ents, dict) or not ents:
        raise ValueError("'entities' fehlt oder ist leer")
    for k, v in ents.items():
        if not isinstance(v, dict) or "entity_id" not in v or "domain" not in v:
            raise ValueError("Eintrag %r braucht mindestens entity_id und domain" % k)
        v.setdefault("area", "")
        v.setdefault("desc", v["entity_id"])
    STATE["entities"] = ents
    if isinstance(areas, dict):
        STATE["areas"] = areas
    save_config()
    return {"ok": True, "entities": len(ents), "areas": len(STATE["areas"])}


def api_predict(body: dict) -> dict:
    text = (body.get("text") or "").strip()
    if not text:
        raise ValueError("'text' fehlt")
    model = body.get("model") or STATE["default_model"] or None
    if model == "router":
        model = None
    threshold = float(body.get("threshold", 0.6))

    # Eigene Fragen erlauben: damit laesst sich das Schema durchprobieren, ohne den
    # Server neu zu starten (ein Neustart kostet ~3 min Modellladezeit). Ohne Override
    # wird der Toolcall gebaut, mit Override nur die rohen Antworten geliefert.
    custom = body.get("questions")
    questions = custom if isinstance(custom, dict) and custom else _questions()

    ctx = body.get("context") if isinstance(body.get("context"), dict) else None
    res = ENGINE.predict(text, questions, model=model, context=ctx,
                         context_for=body.get("context_for"))
    out = {"text": text, "answers": res["answers"], "routing": res["routing"],
           "timing": res["timing"], "usage": res["usage"], "device": res["device"]}
    if custom:
        out["toolcall"] = {"kind": "raw", "domain": None, "service": None,
                           "target": {}, "data": {},
                           "warnings": ["eigenes Frage-Schema -- kein Toolcall gebaut"],
                           "confidence": None}
    else:
        out["toolcall"] = hschema.build_toolcall(res["answers"], STATE["entities"], threshold)
    return out


def api_bench(body: dict) -> dict:
    texts: List[str] = body.get("texts") or []
    texts = [t.strip() for t in texts if t and t.strip()]
    if not texts:
        raise ValueError("'texts' ist leer")
    runs = max(1, min(200, int(body.get("runs", 20))))
    warmup = max(0, min(20, int(body.get("warmup", 3))))
    model = body.get("model") or STATE["default_model"] or None
    if model == "router":
        model = None
    questions = _questions()
    ENGINE.ensure()

    for i in range(warmup):
        ENGINE.predict(texts[i % len(texts)], questions, model=model)

    samples: List[float] = []
    infer: List[float] = []
    route: List[float] = []
    tokens: List[int] = []
    per_model: Dict[str, int] = {}
    t_start = time.perf_counter()
    for i in range(runs):
        r = ENGINE.predict(texts[i % len(texts)], questions, model=model)
        samples.append(r["timing"]["total_ms"])
        infer.append(r["timing"]["infer_ms"])
        route.append(r["timing"]["route_ms"])
        tokens.append(r["usage"]["input_tokens"])
        m = r["routing"]["model"]
        per_model[m] = per_model.get(m, 0) + 1
    wall = time.perf_counter() - t_start

    def pct(xs: List[float], q: float) -> float:
        s = sorted(xs)
        idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
        return round(s[idx], 2)

    return {
        "runs": runs, "warmup": warmup, "questions": len(questions),
        "wall_s": round(wall, 3),
        "qps": round(runs / wall, 2) if wall > 0 else None,
        "decisions_per_s": round(runs * len(questions) / wall, 1) if wall > 0 else None,
        "total": {"min": round(min(samples), 2), "mean": round(statistics.fmean(samples), 2),
                  "p50": pct(samples, .5), "p95": pct(samples, .95),
                  "p99": pct(samples, .99), "max": round(max(samples), 2),
                  "stdev": round(statistics.pstdev(samples), 2)},
        "infer": {"mean": round(statistics.fmean(infer), 2), "p95": pct(infer, .95)},
        "route": {"mean": round(statistics.fmean(route), 3), "p95": pct(route, .95)},
        "tokens": {"mean": round(statistics.fmean(tokens), 1), "max": max(tokens)},
        "per_model": per_model,
        "samples": [round(s, 2) for s in samples],
        "device": ENGINE.info().get("device_actual"),
    }


def api_ha_connect(body: dict) -> dict:
    # Ohne Angaben die gespeicherten nehmen -- so laesst sich der Katalog
    # aktualisieren, ohne den Token erneut einzutippen.
    url = (body.get("url") or "").strip() or STATE["ha_url"]
    token = (body.get("token") or "").strip() or STATE["ha_token"]
    if not url or not token:
        raise ValueError("url und token noetig")
    ha.ping(url, token)
    limit = int(body.get("limit", 200))
    ents, areas, info = ha.fetch_catalog(url, token, limit=limit)
    if not ents:
        raise ValueError("keine fuer Assist freigegebenen Entities in den "
                         "unterstuetzten Domains gefunden"
                         if info.get("assist_filter") else
                         "keine unterstuetzten Entities gefunden")
    STATE.update({"ha_url": url, "ha_token": token, "entities": ents, "areas": areas})
    if "save_token" in body:
        STATE["save_token"] = bool(body["save_token"])
    save_config()
    return {"ok": True, "token_gespeichert": STATE["save_token"], "entities": len(ents), "areas": len(areas),
            "catalog": ents, "area_list": areas,
            "assist_filter": info.get("assist_filter", False),
            "mit_alias": info.get("mit_alias", 0),
            "nicht_ansprechbar": info.get("nicht_ansprechbar", []),
            "warnung": info.get("warnung", "")}


def api_ha_execute(body: dict) -> dict:
    if not (STATE["ha_url"] and STATE["ha_token"]):
        raise ValueError("keine HA-Verbindung konfiguriert")
    call = body.get("toolcall") or {}
    if call.get("kind") == "query":
        eid = (call.get("target") or {}).get("entity_id")
        if not eid:
            raise ValueError("query ohne entity_id")
        return {"ok": True, "mode": "query",
                "result": ha.get_state(STATE["ha_url"], STATE["ha_token"], eid)}
    domain, service = call.get("domain"), call.get("service")
    if not domain or not service:
        raise ValueError("toolcall ohne domain/service nicht ausfuehrbar")
    res = ha.call_service(STATE["ha_url"], STATE["ha_token"], domain, service,
                          call.get("target") or {}, call.get("data") or {})
    return {"ok": True, "mode": "service_call", "result": res}


def api_fallback_get(_: dict) -> dict:
    return {"enabled": STATE["fb_enabled"], "url": STATE["fb_url"],
            "model": STATE["fb_model"], "threshold": STATE["fb_threshold"],
            "timeout": STATE["fb_timeout"]}


def api_fallback_set(body: dict) -> dict:
    if "url" in body:
        STATE["fb_url"] = (body["url"] or "").strip() or STATE["fb_url"]
    if "model" in body:
        STATE["fb_model"] = (body["model"] or "").strip()
    if "threshold" in body:
        STATE["fb_threshold"] = max(0.0, min(1.0, float(body["threshold"])))
    if "timeout" in body:
        STATE["fb_timeout"] = max(5.0, min(600.0, float(body["timeout"])))
    if "enabled" in body:
        want = bool(body["enabled"])
        if want and not STATE["fb_model"]:
            raise ValueError("kein Ollama-Modell gewaehlt")
        STATE["fb_enabled"] = want
    save_config()
    return api_fallback_get({})


def api_context_get(_: dict) -> dict:
    return {"entries": STATE["ctx_entries"], "max": CONTEXT_MAX}


def api_context_set(body: dict) -> dict:
    """Wieviele frühere Äußerungen gehen an laya? 0 schaltet den Kontext ab."""
    if "entries" in body:
        STATE["ctx_entries"] = max(0, min(CONTEXT_MAX, int(body["entries"])))
        save_config()
    return api_context_get({})


def api_fallback_models(_: dict) -> dict:
    return {"models": ollama.list_models(STATE["fb_url"])}


def api_trace(_: dict) -> dict:
    return {"trace": TRACE[-25:][::-1], "last_request": LAST_REQUEST}


def _note(entry: Dict[str, Any]):
    TRACE.append(entry)
    del TRACE[:-100]


def api_models(_: dict) -> dict:
    """OpenAI-Modellliste. Home Assistant fragt sie beim Einrichten ab."""
    now = int(time.time())
    models = [{"id": "laya", "object": "model", "created": now, "owned_by": "laya"}]
    if STATE["fb_enabled"] and STATE["fb_model"]:
        models.append({"id": "laya+" + STATE["fb_model"], "object": "model",
                       "created": now, "owned_by": "laya"})
    return {"object": "list", "data": models}


def api_chat(body: dict) -> dict:
    """OpenAI-kompatibler Endpunkt: laya zuerst, sonst Ollama."""
    messages = body.get("messages") or []
    if not messages:
        raise ValueError("'messages' fehlt")
    stream = bool(body.get("stream"))
    model_label = body.get("model") or "laya"

    # Schritt 2 eines Tool-Durchlaufs: HA hat das Tool ausgefuehrt und will nur
    # noch einen Satz zum Vorlesen. Dafuer ist laya nicht zustaendig -- es ist ein
    # Klassifikator und bildet keine Saetze.
    #
    # Nach einem Schaltbefehl genuegt eine feste Quittung. Nach einer Statusfrage
    # steht die eigentliche Antwort aber genau in diesem Tool-Ergebnis; ein festes
    # "Erledigt." verschluckt sie. Deshalb formuliert dann die Rueckfallebene --
    # sie hat das Ergebnis im Kontext. Ohne Rueckfallebene bleibt es bei der
    # Quittung, dann kann hier niemand einen Satz bilden.
    if oai.ends_with_tool_result(messages):
        if oai.result_needs_words(messages) and STATE["fb_enabled"] and STATE["fb_model"]:
            t0 = time.perf_counter()
            try:
                answer = ollama.chat(STATE["fb_url"], STATE["fb_model"], body,
                                     timeout=STATE["fb_timeout"])
                fb_ms = (time.perf_counter() - t0) * 1000.0
                said = ((answer.get("choices") or [{}])[0].get("message") or {}
                        ).get("content") or ""
                answer["laya"] = {"route": "antwort", "reason": "Ollama formuliert "
                                  "das Tool-Ergebnis", "ollama_ms": round(fb_ms, 1)}
                _note({"t": time.time(), "text": "(Tool-Ergebnis)", "route": "antwort",
                       "reason": "Ollama formuliert das Tool-Ergebnis",
                       "ms": round(fb_ms, 1), "tool": ", ".join(oai.answered_tools(messages)),
                       "args": said[:120]})
                return {"__raw__": answer, "__stream__": stream}
            except RuntimeError as e:
                _note({"t": time.time(), "text": "(Tool-Ergebnis)", "route": "quittung",
                       "reason": "Ollama nicht erreichbar (%s) -- feste Quittung" % e})
        payload = oai.completion(model_label, content="Erledigt.")
        _note({"t": time.time(), "text": "(Tool-Ergebnis)", "route": "quittung",
               "reason": "Client meldet Tool-Ausfuehrung zurueck",
               "tool": ", ".join(oai.answered_tools(messages))})
        return {"__raw__": payload, "__stream__": stream}

    text = oai.last_user_message(messages)
    if not text:
        raise ValueError("keine Nutzeraeusserung in 'messages'")

    tools = oai.declared_tools(body)
    LAST_REQUEST.clear()
    LAST_REQUEST.update({
        "at": time.time(),
        "text": text,
        "tool_names": sorted(tools),
        "tools": [{"name": n, "parameters": (f.get("parameters") or {})}
                  for n, f in tools.items()],
        "messages": [{"role": m.get("role"),
                      "content": (m.get("content") if isinstance(m.get("content"), str)
                                  else json.dumps(m.get("content"), ensure_ascii=False))
                                 if m.get("content") is not None else None}
                     for m in messages][-6:],
    })
    vorher = oai.history(messages, STATE["ctx_entries"])
    t0 = time.perf_counter()
    res = ENGINE.predict(text, _questions(), model=STATE["default_model"],
                         context={"vorher": vorher} if vorher else None,
                         context_for=hschema.CONTEXT_QUESTIONS,
                         allow_load=False)
    call = hschema.build_toolcall(res["answers"], STATE["entities"],
                                  STATE["fb_threshold"])
    tool_call, reason = oai.build_tool_call(
        res["answers"], call, STATE["entities"], STATE["areas"], tools,
        STATE["fb_threshold"])
    laya_ms = (time.perf_counter() - t0) * 1000.0

    if tool_call is not None:
        payload = oai.completion(model_label, content=None, tool_calls=[tool_call],
                                 extra={"route": "laya", "reason": reason,
                                        "ms": round(laya_ms, 1),
                                        "toolcall": call})
        _note({"t": time.time(), "text": text, "route": "laya", "reason": reason,
               "ms": round(laya_ms, 1), "offered": sorted(tools), "kontext": vorher,
               "tool": tool_call["function"]["name"],
               "args": tool_call["function"]["arguments"]})
        return {"__raw__": payload, "__stream__": stream}

    if not (STATE["fb_enabled"] and STATE["fb_model"]):
        # Ohne Rueckfallebene ehrlich sagen, dass es nicht reicht, statt zu raten.
        payload = oai.completion(
            model_label,
            content="Das habe ich nicht sicher verstanden.",
            extra={"route": "keine", "reason": reason, "ms": round(laya_ms, 1)})
        _note({"t": time.time(), "text": text, "route": "keine", "reason": reason,
               "ms": round(laya_ms, 1), "offered": sorted(tools), "kontext": vorher})
        return {"__raw__": payload, "__stream__": stream}

    t1 = time.perf_counter()
    answer = ollama.chat(STATE["fb_url"], STATE["fb_model"], body,
                         timeout=STATE["fb_timeout"])
    fb_ms = (time.perf_counter() - t1) * 1000.0
    answer.setdefault("laya", {})
    answer["laya"] = {"route": "ollama", "reason": reason,
                      "laya_ms": round(laya_ms, 1), "ollama_ms": round(fb_ms, 1)}
    ch = (answer.get("choices") or [{}])[0].get("message") or {}
    _note({"t": time.time(), "text": text, "route": "ollama", "reason": reason,
           "ms": round(laya_ms + fb_ms, 1), "offered": sorted(tools), "kontext": vorher,
           "tool": (ch.get("tool_calls") or [{}])[0].get("function", {}).get("name"),
           "args": (ch.get("tool_calls") or [{}])[0].get("function", {}).get("arguments")
                   or (ch.get("content") or "")[:120]})
    return {"__raw__": answer, "__stream__": stream}


ROUTES = {
    ("GET", "/api/health"): api_health,
    ("GET", "/api/schema"): api_schema,
    ("POST", "/api/schema"): api_schema_set,
    ("POST", "/api/predict"): api_predict,
    ("POST", "/api/bench"): api_bench,
    ("POST", "/api/ha/connect"): api_ha_connect,
    ("POST", "/api/ha/execute"): api_ha_execute,
    ("GET", "/api/context"): api_context_get,
    ("POST", "/api/context"): api_context_set,
    ("GET", "/api/fallback"): api_fallback_get,
    ("POST", "/api/fallback"): api_fallback_set,
    ("GET", "/api/fallback/models"): api_fallback_models,
    ("GET", "/api/trace"): api_trace,
    ("GET", "/v1/models"): api_models,
    ("POST", "/v1/chat/completions"): api_chat,
}


class Server(ThreadingHTTPServer):
    """Mehr wartende Verbindungen zulassen als die Standard-5.

    Weil jede Inferenz serialisiert laeuft, stauen sich gleichzeitige Anfragen vor
    dem Lock. Mit der Default-Queue werden sie ab der sechsten vom Kernel abgewiesen
    (Connection reset), statt kurz zu warten.
    """
    daemon_threads = True
    request_queue_size = 128


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, code: int, payload: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, obj: Any):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _static(self, path: str):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, name))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            return self._json(404, {"error": "not found"})
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                 ".css": "text/css"}.get(os.path.splitext(full)[1], "text/plain")
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def _handle(self, method: str):
        path = urlparse(self.path).path
        fn = ROUTES.get((method, path))
        if fn is None:
            if method == "GET":
                return self._static(path)
            return self._json(404, {"error": "unknown endpoint %s" % path})
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if raw:
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError as e:
                    return self._json(400, {"error": "ungueltiges JSON: %s" % e})
        try:
            out = fn(body)
            if isinstance(out, dict) and "__raw__" in out:
                payload = out["__raw__"]
                if out.get("__stream__"):
                    return self._send(200, oai.to_sse(payload),
                                      "text/event-stream; charset=utf-8")
                return self._json(200, payload)
            return self._json(200, out)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:                        # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._json(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def main():
    global ENGINE
    cfg = settings.load()
    srv_cfg = cfg.get("server") or {}

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=srv_cfg.get("port", 7788))
    ap.add_argument("--host", default=srv_cfg.get("host", "0.0.0.0"))
    ap.add_argument("--device", default=srv_cfg.get("device"),
                    help="mps, cpu oder cuda")
    ap.add_argument("--default-model", default=(cfg.get("model") or {}).get("default",
                                                               DEFAULT_MODEL),
                    help="fester Checkpoint statt Routing; 'router' fuer Auto-Routing")
    ap.add_argument("--ollama-url",
                    default=(cfg.get("fallback") or {}).get("url",
                             "http://localhost:11434"))
    ap.add_argument("--ollama-model", default="",
                    help="Modell fuer die Rueckfallebene; leer = aus")
    ap.add_argument("--preload", action="store_true",
                    default=bool(srv_cfg.get("preload", True)),
                    help="Checkpoint beim Start laden statt beim ersten Request")
    ap.add_argument("--no-preload", dest="preload", action="store_false")
    args = ap.parse_args()

    load_config()
    STATE["default_model"] = args.default_model
    STATE["fb_url"] = args.ollama_url
    if args.ollama_model:                      # nur ein echtes Argument sticht
        STATE["fb_model"] = args.ollama_model
        STATE["fb_enabled"] = True
    names = ([args.default_model] if args.default_model in ("english", "multilingual",
                                                            "typed-decisions")
             else ["multilingual"])
    ENGINE = Engine(device=args.device, preload=False, preload_names=names)
    save_config()                    # Datei anlegen, falls sie noch fehlt
    srv = Server((args.host, args.port), Handler)
    print("laya WebUI -> http://%s:%d" % (args.host, args.port))
    print("OpenAI-Endpunkt -> http://%s:%d/v1  (Modell 'laya')" % (args.host, args.port))
    print("Rueckfallebene  -> %s" % (("%s @ %s" % (args.ollama_model, args.ollama_url))
                                     if args.ollama_model else "aus"))
    if args.preload:
        # Im Hintergrund laden, damit die Seite sofort erreichbar ist und anzeigen
        # kann, dass das Modell noch kommt -- sonst sieht ein Neustart drei Minuten
        # lang aus wie ein Absturz.
        print("Checkpoint %s wird im Hintergrund geladen ..." % ", ".join(names))
        threading.Thread(target=ENGINE.ensure, kwargs={"preload_all": True},
                         daemon=True).start()
    else:
        print("Modell wird beim ersten Request geladen.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbeendet")


if __name__ == "__main__":
    main()
