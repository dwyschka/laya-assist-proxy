"""Rückfallebene: Anfrage an Ollama weiterreichen.

Ollama bringt selbst eine OpenAI-kompatible Route mit (`/v1/chat/completions`),
inklusive Tool-Calling. Die Anfrage geht daher unverändert weiter -- nur das
Modellfeld wird auf das konfigurierte Modell gesetzt. Kein Übersetzen, kein
zweites Format, das auseinanderlaufen kann.
"""
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List


def _post(base_url: str, path: str, payload: dict, timeout: float) -> Any:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError("Ollama HTTP %d: %s" % (e.code, e.read().decode()[:300]))
    except urllib.error.URLError as e:
        raise RuntimeError("Ollama nicht erreichbar (%s): %s" % (url, e.reason))


def chat(base_url: str, model: str, body: Dict[str, Any],
         timeout: float = 120.0) -> Dict[str, Any]:
    """Reicht eine Chat-Anfrage durch und liefert die Antwort im OpenAI-Format."""
    payload = dict(body)
    payload["model"] = model
    payload["stream"] = False          # Streaming macht der aufrufende Server selbst
    return _post(base_url, "/v1/chat/completions", payload, timeout)


def list_models(base_url: str, timeout: float = 10.0) -> List[str]:
    url = base_url.rstrip("/") + "/api/tags"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError("Ollama HTTP %d" % e.code)
    except urllib.error.URLError as e:
        raise RuntimeError("Ollama nicht erreichbar (%s): %s" % (url, e.reason))
    return [m.get("name") for m in data.get("models", []) if m.get("name")]
