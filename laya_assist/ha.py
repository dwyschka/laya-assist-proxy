"""Schmaler Home-Assistant-REST-Client: Katalog holen und Service-Calls absetzen.

Der Katalog enthaelt nur, was in Home Assistant fuer Assist freigegeben ist --
diese Freigabe kommt ueber die WebSocket-API (siehe ha_ws), weil die REST-API
sie nicht kennt. Bereiche werden dabei nicht eingeschraenkt: es kommen alle,
in denen freigegebene Entitaeten liegen.
"""
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from . import ha_ws

# Welche HA-Domains uebernehmen wir in den Katalog?
SUPPORTED_DOMAINS = {"light", "switch", "climate", "cover", "media_player",
                     "lock", "vacuum", "fan", "scene", "script"}


def _request(base_url: str, token: str, path: str, method: str = "GET",
             payload: Optional[dict] = None, timeout: float = 10.0) -> Any:
    url = base_url.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError("HA %s %s -> HTTP %d: %s"
                           % (method, path, e.code, e.read().decode()[:300]))
    except urllib.error.URLError as e:
        raise RuntimeError("HA nicht erreichbar (%s): %s" % (url, e.reason))
    return json.loads(body) if body.strip() else None


_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


def _slug(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch in _UMLAUTS:
            out.append(_UMLAUTS[ch])
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        elif ch in " -_/.":
            out.append("_")
    return "_".join(x for x in "".join(out).split("_") if x) or "entity"


def _describe(name: str, aliases: List[str], area_name: str) -> str:
    """Kriterientext fuer laya: Anzeigename, Aliase, Bereich."""
    head = ", ".join([name] + [a for a in aliases if a.lower() != name.lower()])
    return "%s%s" % (head, (" im Bereich %s" % area_name) if area_name else "")


# Jinja-Template, das HA serverseitig rendert. `area_name()` loest die echte
# Bereichszuordnung auf -- die steht weder in /api/states noch sonst irgendwo in
# der REST-API, wohl aber im Template-Kontext.
CATALOG_TEMPLATE = """
{%- set ns = namespace(items=[]) -%}
{%- for s in states -%}
  {%- if s.domain in __DOMAINS__ -%}
    {%- set ns.items = ns.items + [{
      'entity_id': s.entity_id, 'name': s.name,
      'area': area_name(s.entity_id) or '', 'domain': s.domain}] -%}
  {%- endif -%}
{%- endfor -%}
{{ ns.items | tojson }}
"""


def fetch_catalog_via_template(base_url: str, token: str, limit: int = 200,
                               allow: Optional[Dict[str, Dict[str, Any]]] = None
                               ) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """Katalog mit echten Bereichen, gerendert von Home Assistant selbst.

    `allow` ist die Assist-Freigabeliste aus ha_ws.fetch_assist_entities(); ist
    sie gesetzt, bleibt nur uebrig, was dort steht -- und die dort hinterlegten
    Aliase wandern in die Beschreibung, damit laya auf sie matchen kann.
    """
    tpl = CATALOG_TEMPLATE.replace("__DOMAINS__", json.dumps(sorted(SUPPORTED_DOMAINS)))
    raw = _request(base_url, token, "/api/template", method="POST",
                   payload={"template": tpl}, timeout=20.0)
    rows = raw if isinstance(raw, list) else json.loads(raw)
    if allow is not None:
        rows = [r for r in rows if r.get("entity_id") in allow]

    entities: Dict[str, Dict[str, str]] = {}
    areas: Dict[str, str] = {}
    for row in rows[:limit]:
        entry = (allow or {}).get(row["entity_id"]) or {}
        aliases = list(entry.get("aliases") or [])
        name = (entry.get("name") or row.get("name")
                or row["entity_id"].split(".", 1)[-1]).strip()
        key = _slug(name) or _slug(row["entity_id"])
        if key in entities:
            key = _slug("%s %s" % (name, row["entity_id"].split(".", 1)[-1]))
        area_name = (row.get("area") or "").strip()
        area_key = _slug(area_name) if area_name else ""
        if area_key:
            areas[area_key] = area_name
        entities[key] = {
            "name": name,
            "entity_id": row["entity_id"],
            "domain": row["domain"],
            "area": area_key,
            "desc": _describe(name, aliases, area_name),
        }
        if aliases:
            entities[key]["aliases"] = aliases
    return entities, areas


def fetch_catalog(base_url: str, token: str, limit: int = 200
                  ) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str], Dict[str, Any]]:
    """Katalog holen: bevorzugt per Template (echte Bereiche), sonst aus /api/states.

    Vorgeschaltet ist die Assist-Freigabe aus der WebSocket-API. Laesst sie sich
    nicht abrufen (kein Admin-Token, WebSocket blockiert), wird der Katalog
    ungefiltert aufgebaut -- das steht dann so im dritten Rueckgabewert, damit die
    Oberflaeche es sagen kann, statt es zu verschweigen.

    Der Rueckfallweg rät den Bereich aus dem ersten Wort des Anzeigenamens und
    liegt damit oft daneben -- er greift nur, wenn /api/template nicht verfuegbar ist.
    """
    info: Dict[str, Any] = {"assist_filter": False, "freigegeben": 0,
                            "mit_alias": 0, "nicht_ansprechbar": [],
                            "warnung": ""}
    allow: Optional[Dict[str, Dict[str, Any]]] = None
    try:
        allow = ha_ws.fetch_assist_entities(base_url, token)
        info["assist_filter"] = True
        info["freigegeben"] = len(allow)
    except Exception as e:
        info["warnung"] = ("Assist-Freigaben nicht abrufbar (%s) -- Katalog "
                           "ungefiltert" % e)

    try:
        ents, areas = fetch_catalog_via_template(base_url, token, limit=limit,
                                                 allow=allow)
        if ents or (allow is not None and not allow):
            info["mit_alias"] = sum(1 for e in ents.values() if e.get("aliases"))
            info["nicht_ansprechbar"] = verify_names(base_url, token, ents)
            return ents, areas, info
    except Exception:
        pass

    states = _request(base_url, token, "/api/states")
    entities: Dict[str, Dict[str, str]] = {}
    area_hits: Dict[str, int] = {}

    for st in states:
        eid = st.get("entity_id", "")
        domain = eid.split(".")[0]
        if domain not in SUPPORTED_DOMAINS:
            continue
        if allow is not None and eid not in allow:
            continue
        entry = (allow or {}).get(eid) or {}
        aliases = list(entry.get("aliases") or [])
        attrs = st.get("attributes") or {}
        name = (entry.get("name") or attrs.get("friendly_name")
                or eid.split(".", 1)[-1].replace("_", " "))
        key = _slug(name)
        if key in entities:
            key = _slug("%s %s" % (name, eid.split(".", 1)[-1]))
        head = _slug(name.split()[0]) if name.split() else ""
        if head:
            area_hits[head] = area_hits.get(head, 0) + 1
        entities[key] = {
            "name": name,
            "entity_id": eid,
            "domain": domain,
            "area": head,
            "desc": _describe(name, aliases, ""),
        }
        if aliases:
            entities[key]["aliases"] = aliases
        if len(entities) >= limit:
            break

    areas = {a: "Bereich %s" % a for a, n in sorted(area_hits.items(),
                                                    key=lambda kv: -kv[1]) if n > 1}
    for ent in entities.values():
        if ent["area"] not in areas:
            ent["area"] = ""
    info["mit_alias"] = sum(1 for e in entities.values() if e.get("aliases"))
    info["nicht_ansprechbar"] = verify_names(base_url, token, entities)
    return entities, areas, info


