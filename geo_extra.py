"""Complementos OSM, completamente opcionales para el ROS territorial.

Los datos de Overpass son públicos, pero los servidores NO garantizan disponibilidad.
Una falla de red jamás modifica ni impide el cálculo ROS original.
"""
from __future__ import annotations
import math
import time
import threading
import requests
from engine import normal

ENDPOINTS = (
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
)
USER_AGENT = 'ROS-EvaluadorComercial/1.2 (+https://rosweb.streamlit.app/)'
_CACHE = {}
_LOCK = threading.Lock()


def meters(lat1, lon1, lat2, lon2):
    a, b, c, d = map(math.radians, (float(lat1), float(lon1), float(lat2), float(lon2)))
    x = math.sin((c-a)/2)**2 + math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(x)))


def _query(lat, lon, radius=1000):
    lat, lon = float(lat), float(lon)
    r = int(radius)
    # Consultas moderadas: POIs comerciales + transporte + ejes viales principales.
    return f'''[out:json][timeout:16];(
      nwr(around:{r},{lat:.6f},{lon:.6f})["shop"~"^(supermarket|convenience|grocery)$"];
      nwr(around:800,{lat:.6f},{lon:.6f})["highway"="bus_stop"];
      nwr(around:900,{lat:.6f},{lon:.6f})["railway"~"^(station|halt|subway_entrance)$"];
      way(around:950,{lat:.6f},{lon:.6f})["highway"~"^(primary|secondary|trunk|motorway)$"];
    );out center;'''


def _coords(e):
    p = e if e.get('lat') is not None and e.get('lon') is not None else e.get('center') or {}
    try:
        lat, lon = float(p['lat']), float(p['lon'])
        if -55 < lat < -20 and -75 < lon < -50:
            return lat, lon
    except (TypeError, ValueError, KeyError):
        pass
    return None


def _normalize_response(elements, lat, lon):
    shops, transport, roads, seen = [], [], [], set()
    for e in elements:
        if not isinstance(e, dict): continue
        k = (e.get('type'), e.get('id'))
        if k in seen: continue
        seen.add(k)
        position = _coords(e)
        if not position: continue
        tag = e.get('tags') or {}
        dist = round(meters(lat, lon, *position))
        name = str(tag.get('name') or tag.get('brand') or 'Supermercado sin nombre')
        data = {'name': name, 'brand': str(tag.get('brand') or ''), 'distance_m': dist,
                'lat': position[0], 'lon': position[1], 'osm_id': f"{e.get('type')}/{e.get('id')}",
                'shop': tag.get('shop')}
        if tag.get('shop') in {'supermarket','convenience','grocery'} and dist <= 1100:
            shops.append(data)
        if (tag.get('highway') == 'bus_stop' or tag.get('railway') in {'station','halt','subway_entrance'}) and dist <= 900:
            transport.append({**data, 'kind': 'Colectivo' if tag.get('highway') == 'bus_stop' else 'Tren/Subte'})
        if tag.get('highway') in {'primary','secondary','trunk','motorway'}:
            roads.append({**data,'kind':tag.get('highway'), 'distance_note':'Centro del tramo cartográfico; no mide acceso real'})
    shops.sort(key=lambda x: x['distance_m'])
    transport.sort(key=lambda x: x['distance_m'])
    roads.sort(key=lambda x: x['distance_m'])
    return {'ok': True, 'supermarkets':shops[:25], 'transport': transport[:30],
            'roads':roads[:20], 'source':'OpenStreetMap / Overpass', 'radius_m':1000}


def fetch_nearby(lat, lon, *, transport=requests.post, endpoints=None, force=False):
    """Una consulta de bajo volumen a lo sumo, con caché de éxitos.

    No guarda fallas de forma persistente; devuelve error amigable al usuario.
    Se puede inyectar transport para pruebas offline.
    """
    lat, lon = float(lat), float(lon)
    key = (round(lat,4),round(lon,4))
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] > time.time() and not force:
            return dict(cached[1], cache=True)
    errors = []
    endpoints = endpoints or ENDPOINTS
    for endpoint in endpoints:
        try:
            resp = transport(endpoint, data={'data':_query(lat,lon)},
                headers={'User-Agent':USER_AGENT, 'Accept':'application/json'},
                timeout=(3,9))
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload,dict) or not isinstance(payload.get('elements'),list):
                raise ValueError('Respuesta OSM sin elementos válidos')
            result = _normalize_response(payload['elements'],lat,lon)
            result['endpoint'] = endpoint
            result['cache'] = False
            with _LOCK:
                _CACHE[key] = (time.time()+60*60*12,result)
            return result
        except (requests.RequestException, ValueError, KeyError) as exc:
            errors.append(f'{endpoint.split("/")[2]}: {type(exc).__name__}')
            # No reintentar inmediatamente este servidor ante límites.
    return {'ok':False, 'supermarkets':[], 'transport':[], 'roads':[],
            'source':'OpenStreetMap / Overpass', 'detail':'; '.join(errors)}


def chain_is_same(brand, expected):
    a,b = normal(brand),normal(expected)
    if not a or not b:return False
    # evita asumir que un nombre corto como DIA aparece en cualquier palabra
    return a == b or (len(a)>3 and a in b.split()) or (len(b)>3 and b in a.split())


def infer_chain(filename, rows=None):
    """Inferencia de marca solo a efectos informativos, no para validar sucursales."""
    base = normal(filename or '')
    for brand in ('Carrefour','DIA','Coto','Jumbo','Disco','Chango Mas','Vea','La Anonima'):
        if normal(brand) in base.split():return brand
    if rows:
        vals = []
        for row in rows[:30]:
            cleaned = {normal(k):v for k,v in row.items()}
            vals.append(cleaned.get('cadena') or cleaned.get('marca') or '')
        nonempty=[normal(x) for x in vals if normal(x)]
        if len(nonempty)>=3 and len(set(nonempty))==1:
            return str(vals[0])
    return ''
