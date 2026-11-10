"""Optional precompute CABA 2022 census polygons from an ArcGIS public layer.
Refuse to publish if population and geometry fail plausibility checks.
"""
import datetime as dt
import gzip
import json
from pathlib import Path
import requests
from shapely.geometry import shape, mapping
from shapely.validation import make_valid

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'data'/'censo_caba_2022.geojson.gz'
API=('https://services1.arcgis.com/gKFHG0iKXPnKkg1l/arcgis/rest/services/'
     'Censo_2010_2022_Comparaciones_Divisiones_Territoriales/FeatureServer/3/query')
BBOX={"xmin":-58.57,"ymin":-34.76,"xmax":-58.30,"ymax":-34.48,"spatialReference":{"wkid":4326}}


def read():
    feature_by_id={}
    for offset in range(0,10000,500):
        params={'f':'geojson','where':'1=1','geometry':json.dumps(BBOX),
          'geometryType':'esriGeometryEnvelope','inSR':4326,'outSR':4326,
          'spatialRel':'esriSpatialRelIntersects',
          'outFields':'OBJECTID,cpr,provincia,Población_2022',
          'resultOffset':offset,'resultRecordCount':500,
          'returnGeometry':'true'}
        r=requests.get(API,params=params,timeout=(15,90))
        r.raise_for_status()
        blob=r.json()
        if not isinstance(blob,dict) or blob.get('error'):
            raise RuntimeError(str(blob.get('error','No GeoJSON')))
        items=blob.get('features')
        if not isinstance(items,list):raise RuntimeError('No GeoJSON features')
        for feature in items:
            p=feature.get('properties') or {}
            name=str(p.get('provincia') or '').lower()
            code=str(p.get('cpr') or '').strip().lstrip('0')
            if not (code=='2' or ('ciudad' in name and 'buenos aires' in name) or name in {'capital federal','caba'}):continue
            try:pop=float(p.get('Población_2022'))
            except (ValueError,TypeError):continue
            if not 0<=pop<=40000:continue
            g=feature.get('geometry')
            if not g:continue
            try:
                geom=shape(g)
                if not geom.is_valid:geom=make_valid(geom)
                geom=geom.simplify(.000016,preserve_topology=True)
                if geom.is_empty or geom.area<=0:continue
            except (TypeError,ValueError):continue
            key=str(p.get('OBJECTID') or len(feature_by_id))
            feature_by_id[key]={'type':'Feature','properties':{'pop':round(pop),'id':key},'geometry':mapping(geom)}
        print('Received',offset+len(items),'records; stored',len(feature_by_id),'CABA polygons',flush=True)
        if len(items)<500:break
    rows=list(feature_by_id.values())
    pop_sum=sum(f['properties']['pop'] for f in rows)
    print('CABA population aggregated',pop_sum,'across',len(rows),'features')
    if len(rows)<800 or not 2_000_000<=pop_sum<=4_500_000:
        raise RuntimeError('CABA layer not credible; refused to save')
    payload={'type':'FeatureCollection','features':rows,'metadata':{
      'source':'Censo 2022, capa comparativa de radios INDEC/Aeroterra',
      'downloaded_at':dt.datetime.now(dt.timezone.utc).isoformat()}}
    tmp=OUTPUT.with_suffix('.tmp')
    with gzip.open(tmp,'wt',encoding='utf-8',compresslevel=7) as f:json.dump(payload,f,ensure_ascii=False,separators=(',',':'))
    tmp.replace(OUTPUT)
    print('CABA snapshot saved',OUTPUT,OUTPUT.stat().st_size)

if __name__=='__main__':read()
