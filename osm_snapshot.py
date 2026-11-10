"""Verified offline OSM data with automatic first-run bootstrap if missing.

Buenos Aires OSM features are downloaded once from the ready-made BBBike PBF,
not queried from public Overpass for every visitor. Other regions can fall
back to the old V37 Overpass query when no regional snapshot is available.
"""
from __future__ import annotations
import gzip
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
MANIFEST = DATA / 'osm_snapshot_manifest.json'
LEGACY = DATA / 'osm_buenosaires.sqlite.gz'
RADIUS_POI = 700
RADIUS_ROAD = 450


def _verified_archive():
    """Return compressed file path and fingerprint only if every part verifies."""
    # Prefer the new single-file snapshot even if an obsolete manifest survives
    # from a previous GitHub upload.
    if LEGACY.is_file() and LEGACY.stat().st_size > 500:
        return LEGACY, str(LEGACY.stat().st_size) + '_' + str(LEGACY.stat().st_mtime_ns)
    if not MANIFEST.is_file():
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


_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAP_ATTEMPT = 0.

def _bootstrap_if_needed():
    """On first use, try building a local map even if GitHub Actions never ran.

    This can be slow on the first request; later visitor queries use the same DB.
    A failed download is throttled instead of retried on every visitor click.
    """
    global _BOOTSTRAP_ATTEMPT
    if os.getenv('ROS_ALLOW_BOOTSTRAP','1') == '0':return
    if time.monotonic()-_BOOTSTRAP_ATTEMPT < 240:return
    with _BOOTSTRAP_LOCK:
        if _verified_archive()[0] is not None:return
        if time.monotonic()-_BOOTSTRAP_ATTEMPT < 240:return
        _BOOTSTRAP_ATTEMPT = time.monotonic()
        try:
            from scripts.build_osm_snapshot import build
            build()
        except Exception as e:
            # Availability failure is not a score of zero. Keep live V37 fallback.
            import logging
            logging.getLogger('rosweb').warning('OSM bootstrap failed: %s',str(e))

@lru_cache(maxsize=4)
def _unpack_db(archive_text,fingerprint):
    archive=Path(archive_text)
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


def database_path():
    archive,fingerprint=_verified_archive()
    if archive is None:
        _bootstrap_if_needed()
        archive,fingerprint=_verified_archive()
    if archive is None:return None
    return _unpack_db(str(archive),str(fingerprint))


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
    # The bundled BBBike extract covers Buenos Aires metro only. Outside its
    # published footprint, skip a pointless 37MB bootstrap and use V37 fallback.
    if _verified_archive()[0] is None and not (-34.85 < lat < -34.40 and -58.85 < lon < -58.12):
        return None, 'Sin extracto local fuera del AMBA; consulta directa alternativa'
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
        seen_pois=set()
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True,timeout=10) as db:
            for ref,pla,plo,shop,amen,high,rail,pt,brand,name,operator,kind in db.execute(sql, (lon-dlon,lon+dlon,lat-dlat,lat+dlat)):
                if geo_dist(lat,lon,pla,plo)>(RADIUS_ROAD if kind=='road' else RADIUS_POI):continue
                if kind != 'road':
                    if ref in seen_pois: continue
                    seen_pois.add(ref)
                tags={k:v for k,v in (
                    ('shop',shop),('amenity',amen),('highway',high),('railway',rail),
                    ('public_transport',pt),('brand',brand),('name',name),('operator',operator)) if v}
                if tags:elems.append({'id':ref,'type':'way' if kind=='road' else 'node',
                                      'lat':pla,'lon':plo,'tags':tags})
        return elems, 'OSM / BBBike · actualización '+meta.get('downloaded_at','s/f')[:10]
    except (sqlite3.Error,ValueError,OverflowError):
        return None,'Error al leer la base cartográfica'


# Compatibility with source-level tests that reset cached readers.
database_path.cache_clear = _unpack_db.cache_clear
snapshot_metadata.cache_clear = lambda: None
