"""Core deterministic calculations for ROS Simple.
All scores are exploratory, NOT calibrated sales probabilities.
"""
from __future__ import annotations
import gzip, json, math, unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional
import numpy as np
from pyproj import Transformer
from shapely.geometry import shape, Point
from shapely.ops import transform
from shapely.strtree import STRtree

HERE = Path(__file__).resolve().parent
CENSUS = HERE / 'data' / 'censo_pba_2022.geojson.gz'
METRIC_CRS = 'EPSG:32721'  # UTM 21S. PBA; small distortion possible at the extremes.
PROJECT = Transformer.from_crs('EPSG:4326', METRIC_CRS, always_xy=True).transform

WEIGHTS = {'Demanda residencial':35, 'Actividad comercial':25, 'Accesibilidad y transporte':15,
           'Equilibrio de competencia':15, 'Servicios complementarios':10}


def clip(v, lo=0., hi=100.): return max(lo, min(hi, float(v)))


def grow(v, low, high): return clip((float(v)-low)/(high-low)*100)


def geo_dist(lat1, lon1, lat2, lon2):
    r=6371.0088
    p1,p2=math.radians(lat1),math.radians(lat2)
    dp=p2-p1;dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(min(1,math.sqrt(a)))*1000


@lru_cache(maxsize=1)
def census_index():
    """Loads a verified 2022 PBA polygons+population snapshot, once per app process."""
    with gzip.open(CENSUS,'rt',encoding='utf-8') as f: obj=json.load(f)
    polygons=[]; pops=[]
    for f in obj['features']:
        p=f['properties'].get('pop')
        if p is None: continue
        try: pop=float(p)
        except (TypeError,ValueError): continue
        if pop<0: continue
        try:
            geo=transform(PROJECT,shape(f['geometry']))
            if geo.is_empty or geo.area <= 0:continue
        except Exception:continue
        polygons.append(geo);pops.append(pop)
    return polygons,np.asarray(pops,dtype=float),STRtree(polygons)


def population_at(lat,lon,radii=(400,800,1200)):
    if not (-43 <= lat <= -32 and -65 <= lon <= -55):return None
    polygons, pops, tree=census_index()
    point=transform(PROJECT,Point(lon,lat))
    # If coordinate isn't within covered census polygons, return no data,
    # rather than presenting neighboring radios as locally accessible population.
    hits=tree.query(point)
    if not len(hits) or not any(polygons[int(j)].covers(point) for j in hits):return None
    totals={}
    area_covered={}
    for radius in radii:
        circle=point.buffer(radius,resolution=20)
        candidates=tree.query(circle)
        tot=0.; area=0.
        for j in candidates:
            idx=int(j); geo=polygons[idx]
            if not geo.intersects(circle):continue
            a=geo.intersection(circle).area
            if a<=0:continue
            tot+=pops[idx]*a/geo.area
            area+=a
        totals[radius]=round(tot)
        area_covered[radius]=min(1,area/circle.area)
    if area_covered[800]<0.4:return None
    totals['coverage_800']=round(area_covered[800]*100)
    totals['density_800']=round(totals[800]/(math.pi*.8**2))
    return totals


def demand_score(pop):
    """Absolute heuristic thresholds. No unsubstantiated 'probability of success'."""
    if not pop:return None
    # Weights don't double count independent variables: density is supplemental spatial concentration.
    s=0.60*grow(pop[800],2500,28000)+0.25*grow(pop[400],700,10000)+0.15*grow(pop[1200],5500,55000)
    return round(clip(s),1)


def norm(text):
    txt=unicodedata.normalize('NFKD',str(text or '')).encode('ascii','ignore').decode().casefold()
    return ' '.join(''.join(x if x.isalnum() else ' ' for x in txt).split())


