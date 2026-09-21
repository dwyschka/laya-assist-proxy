"""Frage-Schema fuer Home-Assistant-Toolcalls.

Das Schema uebersetzt einen Sprachbefehl in einen HA Service-Call. Es besteht aus
zwei Teilen:

  * ENTITIES  -- Katalog der schaltbaren Dinge (key -> entity_id, area, domain)
  * build_questions() -- daraus gebaute typed questions fuer laya

Der Katalog ist entweder der statische DEFAULT_ENTITIES oder wird per
ha.fetch_catalog() live aus einer Home-Assistant-Instanz erzeugt.
"""
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- Katalog

DEFAULT_AREAS = {
    "wohnzimmer": "Wohnzimmer, Couch, Fernsehbereich",
    "kueche": "Küche, Essbereich, Herd",
    "schlafzimmer": "Schlafzimmer, Bett",
    "bad": "Badezimmer, Dusche",
    "buero": "Arbeitszimmer, Büro, Schreibtisch",
    "flur": "Flur, Diele, Eingang",
    "garten": "Garten, Terrasse, Balkon, draußen",
}

DEFAULT_ENTITIES: Dict[str, Dict[str, str]] = {
    "licht_wohnzimmer": {
        "name": "Deckenlicht Wohnzimmer",
        "entity_id": "light.wohnzimmer_decke", "area": "wohnzimmer", "domain": "light",
        "desc": "Deckenlicht im Wohnzimmer, Wohnzimmerlampe",
    },
    "licht_kueche": {
        "name": "Licht Küche",
        "entity_id": "light.kueche_decke", "area": "kueche", "domain": "light",
        "desc": "Licht in der Küche, Küchenlampe",
    },
    "licht_schlafzimmer": {
        "name": "Nachttischlampe",
        "entity_id": "light.schlafzimmer_nachttisch", "area": "schlafzimmer", "domain": "light",
        "desc": "Nachttischlampe, Licht im Schlafzimmer",
    },
    "licht_buero": {
        "name": "Schreibtischlampe",
        "entity_id": "light.buero_schreibtisch", "area": "buero", "domain": "light",
        "desc": "Schreibtischlampe, Licht im Büro",
    },
    "heizung_wohnzimmer": {
        "name": "Heizung Wohnzimmer",
        "entity_id": "climate.wohnzimmer", "area": "wohnzimmer", "domain": "climate",
        "desc": "Thermostat und Heizung im Wohnzimmer",
    },
    "heizung_schlafzimmer": {
        "name": "Heizung Schlafzimmer",
        "entity_id": "climate.schlafzimmer", "area": "schlafzimmer", "domain": "climate",
        "desc": "Thermostat und Heizung im Schlafzimmer",
    },
    "rollladen_wohnzimmer": {
        "name": "Rollladen Wohnzimmer",
        "entity_id": "cover.wohnzimmer_rollladen", "area": "wohnzimmer", "domain": "cover",
        "desc": "Rollladen, Jalousie, Vorhang im Wohnzimmer",
    },
    "tv_wohnzimmer": {
        "name": "Fernseher",
        "entity_id": "media_player.wohnzimmer_tv", "area": "wohnzimmer", "domain": "media_player",
        "desc": "Fernseher, TV im Wohnzimmer",
    },
    "lautsprecher_kueche": {
        "name": "Lautsprecher Küche",
        "entity_id": "media_player.kueche_speaker", "area": "kueche", "domain": "media_player",
        "desc": "Lautsprecher, Radio, Musik in der Küche",
    },
    "kaffeemaschine": {
        "name": "Kaffeemaschine",
        "entity_id": "switch.kueche_kaffeemaschine", "area": "kueche", "domain": "switch",
        "desc": "Kaffeemaschine, Steckdose in der Küche",
    },
    "staubsauger": {
        "name": "Staubsauger",
        "entity_id": "vacuum.roborock", "area": "flur", "domain": "vacuum",
        "desc": "Staubsaugerroboter, Saugroboter",
    },
    "haustuer": {
        "name": "Haustür",
        "entity_id": "lock.haustuer", "area": "flur", "domain": "lock",
        "desc": "Haustürschloss, Tür abschließen",
    },
}

