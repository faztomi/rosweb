"""Geocodificación resiliente y auditable para Asistente de Expansión Retail.

Resolución oficial Georef -> OSM/Nominatim -> Photon. Siempre exige que
la calle, altura o cruce coincidan, y nunca usa centroides como direcciones.
No se guarda en caché ningún error ni resultado vacío.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path

import requests

GEOREF_API = os.environ.get('ROS_GEOREF_URL', 'https://apis.datos.gob.ar/georef/api/v2.0/direcciones')
PHOTON_API = os.environ.get('ROS_PHOTON_URL', 'https://photon.komoot.io/api/')


def normalize(value):
    s = unicodedata.normalize('NFKD', str(value or '').lower())
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return ' '.join(s.split())


_STOP = set('avenida av avda calle ruta gral general dr doctor don de del la las los el y esq esquina n nro numero boulevard bv pres presidente'.split())


def road_tokens(value):
    return {t for t in normalize(value).split() if t not in _STOP}


def same_road(a, b):
    aa, bb = road_tokens(a), road_tokens(b)
    if not aa or not bb:
        return False
    overlap = len(aa & bb)
    return overlap >= max(1, min(len(aa), len(bb)) - 1) and overlap >= max(1, round(len(aa) * 0.6))


def extract_address(address, case=None):
    if case:
        raw = str(case.get('direccion') or '').strip()
        locality = str(case.get('localidad') or '').split('(')[0].strip()
        municipality = str(case.get('municipio_ros') or '').strip()
        province = str(case.get('provincia') or 'Buenos Aires').strip()
    else:
        parts = [p.strip() for p in str(address or '').split(',')]
        raw = parts[0]
        locality = parts[1] if len(parts) >= 2 else ''
        municipality = parts[2] if len(parts) >= 4 else ''
        normalized = normalize(address)
        if any(x in normalized for x in ('caba','capital federal','ciudad autonoma de buenos aires')):
            province = 'Ciudad Autónoma de Buenos Aires'
        else:
            province = 'Buenos Aires' if 'buenos aires' in normalized else ''
    number_match = re.search(r'\b(\d{1,5})\s*$', raw) # evita '9 de Julio' como altura
    number = int(number_match.group(1)) if number_match else None
    street = (raw[:number_match.start()] if number_match else raw).strip(' ,/')
    # En una esquina con dos calles: "Valentín Gómez y French", "Sarratea esq. Serrano".
    corner_parts = re.split(r'\s+(?:esq\.?|esquina|y)\s+', raw, maxsplit=1, flags=re.I)
    corner = len(corner_parts) == 2 and number is None
    if corner:
        street, cross_street = corner_parts[0].strip(), corner_parts[1].strip()
    else:
        cross_street = ''
    return {'raw':raw,'street':street,'number':number,'corner':corner,
            'cross_street':cross_street,'locality':locality,'municipality':municipality,
            'province':province}


def _coords_ok(lat, lon):
    try:
        return -56 <= float(lat) <= -21 and -74 <= float(lon) <= -53
    except (ValueError, TypeError):
        return False


def _jurisdiction_ok(candidate, wanted):
    cand = normalize(candidate)
    exp = normalize(wanted)
    if not cand or not exp:
        return True
    if cand == exp or cand in exp or exp in cand:
        return True
    def strip_prefix(s):
        for x in ('general ', 'partido de ', 'partido del '):
            if s.startswith(x): s = s[len(x):]
        return s
    return strip_prefix(cand) == strip_prefix(exp)


def _from_georef(item, a, case):
    point = item.get('ubicacion') or {}
    lat, lon = point.get('lat'), point.get('lon')
    if not _coords_ok(lat, lon):
        return None
    name = ((item.get('calle') or {}).get('nombre') or '')
    if not same_road(a['street'], name):
        return None
    if a['number'] is not None:
        value = (item.get('altura') or {}).get('valor')
        if value is None or str(value).split('.')[0] != str(a['number']):
            return None
    elif a['corner']:
        cross = ((item.get('calle_cruce_1') or {}).get('nombre') or '')
        if not same_road(a['cross_street'], cross):
            return None
    else:
        # Nunca aceptar un punto de la calle genérica como si fuera un inmueble.
        return None
    dept = (item.get('departamento') or {}).get('nombre') or ''
    province = (item.get('provincia') or {}).get('nombre') or ''
    if a['municipality'] and dept and not _jurisdiction_ok(dept, a['municipality']):
        # Una esquina limítrofe requiere confirmación de la jurisdicción
        # del inmueble; no usar el municipio cargado para forzar el match.
        return None
    if a['province'] and province and not _jurisdiction_ok(province, a['province']):
        return None
    loc = (item.get('localidad_censal') or item.get('localidad') or {}).get('nombre') or a['locality']
    return {
        'lat':str(lat), 'lon':str(lon),
        'display_name':item.get('nomenclatura') or f"{a['raw']}, {loc}, {dept}, {province}",
        'address':{'road':name, 'house_number':str(a['number']) if a['number'] else '',
                   'suburb':loc,'city':loc,'county':dept or a['municipality'],
                   'state':province or a['province']},
        'origin':'Georef Argentina · dirección verificada',
    }


def _from_nominatim(item, a, case):
    lat, lon = item.get('lat'), item.get('lon')
    if not _coords_ok(lat, lon):
        return None
    addr = item.get('address') or {}
    street = addr.get('road') or addr.get('pedestrian') or addr.get('residential') or ''
    if not same_road(a['street'], street):
        return None
    if a['number'] is not None:
        num = str(addr.get('house_number') or '').strip()
        if num != str(a['number']):
            return None
    elif a['corner']:
        # Sin dos calles confirmadas Nominatim no puede corroborar el cruce.
        return None
    else:
        return None
    place = normalize(' '.join(str(addr.get(k,'')) for k in
               ('suburb','neighbourhood','town','city','county','municipality','state')))
    if a['locality'] or a['municipality']:
        if not any((('buenos aires' in place) if normalize(v) in ('caba','capital federal','ciudad autonoma de buenos aires') else normalize(v) in place)
                   for v in (a['locality'],a['municipality']) if v):
            return None
    return dict(item, origin='OpenStreetMap Nominatim · número verificado')


def _from_photon(feature, a, case):
    prop = feature.get('properties') or {}
    coord = (feature.get('geometry') or {}).get('coordinates') or []
    if len(coord) != 2 or not _coords_ok(coord[1], coord[0]):
        return None
    street = prop.get('street') or prop.get('name') or ''
    if not same_road(a['street'], street):
        return None
    if a['number'] is not None:
        if str(prop.get('housenumber') or '') != str(a['number']):
            return None
    elif a['corner']:
        # Photon no informa ambas calles de una intersección de forma confiable.
        return None
    else:
        return None
    place = normalize(' '.join(str(prop.get(k,'') or '') for k in ('city','district','county','state')))
    if a['locality'] or a['municipality']:
        if not any((('buenos aires' in place) if normalize(v) in ('caba','capital federal','ciudad autonoma de buenos aires') else normalize(v) in place)
                   for v in (a['locality'],a['municipality']) if v):
            return None
    return {'lat':str(coord[1]),'lon':str(coord[0]),
            'display_name':', '.join(filter(None,[f"{street} {a['number']}", prop.get('city'),prop.get('state')])),
            'address':{'road':street,'house_number':str(a['number']),
                       'suburb':prop.get('city') or a['locality'],
                       'county':prop.get('county') or a['municipality'],
                       'state':prop.get('state') or a['province']},
            'origin':'OSM Photon · dirección verificada',
           }


def _http_json(fetch, url, params, user_agent, timeout=(3,7)):
    response = fetch(url, params=params,
                     headers={'User-Agent':user_agent,'Accept':'application/json',
                              'Accept-Language':'es-AR,es;q=0.9'},
                     timeout=timeout)
    response.raise_for_status()
    return response.json()


def _load_cache(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _store_cache(path, data):
    try:
        p=Path(path)
        temp=p.with_name(p.name+'.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(p)
    except OSError:
        pass  # No bloquear el análisis por no poder guardar la caché.


def locate(address, *, case=None, fetch=requests.get, user_agent='AsistenteExpansionRetail/1.1',
           nominatim_url='https://nominatim.openstreetmap.org/search',
           nominatim_slot=None, cache_path=None, errors=None):
    """Devuelve un dict de geocodificación verificada o None. Errores quedan registrados.

    Una respuesta vacía NO se guarda: volver a intentar tiene que poder funcionar.
    No inventa coordenadas cuando los tres proveedores fallan.
    """
    a = extract_address(address, case)
    failures = []
    if errors is None: errors = failures
    cache = _load_cache(cache_path) if cache_path else {}
    key = normalize(address)
    cached = cache.get(key)
    if isinstance(cached,dict) and _coords_ok(cached.get('lat'),cached.get('lon')):
        return cached
    if not a['street'] or not (a['number'] or a['corner']):
        return None

    # 1) Fuente oficial Argentina, compatible con numeración y esquinas.
    georef_params={'direccion':a['raw'],'provincia':a['province'] or 'Buenos Aires','max':5}
    if a['municipality']:
        georef_params['departamento']=a['municipality']
    georef_urls=[GEOREF_API]
    if '/v2.0/' in GEOREF_API:
        georef_urls.append('https://apis.datos.gob.ar/georef/api/direcciones')
    found=None
    for index,endpoint in enumerate(georef_urls):
        try:
            info=_http_json(fetch,endpoint,georef_params,user_agent)
            for item in (info.get('direcciones') or []):
                found=_from_georef(item,a,case)
                if found:break
            if found:break
            # Consultar v1 sólo si v2 no respondió correctamente.
            break
        except (requests.RequestException, ValueError, KeyError) as exc:
            errors.append(f'Georef: {type(exc).__name__} ({str(exc)[:90]})')
            if index == 0 and len(georef_urls)>1:
                continue

    # 2) Nominatim: máximo una solicitud por segundo y encabezado propio.
    if not found:
        if nominatim_slot:
            nominatim_slot()
        params={'q':address,'format':'jsonv2','addressdetails':1,'limit':5,'countrycodes':'ar'}
        try:
            choices=_http_json(fetch,nominatim_url,params,user_agent)
            for item in choices if isinstance(choices,list) else []:
                found=_from_nominatim(item,a,case)
                if found:break
        except (requests.RequestException, ValueError) as exc:
            errors.append(f'Nominatim: {type(exc).__name__} ({str(exc)[:90]})')

    # 3) Photon sólo para direcciones numeradas; valida calle, altura y jurisdicción.
    if not found and a['number'] is not None:
        search = ', '.join(filter(None,[f"{a['street']} {a['number']}",a['locality'],a['municipality'],a['province']]))
        try:
            info=_http_json(fetch,PHOTON_API,{'q':search,'limit':8,'lang':'es'},user_agent)
            for item in (info.get('features') or []):
                found=_from_photon(item,a,case)
                if found:break
        except (requests.RequestException, ValueError) as exc:
            errors.append(f'Photon: {type(exc).__name__} ({str(exc)[:90]})')

    if found and cache_path:
        cache[key]=found
        _store_cache(cache_path,cache)
    return found