def poi_classify(items, own_brand=''):
    """Classify OSM elements into raw evidence counts; results from a successful query only."""
    commercial=[]; amenities=[];transport=[];roads=[];competitors=[];own_osm=[]
    own=norm(own_brand)
    for obj in items:
        tags=obj.get('tags') or {}
        typ=tags.get('shop','')
        amenity=tags.get('amenity','')
        highway=tags.get('highway','')
        railway=tags.get('railway','')
        trans=tags.get('public_transport','')
        brand=norm(' '.join([tags.get('brand',''),tags.get('name',''),tags.get('operator','')]))
        if typ in {'supermarket','convenience','bakery','butcher','greengrocer','chemist','clothes','shoes','variety_store','mall','kiosk','department_store','hardware','florist','beverages','books','electronics'}:
            commercial.append(obj)
        if amenity in {'school','kindergarten','hospital','clinic','doctors','pharmacy','bank','atm','university','post_office','marketplace'}:
            amenities.append(obj)
        if highway=='bus_stop' or railway in {'station','halt','tram_stop'} or trans in {'platform','stop_position','station'}:transport.append(obj)
        if highway in {'primary','secondary','tertiary','residential','pedestrian','footway'} and obj.get('type')=='way': roads.append(obj)
        if typ=='supermarket':
            if own and own in brand:own_osm.append(obj)
            else: competitors.append(obj)
    return dict(commercial=commercial, amenities=amenities,transport=transport,roads=roads,competitors=competitors,own_osm=own_osm)


def osm_scores(classes, population=None, branch_nearby=0):
    """POI features measure recorded establishments, not observed pedestrian traffic."""
    c=len(classes['commercial']);a=len(classes['amenities']);t=len(classes['transport']);r=len(classes['roads'])
    k=len(classes['competitors'])
    # POI presence is a proxy, not a pedestrian or accessibility measurement.
    # Competitor counts are OSM records, not chain market shares.
    sc={
        'Actividad comercial': round(.75*grow(c,1,55)+.25*grow(len([x for x in classes['commercial'] if (x.get('tags') or {}).get('shop') not in {'supermarket','convenience'}]),0,35),1),
        'Accesibilidad y transporte': round(.60*grow(t,0,22)+.40*grow(r,4,75),1),
        'Servicios complementarios':round(grow(a,0,24),1),
    }
    # Demand / supermarket supply: adjust down for nearby own branches, excluding the site itself.
    # Without census we cannot measure this dimension.
    if population:
        pop_per_store=population[800]/max(1,1 + 1.0*k + 0.9*branch_nearby)
        sc['Equilibrio de competencia']=round(clip(.75*grow(pop_per_store,1000,17000)+.25*grow(c,2,40)),1)
    return sc,{'comercios':c,'servicios':a,'paradas':t,'vias':r,'supermercados_terceros':k,'supermercados_propios_osm':len(classes['own_osm'])}


def own_network_summary(lat,lon,branches):
    verified=[]
    for row in branches:
        try:
            a=float(row['Latitud']);b=float(row['Longitud'])
            if not (-56<a<-20 and -74<b<-53):continue
        except (ValueError,TypeError,KeyError):continue
        d=geo_dist(lat,lon,a,b)
        if d<40:continue  # Site itself is not its own competing branch.
        verified.append((d,row.get('Nombre') or row.get('Dirección') or 'Sucursal'))
    verified.sort(key=lambda x:x[0])
    return {'total_verificadas':sum(bool((b.get('Latitud') is not None) and (b.get('Longitud') is not None)) for b in branches),
            'proximas_1500':sum(d<=1500 for d,_ in verified),
            'mas_cercana_m':round(verified[0][0]) if verified else None,
            'lista_cercanas':verified[:5]}


def compute_result(lat,lon, osm=None, branches=None, own_brand=''):
    pop=population_at(lat,lon)
    scores={k:None for k in WEIGHTS}
    if pop:scores['Demanda residencial']=demand_score(pop)
    raw={}
    nearby=own_network_summary(lat,lon,branches or [])
    if osm is not None:
        classes=poi_classify(osm,own_brand=own_brand)
        osm_sc,raw=osm_scores(classes,population=pop,branch_nearby=nearby['proximas_1500'])
        scores.update(osm_sc)
    available=sum(WEIGHTS[k] for k,v in scores.items() if v is not None)
    total=round(sum(scores[k]*WEIGHTS[k] for k in scores if scores[k] is not None)/available,1) if available else None
    status='Completo (heurístico)' if available==100 else 'Parcial (fuentes faltantes)'
    return {'scores':scores,'score':total,'coverage':available,'status':status,'poblacion':pop,'osm':raw,'red':nearby}