# ---------------------------------------------------------------- Aktionen

ACTIONS: Dict[str, Dict[str, Any]] = {
    "an": {
        "desc": "das Gerät soll an sein, laufen, eingeschaltet werden",
        "services": {"light": "turn_on", "switch": "turn_on", "media_player": "turn_on",
                     "climate": "turn_on", "vacuum": "start", "cover": "open_cover",
                     "lock": "unlock", "fan": "turn_on"},
    },
    "aus": {
        "desc": "das Gerät soll aus sein, stoppen, ausgeschaltet werden",
        "services": {"light": "turn_off", "switch": "turn_off", "media_player": "turn_off",
                     "climate": "turn_off", "vacuum": "return_to_base",
                     "cover": "close_cover", "lock": "lock", "fan": "turn_off"},
    },
    "wechseln": {
        "desc": "der Zustand soll umgekehrt werden, umschalten",
        "services": {"light": "toggle", "switch": "toggle", "media_player": "toggle",
                     "cover": "toggle", "fan": "toggle"},
    },
    "heller": {
        "desc": "eine bestimmte Helligkeit einstellen, dimmen",
        "services": {"light": "turn_on"},
    },
    "wärmer": {
        "desc": "eine bestimmte Temperatur einstellen, Heizung regeln",
        "services": {"climate": "set_temperature"},
    },
    "hoch": {
        "desc": "öffnen, hochfahren, aufmachen",
        "services": {"cover": "open_cover", "lock": "unlock"},
    },
    "runter": {
        "desc": "schließen, runterfahren, zumachen",
        "services": {"cover": "close_cover", "lock": "lock"},
    },
    "frage": {
        "desc": "es wird nur nach dem Zustand gefragt, nichts geschaltet",
        "services": {},
    },
}

BRIGHTNESS_LEVELS = ["aus / 0%", "sehr dunkel / 25%", "mittel / 50%",
                     "hell / 75%", "maximal / 100%"]
BRIGHTNESS_PCT = [0, 25, 50, 75, 100]

TEMP_LEVELS = ["sehr kalt / 16 Grad", "kühl / 18 Grad", "normal / 20 Grad",
               "warm / 22 Grad", "sehr warm / 24 Grad"]
TEMP_VALUES = [16, 18, 20, 22, 24]


# ---------------------------------------------------------------- Fragen

# Welche Fragen den Gespraechskontext sehen duerfen. Die Geraetefrage braucht ihn,
# um "mach ihn wieder aus" aufzuloesen. Die Aktionsfrage darf ihn nicht sehen: in
# der Vorgeschichte steht die umgekehrte Aktion ("mach den bambu an"), und das
# zieht die Antwort messbar dorthin.
CONTEXT_QUESTIONS = ("device", "area")


