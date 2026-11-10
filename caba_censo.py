"""Censo 2022, radios CABA: consultas geoespaciales bajo demanda.

Fuente: servicio público ArcGIS Online de comparación Censo 2010/2022
(publicación cartográfica Aeroterra con datos INDEC).
No inventar población en caso de error de consulta o cobertura insuficiente.
"""
from __future__ import annotations
from functools import lru_cache
from math import cos, radians
import requests
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform

API = ('https://services1.arcgis.com/gKFHG0iKXPnKkg1l/arcgis/rest/services/'
       'Censo_2010_2022_Comparaciones_Divisiones_Territoriales/FeatureServer/3/query')
PROJ = Transformer.from_crs('EPSG:4326', 'EPSG:32721', always_xy=True).transform


def _in_caba_extent(lat:float,lon:float)->bool:
    # Rectángulo aproximado. Las fronteras se verifican contra la geometría.
    return -34.75 < lat < -34.49 and -58.56 < lon < -58.31


def _properties_pop(props):
    for name in ('Población_2022','Poblacion_2022','poblacion_2022'):
        v=props.get(name)
        if v is not None:
            try:
                v=float(v)
                return v if 0<=v<40000 else None
            except (TypeError,ValueError):return None
    return None


def _is_caba(props):
    province=str(props.get('provincia') or '').casefold()
    code=str(props.get('cpr') or '').strip().lstrip('0')
    return code=='2' or ('ciudad' in province and 'buenos aires' in province) or province.strip() in {'caba','capital federal'}


def query_caba_polygons(lat,lon,request_get=None):
    """Return verified CABA radio polygons, or None if remote source is unavailable."""
    if not _in_caba_extent(lat,lon):return []
    request_get=request_get or requests.get
    delta_lat=1500/111320
    delta_lon=1500/(111320*max(.4,cos(radians(lat))))
    bbox={'xmin':lon-delta_lon,'ymin':lat-delta_lat,'xmax':lon+delta_lon,'ymax':lat+delta_lat,
          'spatialReference':{'wkid':4326}}
    import json
    collected=[]
    for offset in range(0,5000,1000):
        params={'f':'geojson','where':'1=1','geometry':json.dumps(bbox,separators=(',',':')),
                'geometryType':'esriGeometryEnvelope','inSR':'4326','spatialRel':'esriSpatialRelIntersects',
                'outFields':'OBJECTID,cpr,provincia,Población_2022',
                'outSR':'4326','returnGeometry':'true',
                'resultRecordCount':1000,'resultOffset':offset}
        try:
            r=request_get(API,params=params,headers={'User-Agent':'ROS-Localizacion/3.0 (evaluador comercial)'},timeout=(5,15))
            r.raise_for_status()
            response=r.json()
        except (requests.RequestException,ValueError,TypeError):return None
        if not isinstance(response,dict) or 'error' in response or not isinstance(response.get('features'),list):
            return None
        features=response['features']
        for feat in features:
            prop=feat.get('properties') or {}
            pop=_properties_pop(prop)
            if pop is None or not _is_caba(prop):continue
            try:
                geom=transform(PROJ,shape(feat['geometry']))
            except (ValueError,TypeError,KeyError):continue
            if not geom.is_empty and geom.area>0:
                collected.append((geom,pop))
        if len(features)<1000:break
    return collected


@lru_cache(maxsize=128)
def population_caba_at(lat,lon,radii=(400,800,1200)):
    """Area-weighted residential pop; cannot assert quality with missing coverage."""
    if not _in_caba_extent(lat,lon):return None
    polys=query_caba_polygons(lat,lon)
    if not polys:return None
    point=transform(PROJ,Point(lon,lat))
    if not any(geom.covers(point) for geom,pop in polys):return None
    from math import pi
    totals={}
    for r in radii:
        circle=point.buffer(r,resolution=20)
        total=0.; covered=0.
        for geom,pop in polys:
            try:
                intersection=geom.intersection(circle)
                if intersection.is_empty:continue
                area=intersection.area
                total+=pop*area/geom.area
                covered+=area
            except Exception:continue
        coverage=min(1.,covered/circle.area)
        if r==800 and coverage<.90:return None
        totals[r]=round(total)
        if r==800:totals['coverage_800']=round(coverage*100)
    totals['density_800']=round(totals[800]/(pi*.8**2))
    totals['fuente']='Censo 2022 · radios de CABA (consulta cartográfica)'
    return totals
