"""Rückfallebene: Anfrage an einen OpenAI-kompatiblen Endpunkt weiterreichen.

Was laya nicht sicher genug entscheidet, geht unveraendert weiter -- nur das
Modellfeld wird gesetzt. Kein Uebersetzen, kein zweites Format, das auseinander
laufen kann.

Der Endpunkt ist frei waehlbar, solange er `/chat/completions` und `/models` im
OpenAI-Format spricht. Erprobt mit Ollama (`http://localhost:11434`) und
Lemonade (`http://localhost:8000/api/v1`).

Zur Basis-URL: manche Server nennen ihr Praefix selbst (`/api/v1` bei Lemonade),
andere erwarten, dass man `/v1` anhaengt (Ollama). Darum wird genau dann `/v1`
ergaenzt, wenn der Pfad nicht schon darauf endet -- so ist beide Schreibweisen
richtig und niemand muss raten.
"""
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


def base_url(url: str) -> str:
    """URL auf das OpenAI-Praefix bringen; `/v1` nur anhaengen, wenn es fehlt."""
    clean = (url or "").strip().rstrip("/")
    if not clean:
        return ""
    path = urlparse(clean).path.rstrip("/")
    return clean if path.endswith("/v1") else clean + "/v1"


def _headers(api_key: Optional[str]) -> Dict[str, str]:
    head = {"Content-Type": "application/json"}
    if api_key:
        head["Authorization"] = "Bearer %s" % api_key
    return head


def _request(url: str, payload: Optional[dict], timeout: float,
             api_key: Optional[str]) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 method="POST" if data else "GET")
    for k, v in _headers(api_key).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError("Rückfallebene HTTP %d: %s"
                           % (e.code, e.read().decode()[:300]))
    except urllib.error.URLError as e:
        raise RuntimeError("Rückfallebene nicht erreichbar (%s): %s" % (url, e.reason))
    return json.loads(body) if body.strip() else None


def chat(url: str, model: str, body: Dict[str, Any], timeout: float = 120.0,
         api_key: Optional[str] = None) -> Dict[str, Any]:
    """Reicht eine Chat-Anfrage durch und liefert die Antwort im OpenAI-Format."""
    payload = dict(body)
    payload["model"] = model
    payload["stream"] = False          # Streaming macht der aufrufende Server selbst
    return _request(base_url(url) + "/chat/completions", payload, timeout, api_key)


def list_models(url: str, timeout: float = 10.0,
                api_key: Optional[str] = None) -> List[str]:
    """Modellliste des Endpunkts.

    Zuerst der OpenAI-Weg `/models`. Ollama-Staende vor der OpenAI-Route kennen
    nur `/api/tags` -- dafuer der zweite Versuch, damit eine alte Installation
    nicht ohne Modellauswahl dasteht.
    """
    try:
        data = _request(base_url(url) + "/models", None, timeout, api_key) or {}
        namen = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        if namen:
            return sorted(namen)
    except RuntimeError as openai_fehler:
        try:
            data = _request((url or "").rstrip("/") + "/api/tags", None,
                            timeout, api_key) or {}
        except RuntimeError:
            raise openai_fehler
        return sorted(m.get("name") for m in (data.get("models") or []) if m.get("name"))
    return []
