"""Projekteinstellungen: versionierbare project.yaml plus lokale Geheimnisse.

Zwei Dateien, bewusst getrennt:

  project.yaml            maschinenspezifisch: Modellwahl, Schwellen, Katalog,
                          Ollama-Adresse. Liegt im Projektverzeichnis und ist
                          gitignored; project.example.yaml zeigt den Aufbau.
  secrets.local.json      nur der HA-Token, und auch nur, wenn das Speichern
                          in der Oberflaeche erlaubt wurde. Ebenfalls gitignored.

Beim Schreiben geht PyYAML der Kommentarkopf verloren, deshalb wird er bei jedem
Speichern neu vorangestellt.
"""
import json
import os
from typing import Any, Dict

import yaml

def project_root() -> str:
    """Wo Konfiguration und Geheimnisse liegen.

    Nicht im Paket -- das kann in site-packages liegen und wird bei einem Update
    ueberschrieben. Voreinstellung ist das aktuelle Arbeitsverzeichnis (der
    Dienst wechselt dorthin), abweichend ueber LAYA_ASSIST_HOME.
    """
    return os.path.abspath(os.environ.get("LAYA_ASSIST_HOME") or os.getcwd())


PROJECT_YAML = os.path.join(project_root(), "project.yaml")
SECRETS_PATH = os.path.join(project_root(), "secrets.local.json")

HEADER = """\
# laya-assist-proxy -- Projekteinstellungen
#
# Maschinenspezifisch und gitignored. Sie enthaelt keine Geheimnisse: der
# Home-Assistant-Token liegt in secrets.local.json und wird nur geschrieben,
# wenn in der Oberflaeche "Token speichern" gesetzt ist.
#
# Geschrieben von der WebUI bei jeder Aenderung; Handbearbeitung ist erlaubt,
# wirkt aber erst nach einem Neustart des Servers.
"""

DEFAULTS: Dict[str, Any] = {
    "server": {"host": "0.0.0.0", "port": 7788, "device": None, "preload": True},
    # Wer entscheidet: laya im Prozess oder kev als eigener Server.
    "engine": {"name": "laya", "url": "http://localhost:8009", "model": "kev-latest"},
    "model": {"default": "multilingual", "threshold": 0.6},
    "fallback": {"enabled": False, "url": "http://localhost:11434",
                 "model": "", "timeout": 120.0},
    "homeassistant": {"url": "", "save_token": False},
    # Wieviele frühere Äußerungen laya mitbekommt. 0 = nur der aktuelle Befehl.
    "context": {"entries": 2},
    "catalog": {"areas": {}, "entities": {}},
}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is not None or k not in out:
            out[k] = v
    return out


def load() -> Dict[str, Any]:
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    if os.path.isfile(PROJECT_YAML):
        try:
            with open(PROJECT_YAML, encoding="utf-8") as f:
                cfg = _merge(cfg, yaml.safe_load(f) or {})
        except (OSError, yaml.YAMLError) as e:
            print("  project.yaml unlesbar, Defaults benutzt: %s" % e)
    return cfg


def load_token() -> str:
    if not os.path.isfile(SECRETS_PATH):
        return ""
    try:
        with open(SECRETS_PATH, encoding="utf-8") as f:
            return (json.load(f) or {}).get("ha_token", "")
    except (OSError, json.JSONDecodeError):
        return ""


def _atomic_write(path: str, text: str, mode: int = 0o644):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def save(cfg: Dict[str, Any]):
    body = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False)
    try:
        _atomic_write(PROJECT_YAML, HEADER + body)
    except OSError as e:
        print("  project.yaml nicht schreibbar: %s" % e)


def save_token(token: str, allowed: bool):
    """Token ablegen -- oder die Datei entfernen, wenn das Speichern abgewaehlt wurde."""
    try:
        if allowed and token:
            _atomic_write(SECRETS_PATH,
                          json.dumps({"ha_token": token}, indent=1), mode=0o600)
        elif os.path.isfile(SECRETS_PATH):
            os.remove(SECRETS_PATH)
    except OSError as e:
        print("  secrets.local.json nicht schreibbar: %s" % e)


# ------------------------------------------------------- Abbildung auf den Laufzeitzustand

def to_state(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "engine": cfg["engine"]["name"],
        "kev_url": cfg["engine"]["url"],
        "kev_model": cfg["engine"]["model"],
        "default_model": cfg["model"]["default"],
        "fb_threshold": float(cfg["model"]["threshold"]),
        "fb_enabled": bool(cfg["fallback"]["enabled"]),
        "fb_url": cfg["fallback"]["url"],
        "fb_model": cfg["fallback"]["model"] or "",
        "fb_timeout": float(cfg["fallback"]["timeout"]),
        "ctx_entries": int(cfg["context"]["entries"]),
        "ha_url": cfg["homeassistant"]["url"] or "",
        "save_token": bool(cfg["homeassistant"]["save_token"]),
        "entities": dict(cfg["catalog"]["entities"] or {}),
        "areas": dict(cfg["catalog"]["areas"] or {}),
    }


def from_state(state: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: dict(v) for k, v in cfg.items()}
    out["engine"] = {"name": state["engine"], "url": state["kev_url"],
                     "model": state["kev_model"]}
    out["model"] = {"default": state["default_model"], "threshold": state["fb_threshold"]}
    out["fallback"] = {"enabled": state["fb_enabled"], "url": state["fb_url"],
                       "model": state["fb_model"], "timeout": state["fb_timeout"]}
    out["homeassistant"] = {"url": state["ha_url"], "save_token": state["save_token"]}
    out["context"] = {"entries": state["ctx_entries"]}
    out["catalog"] = {"areas": state["areas"], "entities": state["entities"]}
    return out