def _resolves(base_url: str, token: str, name: str, entity_id: str) -> bool:
    """Findet Home Assistant unter `name` genau diese Entitaet?

    Geprueft wird mit dem lesenden Intent HassGetState -- derselbe Matcher, den
    auch HassTurnOn benutzt, nur ohne Nebenwirkung. Nicht jeder Anzeigename ist
    ansprechbar: doppelte Namen sind mehrdeutig, und manche Namen kennt der
    Matcher schlicht nicht, obwohl die Entitaet freigegeben ist.
    """
    try:
        res = _request(base_url, token, "/api/intent/handle", method="POST",
                       payload={"name": "HassGetState", "data": {"name": name}},
                       timeout=10.0) or {}
    except RuntimeError:
        return False
    hits = ((res.get("data") or {}).get("success") or [])
    return any(h.get("id") == entity_id for h in hits)


def verify_names(base_url: str, token: str,
                 entities: Dict[str, Dict[str, Any]]) -> List[str]:
    """Traegt in jede Entitaet den Namen ein, unter dem HA sie wirklich findet.

    Reihenfolge: erst die Aliase (die hat der Nutzer genau dafuer vergeben), dann
    der Anzeigename. Was unter keinem Namen auffindbar ist, wird zurueckgegeben --
    dort hilft nur ein Alias in Home Assistant.
    """
    unreachable: List[str] = []
    for key, ent in entities.items():
        eid = ent["entity_id"]
        for candidate in list(ent.get("aliases") or []) + [ent.get("name") or ""]:
            if candidate and _resolves(base_url, token, candidate, eid):
                ent["assist_name"] = candidate
                break
        else:
            unreachable.append(eid)
    return unreachable


def call_service(base_url: str, token: str, domain: str, service: str,
                 target: Dict[str, Any], data: Dict[str, Any]) -> Any:
    payload = dict(data or {})
    payload.update(target or {})
    return _request(base_url, token, "/api/services/%s/%s" % (domain, service),
                    method="POST", payload=payload)


def get_state(base_url: str, token: str, entity_id: str) -> Any:
    return _request(base_url, token, "/api/states/%s" % entity_id)


def ping(base_url: str, token: str) -> Any:
    return _request(base_url, token, "/api/")
