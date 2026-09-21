"""kev als Alternative zu laya: dieselben Fragen, anderer Entscheider.

kev (https://github.com/jaredpalmer/kev) ist eine Familie kleiner
Entscheidungsmodelle auf Qwen-Basis -- dieselbe Idee wie laya und, praktischer
noch, dieselben Fragetypen: `choice`, `score`, `noul`. Deshalb passt unser
Frage-Schema unveraendert hinein.

Der Unterschied ist die Bauform: laya laeuft im Prozess, kev als eigener Server
(`python -m kev.serve --run jaredpalmer/kev-4b --port 8009`). Wir sprechen ihn
darum ueber HTTP an, genau wie die Ollama-Rueckfallebene.

Die Antwortstruktur ist bis auf eine Kleinigkeit identisch: `noul` bringt bei kev
keine `confidence` mit. Die rechnen wir nach, damit alles dahinter -- Toolcall,
Schwelle, Oberflaeche -- nicht wissen muss, wer geantwortet hat.
"""
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class KevError(RuntimeError):
    pass


def _post(base_url: str, path: str, payload: dict, timeout: float) -> Any:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise KevError("kev HTTP %d: %s" % (e.code, e.read().decode()[:300]))
    except urllib.error.URLError as e:
        raise KevError("kev nicht erreichbar (%s): %s" % (url, e.reason))


def _get(base_url: str, path: str, timeout: float = 10.0) -> Any:
    url = base_url.rstrip("/") + path
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise KevError("kev HTTP %d" % e.code)
    except urllib.error.URLError as e:
        raise KevError("kev nicht erreichbar (%s): %s" % (url, e.reason))


def _normalise(answers: Dict[str, Any]) -> Dict[str, Any]:
    """Antworten auf die Form bringen, die der Rest des Programms erwartet."""
    for ans in answers.values():
        if ans.get("type") == "noul" and "confidence" not in ans:
            p = float(ans.get("noul", 0.0))
            ans["confidence"] = round(max(p, 1.0 - p), 4)
        ans.setdefault("confidence", 0.0)
        ans.setdefault("action", {})
    return answers


class Engine:
    """Gleiche Schnittstelle wie der laya-Motor im Server, nur ueber HTTP."""

    def __init__(self, url: str, model: str = "kev-latest", timeout: float = 60.0):
        self.url = url
        self.model = model
        self.timeout = timeout

    # -- Zustand

    def info(self) -> Dict[str, Any]:
        out = {"engine": "kev", "url": self.url, "model": self.model,
               "loaded": False, "loading": False, "load_ms": None,
               "checkpoints": [], "device_actual": None, "error": None}
        try:
            info = _get(self.url, "/api/info")
            out["loaded"] = True
            out["checkpoints"] = [info.get("run") or self.model]
            out["device_actual"] = info.get("device")
        except KevError as e:
            try:                                  # aeltere Staende ohne /api/info
                models = _get(self.url, "/v1/models")
                out["loaded"] = True
                out["checkpoints"] = [m.get("id") for m in (models.get("data") or [])]
            except KevError:
                out["error"] = str(e)
        return out

    # -- Entscheiden

    def predict(self, text: str, questions: Dict[str, Any],
                model: Optional[str] = None,
                context: Optional[Dict[str, Any]] = None,
                context_for: Optional[Any] = None,
                allow_load: bool = True) -> Dict[str, Any]:
        """Wie Engine.predict des laya-Motors -- inklusive der Kontext-Trennung.

        `allow_load` ist hier ohne Bedeutung: kev haelt sein Modell selbst und
        laedt nichts auf Zuruf nach. Das Argument bleibt, damit der Server beide
        Motoren gleich aufrufen kann.
        """
        state: Dict[str, Any] = {"command": text}
        state_ctx = dict(state)
        if context:
            state_ctx.update(context)

        if context and context_for is not None:
            wanted = set(context_for)
            gruppen = [({k: v for k, v in questions.items() if k in wanted}, state_ctx),
                       ({k: v for k, v in questions.items() if k not in wanted}, state)]
        elif context:
            gruppen = [(questions, state_ctx)]
        else:
            gruppen = [(questions, state)]
        gruppen = [(q, st) for q, st in gruppen if q]

        t0 = time.perf_counter()
        answers: Dict[str, Any] = {}
        tokens = 0
        for qs, st in gruppen:
            res = _post(self.url, "/v1/systemone",
                        {"state": st, "model": model or self.model, "questions": qs},
                        timeout=self.timeout)
            answers.update(res.get("answers") or {})
            tokens += int(((res.get("usage") or {}).get("input_tokens")) or 0)
        ms = (time.perf_counter() - t0) * 1000.0

        fehlend = [q for q in questions if q not in answers]
        if fehlend:
            raise KevError("kev hat %s nicht beantwortet" % ", ".join(fehlend))

        return {
            "model": model or self.model,
            "answers": _normalise({k: answers[k] for k in questions}),
            "usage": {"input_tokens": tokens, "output_tokens": 0},
            "routing": {"model": model or self.model, "reason": "kev (%s)" % self.url,
                        "engine": "kev"},
            "timing": {"route_ms": 0.0, "load_ms": 0.0,
                       "infer_ms": round(ms, 3), "total_ms": round(ms, 3)},
            "device": "kev@%s" % self.url,
        }