def build_questions(entities: Optional[Dict[str, Dict[str, str]]] = None,
                    areas: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Baut das typed-question-Schema aus einem Entity-Katalog."""
    entities = entities or DEFAULT_ENTITIES
    areas = areas or DEFAULT_AREAS

    device_crit = {k: v.get("desc") or v["entity_id"] for k, v in entities.items()}
    device_crit["unklar"] = "kein konkretes Gerät genannt oder nicht im Katalog"

    area_crit = dict(areas)
    area_crit["unklar"] = "kein Raum genannt"

    return {
        "action": {
            "type": "choice",
            "instructions": "Welche Aktion fordert `command` an?",
            "criteria": {k: v["desc"] for k, v in ACTIONS.items()},
        },
        "device": {
            "type": "choice",
            "instructions": "Welches Gerät aus der Liste meint `command`?",
            "criteria": device_crit,
        },
        "area": {
            "type": "choice",
            "instructions": "Welchen Raum nennt `command`?",
            "criteria": area_crit,
        },
        "brightness": {
            "type": "score",
            "instructions": "Welche Helligkeit verlangt `command`, falls es um Licht geht?",
            "criteria": BRIGHTNESS_LEVELS,
        },
        "temperature": {
            "type": "score",
            "instructions": "Welche Temperatur verlangt `command`, falls es um Heizung geht?",
            "criteria": TEMP_LEVELS,
        },
        "is_query": {
            "type": "noul",
            "instructions": "Ist `command` eine Frage?",
        },
        "needs_confirmation": {
            "type": "noul",
            "instructions": "Betrifft `command` ein Schloss, eine Tür oder die Sicherheit?",
        },
    }


# ---------------------------------------------------------------- Toolcall

# Nur diese Aktionen implizieren ihre Domaene so eindeutig, dass ein Geraetetausch
# innerhalb des Raums vertretbar ist. Bei turn_on/toggle/close waere er gefaehrlich:
# dort wuerde still etwas anderes geschaltet als genannt.
DOMAIN_IMPLYING = {"wärmer", "heller"}


def _same_area_fallback(entities: Dict[str, Dict[str, str]], entity: Dict[str, str],
                        action: str) -> Optional[Dict[str, str]]:
    """Sucht im Raum des gewaehlten Geraets eines, dessen Domaene die Aktion beherrscht."""
    if action not in DOMAIN_IMPLYING:
        return None
    area = entity.get("area")
    if not area:
        return None
    supported = ACTIONS[action]["services"]
    for cand in entities.values():
        if cand.get("area") == area and cand["domain"] in supported:
            return cand
    return None


def _fold(text: str) -> str:
    """Kleinschreibung ohne Umlaute -- fuer den Vergleich Befehl gegen Raumname."""
    out = text.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss"),
                 ("ae", "a"), ("oe", "o"), ("ue", "u")):
        out = out.replace(a, b)
    return out


def area_named(text: str, area_key: str, areas: Dict[str, str]) -> bool:
    """Kommt der Raum im Befehl ueberhaupt vor?

    Ohne diese Pruefung waere die Bereichsfrage gefaehrlich: sie antwortet auch
    dann mit voller Confidence, wenn gar kein Raum genannt wurde -- "mach den
    bambu an" ergab "Garten" mit 0,90. Ein Geraet, das der Befehl beim Namen
    nennt, darf davon nicht ueberstimmt werden. Verglichen wird wortweise und
    ohne Umlaute, damit "Wohnzimmerlicht" den Bereich "Wohnzimmer" trifft.
    """
    if not text:
        return False
    hay = _fold(text)
    label = areas.get(area_key, "") or area_key.replace("_", " ")
    return any(len(w) > 2 and w in hay for w in _fold(label).split())


def area_id_of(entities: Dict[str, Dict[str, str]], area_key: str) -> str:
    """Echte HA-Area-ID zu unserem Slug -- aus dem Katalog, nicht geraten.

    Unser Slug kommt aus dem Bereichsnamen ("Büro Daniel" -> buero_daniel), HA
    fuehrt die Area aber unter buro_daniel. Ein Service-Call mit dem Slug ginge
    ins Leere.
    """
    for ent in entities.values():
        if ent.get("area") == area_key and ent.get("area_id"):
            return ent["area_id"]
    return area_key


def _area_statt_geraet(entity: Optional[Dict[str, str]], area_key: str,
                       area_conf: float, threshold: float,
                       text: str, areas: Dict[str, str]) -> bool:
    """Widerspricht das gewaehlte Geraet dem sicher erkannten Raum?

    Die Geraetefrage verwechselt aehnlich benannte Geraete quer durch die
    Wohnung; die Bereichsfrage liegt dabei gemessen bei 1,00. Sagt der Befehl
    also klar einen Raum und liegt das gewaehlte Geraet in einem anderen, ist
    "dieser Typ in diesem Raum" die bessere Angabe als ein Geraetename, dem wir
    nicht trauen.
    """
    if entity is None or not area_key or area_key == "unklar":
        return False
    if area_conf < threshold or not area_named(text, area_key, areas):
        return False
    eigen = entity.get("area") or ""
    return bool(eigen) and eigen != area_key


def build_toolcall(answers: Dict[str, Any],
                   entities: Optional[Dict[str, Dict[str, str]]] = None,
                   threshold: float = 0.6, text: str = "",
                   areas: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Uebersetzt laya-Antworten in einen Home-Assistant Service-Call."""
    entities = entities or DEFAULT_ENTITIES
    areas = areas if areas is not None else DEFAULT_AREAS
    warnings: List[str] = []

    action = answers["action"]["choice"]
    action_conf = answers["action"]["confidence"]
    device_key = answers["device"]["choice"]
    device_conf = answers["device"]["confidence"]
    area_key = answers["area"]["choice"]
    area_conf = answers["area"]["confidence"]
    is_query = answers["is_query"]["noul"]

    if action_conf < threshold:
        warnings.append("Aktion unsicher (%.2f < %.2f)" % (action_conf, threshold))
    if device_conf < threshold:
        warnings.append("Gerät unsicher (%.2f < %.2f)" % (device_conf, threshold))

    if is_query > 0.5 or action == "frage":
        target = entities.get(device_key)
        return {
            "kind": "query",
            "domain": None,
            "service": None,
            "target": {"entity_id": target["entity_id"]} if target else {},
            "data": {},
            "warnings": warnings,
            "confidence": round(min(action_conf, device_conf), 4),
        }

    entity = entities.get(device_key)
    if entity is None:
        if area_key != "unklar" and area_conf >= threshold:
            warnings.append("Kein Gerät erkannt, fallback auf Bereich %r" % area_key)
            domain = "homeassistant"
            service = "turn_on" if action in ("an", "hoch") else "turn_off"
            return {
                "kind": "service_call", "domain": domain, "service": service,
                "target": {"area_id": area_key}, "data": {},
                "warnings": warnings, "confidence": round(area_conf, 4),
            }
        warnings.append("Kein Gerät zuzuordnen -- Rückfrage nötig")
        return {"kind": "unresolved", "domain": None, "service": None, "target": {},
                "data": {}, "warnings": warnings,
                "confidence": round(min(action_conf, device_conf), 4)}

    domain = entity["domain"]
    service = ACTIONS[action]["services"].get(domain)

    if service is not None and _area_statt_geraet(entity, area_key, area_conf,
                                                  threshold, text, areas):
        warnings.append("Gerät %s liegt in %r, genannt wurde %r -- es zählt der Raum"
                        % (entity["entity_id"], entity.get("area"), area_key))
        return {
            "kind": "service_call", "domain": domain, "service": service,
            "target": {"area_id": area_id_of(entities, area_key)}, "data": {},
            "warnings": warnings,
            "confidence": round(min(action_conf, area_conf), 4),
        }

    if service is None:
        # Aktion passt nicht zur Domaene des gewaehlten Geraets -- das passiert, wenn das
        # Geraet knapp daneben liegt ("zu kalt" -> Licht statt Heizung). Steht im selben
        # Raum eines, das die Aktion beherrscht, ist das die bessere Wahl.
        alt = _same_area_fallback(entities, entity, action)
        if alt is not None:
            warnings.append("Aktion %r passt nicht zu %s -- stattdessen %s im selben Raum"
                            % (action, entity["entity_id"], alt["entity_id"]))
            entity = alt
            domain = entity["domain"]
            service = ACTIONS[action]["services"].get(domain)

    if service is None:
        warnings.append("Aktion %r ist fuer Domain %r nicht definiert" % (action, domain))
        return {"kind": "unsupported", "domain": domain, "service": None,
                "target": {"entity_id": entity["entity_id"]}, "data": {},
                "warnings": warnings, "confidence": round(action_conf, 4)}

    data: Dict[str, Any] = {}
    if action == "heller" and domain == "light":
        idx = int(round(answers["brightness"]["score"]))
        idx = max(0, min(len(BRIGHTNESS_PCT) - 1, idx))
        pct = BRIGHTNESS_PCT[idx]
        if pct == 0:
            service = "turn_off"
        else:
            data["brightness_pct"] = pct
    elif action == "wärmer" and domain == "climate":
        idx = int(round(answers["temperature"]["score"]))
        idx = max(0, min(len(TEMP_VALUES) - 1, idx))
        data["temperature"] = TEMP_VALUES[idx]

    if answers["needs_confirmation"]["noul"] > 0.6:
        warnings.append("Modell haelt eine Rückfrage fuer sinnvoll (%.2f)"
                        % answers["needs_confirmation"]["noul"])

    return {
        "kind": "service_call",
        "domain": domain,
        "service": service,
        "target": {"entity_id": entity["entity_id"]},
        "data": data,
        "warnings": warnings,
        "confidence": round(min(action_conf, device_conf), 4),
    }
