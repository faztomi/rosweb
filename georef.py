"""Optional address validation. A failed geocoder NEVER prevents territorial score."""
from __future__ import annotations
import re
from difflib import SequenceMatcher
from engine import normal

BASE = "https://apis.datos.gob.ar/georef/api/v2.0/direcciones"
FALLBACK = "https://apis.datos.gob.ar/georef/api/direcciones"


def street_and_number(addr):
    street=str(addr).split(",")[0].strip()
    # Last numeric token is a door number for a common Argentine street address.
    m=re.search(r"\s(\d{1,5})\s*$",street)
    if not m:return normal(street),None
    return normal(street[:m.start()]),int(m.group(1))


def street_similarity(requested, returned):
    a=normal(requested);b=normal(returned)
    common=("avenida", "av", "calle", "doctor", "dr", "general", "gral")
    for w in common:
        a=re.sub(r"\b"+w+r"\b", "", a)
        b=re.sub(r"\b"+w+r"\b", "", b)
    a=" ".join(a.split());b=" ".join(b.split())
    if not a or not b:return 0
    # Full match, or provider with expanded given names (e.g. Mariano S. Loria).
    if a==b:return 1
    wa=a.split();wb=b.split()
    if len(wa)==1 and wa[0] in wb:return 0.92
    if len(wa)>1 and len(wb)>1 and wa[-1]==wb[-1]:return 0.88
    return SequenceMatcher(None,a,b).ratio()


def verify_address(address, zone, session=None):
    """Only an extra confirmation. Never geocode using an unverified wrong municipality."""
    import requests
    session=session or requests
    street,num=street_and_number(address)
    if not street or num is None:return {"verified":False,"reason":"Dirección sin calle y altura verificables"}
    juris=normal(zone['nombre'])
    params={"direccion":str(address),"max":5}
    # Constrain the provider by province + administrative department when possible.
    if zone['ambito']=='PBA':
        params["provincia"]="Buenos Aires";params["departamento"]=zone['nombre']
    else:params["provincia"]="Ciudad Autónoma de Buenos Aires"
    for url in (BASE,FALLBACK):
        try:
            res=session.get(url,params=params,timeout=6,headers={"User-Agent":"ROS-EvaluadorTerritorial/1.0 (contact: administrador de la app)"})
            res.raise_for_status(); items=res.json().get('direcciones') or []
        except Exception:continue
        for row in items:
            depart=normal((row.get('departamento') or {}).get('nombre',''))
            prov=normal((row.get('provincia') or {}).get('nombre',''))
            if zone['ambito']=='PBA' and depart!=juris:continue
            if zone['ambito']=='CABA' and not ('ciudad autonoma' in prov or 'capital federal' in prov):continue
            height=(row.get('altura') or {}).get('valor')
            try: height=int(height)
            except (ValueError,TypeError):continue
            if height!=num:continue
            returned=(row.get('calle') or {}).get('nombre','')
            if street_similarity(street,returned)<0.86:continue
            location=row.get('ubicacion') or {}
            if location.get('lat') is None or location.get('lon') is None:continue
            return {"verified":True,"lat":float(location['lat']),"lon":float(location['lon']),"display":row.get('nomenclatura') or str(address),"provider":"Georef Argentina"}
    return {"verified":False,"reason":"No se verificó la altura exacta con el municipio solicitado"}
