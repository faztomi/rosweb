"""Lectura de red propia y alertas de cercanía, independiente del score ROS base."""
from __future__ import annotations
from engine import normal
from geo_extra import meters, chain_is_same


def branches_with_coords(rows):
    result = []
    for r in rows:
        data = {normal(k):v for k,v in r.items()}
        def field(*keys):
            for k in keys:
                v=data.get(normal(k))
                if v is not None and str(v).strip():return v
            return None
        try:
            lat=float(str(field('lat','latitud','latitude')).strip().replace(',','.'))
            lon=float(str(field('lon','lng','longitud','longitude')).strip().replace(',','.'))
            if not (-55<=lat<=-20 and -75<=lon<=-50):continue
        except (ValueError,TypeError,AttributeError):continue
        label=field('nombre','sucursal','direccion','local','punto operacional') or 'Sucursal de la red'
        result.append({'lat':lat,'lon':lon,'name':str(label)})
    return result


def network_analysis(rows, lat, lon, supermarkets=None, chain=''):
    coords = branches_with_coords(rows)
    near = [{**s,'distance_m':round(meters(lat,lon,s['lat'],s['lon']))} for s in coords]
    near.sort(key=lambda x:x['distance_m'])
    # Señal por proximidad, NO estimación de ventas canibalizadas.
    nearest = near[0]['distance_m'] if near else None
    matched = []
    if chain and supermarkets:
        for s in supermarkets:
            if chain_is_same(s.get('brand') or s.get('name'),chain):matched.append(s)
    if nearest is None and matched:
        nearest = min(x['distance_m'] for x in matched)
        identified_by = 'marca OSM (sin cotejo individual)'
    elif nearest is not None:
        identified_by = 'coordenadas aportadas'
    else:
        identified_by = 'sin coordenadas verificables'
    if nearest is None:level='Sin evaluar'
    elif nearest < 400:level='Alta proximidad; revisar canibalización'
    elif nearest < 1000:level='Proximidad media; revisar superposición'
    else:level='Sin proximidad crítica detectada en la red disponible'
    return {'count_loaded':len(rows), 'count_georeferenced':len(coords),
            'nearest_m':nearest,'nearest_source':identified_by,
            'nearby':near[:10], 'own_osm_by_brand':matched[:10], 'cannibalization_signal':level,
            'within_2km':sum(s['distance_m']<=2000 for s in near),
            'within_5km':sum(s['distance_m']<=5000 for s in near),
            'logistics_note':('Proximidad a red operativa: indicador preliminar, no prueba de rutas, tiempos ni costos logísticos.'
                              if near else 'Sin coordenadas en la red: no es posible medir proximidad logística.')}


def label_branches(osm_shops, own_rows):
    coords = branches_with_coords(own_rows)
    out=[]
    for s in osm_shops:
        own = any(meters(s['lat'],s['lon'],t['lat'],t['lon']) <= 65 for t in coords)
        out.append({**s,'in_uploaded_network':own})
    return out
