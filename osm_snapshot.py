"""Local OpenStreetMap snapshot for ROS.

The public evaluation never calls Overpass.  A scheduled build job downloads
Geofabrik's complete Argentina extract and saves selected features as a
verified SQLite spatial index, split into GitHub-safe chunks.
"""
from __future__ import annotations
import gzip
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
MANIFEST = DATA / 'osm_snapshot_manifest.json'
LEGACY = DATA / 'osm_pba_caba.sqlite.gz'
RADIUS_POI = 700
RADIUS_ROAD = 450


def _verified_archive():
    """Return compressed file path and fingerprint only if every part verifies."""
    if not MANIFEST.is_file():
        if LEGACY.is_file() and LEGACY.stat().st_size > 500:
            return LEGACY, str(LEGACY.stat().st_size) + '_' + str(LEGACY.stat().st_mtime_ns)
        return None, None
    try:
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
        if manifest.get('format') != 'ros-sqlite-gzip-v1':return None, None
        parts = manifest.get('parts')
        if not isinstance(parts,list) or not parts or len(parts)>100:return None, None
        digest = manifest.get('sha256','')
        if not isinstance(digest,str) or len(digest)!=64:return None,None
        target = Path(tempfile.gettempdir()) / ('ros_osm_' + digest[:20] + '.sqlite.gz')
        if target.exists() and target.stat().st_size == int(manifest['bytes']):
            return target, digest
        temp = target.with_suffix('.working')
        h=hashlib.sha256()
        size=0
        with temp.open('wb') as dest:
            for name in parts:
                if not isinstance(name,str) or not name.startswith('osm_part_') or '/' in name or '\\' in name:
                    raise ValueError('Invalid snapshot part')
                part=DATA/name
                if not part.is_file():raise FileNotFoundError(str(part))
                with part.open('rb') as src:
                    while True:
                        data=src.read(2*1024*1024)
                        if not data:break
                        h.update(data);size+=len(data);dest.write(data)
        if h.hexdigest()!=digest or size!=int(manifest['bytes']):
            temp.unlink(missing_ok=True)
            return None,None
        os.replace(temp,target)
        return target,digest
    except (OSError,UnicodeError,ValueError,KeyError,TypeError,OverflowError):
        return None,None


@lru_cache(maxsize=1)
def database_path():
    archive,fingerprint=_verified_archive()
    if archive is None:return None
    target=Path(tempfile.gettempdir())/('ros_osm_db_'+fingerprint[:24]+'.sqlite')
    try:
        if not target.exists() or target.stat().st_size<100000:
            temp=target.with_suffix('.partial')
            with gzip.open(archive,'rb') as src,temp.open('wb') as dst:
                while True:
                    data=src.read(2*1024*1024)
                    if not data:break
                    dst.write(data)
            os.replace(temp,target)
        with sqlite3.connect(f'file:{target}?mode=ro',uri=True,timeout=7) as db:
            meta=dict(db.execute('SELECT key,value FROM meta').fetchall())
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if meta.get('complete')!='1' or not {'places','places_geo','meta'}<=tables:
                return None
            if int(meta.get('features','0'))<10000:return None
        return target
    except (OSError,EOFError,ValueError,sqlite3.Error):
        return None


@lru_cache(maxsize=1)
def snapshot_metadata():
    path=database_path()
    if path is None:return None
    try:
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True,timeout=5) as db:
            return dict(db.execute('SELECT key,value FROM meta').fetchall())
    except sqlite3.Error:return None


def local_osm(lat:float,lon:float):
    """Return OSM features in 700m, or None when verified snapshot is absent.

    An empty list is *not* evidence of zero real-world businesses. Source is
    a full OSM regional extract but OSM coverage is inherently voluntary.
    """
    path=database_path();meta=snapshot_metadata()
    if path is None or meta is None:
        return None,'Base geográfica todavía no disponible'
    try:
        bounds=[float(meta[k]) for k in ('min_lat','max_lat','min_lon','max_lon')]
        if not(bounds[0]+.01<lat<bounds[1]-.01 and bounds[2]+.01<lon<bounds[3]-.01):
            return None,'Fuera de la cobertura cartográfica'
        dlat=800/111320
        dlon=800/(111320*max(.4,math.cos(math.radians(lat))))
        sql='''SELECT p.osm_ref,p.lat,p.lon,p.shop,p.amenity,p.highway,p.railway,
                 p.public_transport,p.brand,p.name,p.operator,p.kind
                 FROM places_geo r JOIN places p ON p.id=r.id
                 WHERE r.max_lon>=? AND r.min_lon<=? AND r.max_lat>=? AND r.min_lat<=?'''
        from engine import geo_dist
        elems=[]
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True,timeout=10) as db:
            for ref,pla,plo,shop,amen,high,rail,pt,brand,name,operator,kind in db.execute(sql, (lon-dlon,lon+dlon,lat-dlat,lat+dlat)):
                if geo_dist(lat,lon,pla,plo)>(RADIUS_ROAD if kind=='road' else RADIUS_POI):continue
                tags={k:v for k,v in (
                    ('shop',shop),('amenity',amen),('highway',high),('railway',rail),
                    ('public_transport',pt),('brand',brand),('name',name),('operator',operator)) if v}
                if tags:elems.append({'id':ref,'type':'way' if kind=='road' else 'node',
                                      'lat':pla,'lon':plo,'tags':tags})
        return elems, 'OSM / Geofabrik · edición '+meta.get('downloaded_at','s/f')[:10]
    except (sqlite3.Error,ValueError,OverflowError):
        return None,'Error al leer la base cartográfica'
