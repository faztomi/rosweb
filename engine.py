"""ROS territorial: reproduce la lógica original del Excel suministrado.

No realiza solicitudes a OpenStreetMap ni presupone que los datos históricos
representen el entorno de una puerta específica.
"""
from __future__ import annotations
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "ros_territorios.json"
WEIGHTS = (17.5, 17.5, 27.5, 37.5)
NAMES = ("Población", "Densidad poblacional", "Densidad comercial", "Cupo disponible")


def normal(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


@lru_cache(maxsize=1)
def load_data():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    assert len(data["zonas"]) == 81
    return data


ALIASES = {
    # Solo localidades de asignación municipal clara. No se infiere barrio a partir de un comercio.
    "temperley": "Lomas De Zamora", "banfield": "Lomas De Zamora",
    "llavallol": "Lomas De Zamora", "turdera": "Lomas De Zamora",
    "villa centenario": "Lomas De Zamora", "ingeniero budge": "Lomas De Zamora",
    "adrogue": "Almirante Brown", "burzaco": "Almirante Brown",
    "longchamps": "Almirante Brown", "glew": "Almirante Brown",
    "claypole": "Almirante Brown", "rafael calzada": "Almirante Brown",
    "malvinas argentinas almirante brown": "Almirante Brown",
    "aldó bonzi": "La Matanza", "aldo bonzi": "La Matanza",
    "isidro casanova": "La Matanza", "san justo": "La Matanza",
    "villa luzuriaga": "La Matanza", "ramos mejia": "La Matanza",
    "tapiales": "La Matanza", "gregorio de laferrere": "La Matanza",
    "del viso": "Pilar", "presidente derqui": "Pilar",
    "pablo podesta": "Tres De Febrero", "caseros": "Tres De Febrero",
    "ciudadela": "Tres De Febrero", "santos lugares": "Tres De Febrero",
    "jose leon suarez": "General San Martin", "villa ballester": "General San Martin",
    "martinez": "San Isidro", "beccar": "San Isidro", "acassuso": "San Isidro",
    "wilde": "Avellaneda", "sarandi": "Avellaneda", "dock sud": "Avellaneda",
    "bernal": "Quilmes", "ezpeleta": "Quilmes", "don bosco": "Quilmes",
    "solano": "Quilmes", "san francisco solano": "Quilmes",
    "garin": "Escobar", "ingeniero maschwitz": "Escobar",
    "monte grande": "Esteban Echeverria", "el jaguel": "Esteban Echeverria",
    "canning": "Esteban Echeverria", "ezeiza": "Ezeiza",
}


def zone_indexes():
    zones = load_data()["zonas"]
    return {(z["ambito"], normal(z["nombre"])): z for z in zones}


def match_address(address: str):
    """Match a named barrio or municipio without relying on a live map provider.

    Only search after the first comma. Street names alone are NEVER interpreted
    as jurisdictions. Returns (row, confidence_label) or (None, reason).
    """
    segments = [normal(s) for s in str(address).split(",")]
    jurisdiction = ", ".join(segments[1:]).strip()
    if not jurisdiction:
        return None, "Escribí también la localidad o el barrio, después de una coma."
    explicit_caba = any(x in jurisdiction for x in ("caba", "capital federal", "ciudad autonoma", "ciudad de buenos aires"))
    idx = zone_indexes()
    matches = []
    for (scope,name),z in idx.items():
        # Exact segment or a longer jurisdiction segment with word boundaries.
        if any(re.search(r"(?<!\w)"+re.escape(name)+r"(?!\w)", s) for s in segments[1:]):
            matches.append((scope,z))
    if matches:
        if explicit_caba:
            matches = [(s,z) for s,z in matches if s=="CABA"]
        if matches:
            # Prefer the most specific CABA barrio when its jurisdiction was explicitly entered.
            matches.sort(key=lambda x:(x[0]=="CABA",len(x[1]["nombre"])),reverse=True)
            return matches[0][1], "Nombre territorial indicado en la dirección"
    for alias,mname in sorted(ALIASES.items(),key=lambda a:len(a[0]),reverse=True):
        alias=normal(alias)
        if any(re.search(r"(?<!\w)"+re.escape(alias)+r"(?!\w)", s) for s in segments[1:]):
            row=idx.get(("PBA",normal(mname)))
            if row:return row, "Localidad asociada a su municipio"
    return None, "No se identificó el barrio o municipio de la base ROS."


def find_by_name(scope, name):
    return zone_indexes().get((scope, normal(name)))


def score(row):
    """Same ordinal rating as the Excel: components in 1..4, weights 17.5/17.5/27.5/37.5."""
    values = row["componentes"]
    weighted = sum(float(x)*p/100.0 for x,p in zip(values, WEIGHTS))
    if abs(weighted-float(row["ros_original"]))>1e-6:
        raise ValueError("Los indicadores no coinciden con el ROS original")
    return {
        "raw":round(weighted,4),
        "score100":round(weighted/4*100,1),
        "clasificacion":row["clasificacion_original"],
        "parts":[{"name":n,"level":int(v),"weight":w,"points":round(w*float(v)/4,2)} for n,v,w in zip(NAMES,values,WEIGHTS)],
    }
