"""Build compact offline OpenStreetMap database for CABA and Buenos Aires metro.

Source is the ready-made BBBike BuenosAires PBF (normally ~37 MB).
Runs in GitHub Actions and also as an automatic first-start fallback if the
published map archive was not committed yet. This avoids querying Overpass on
*every* visitor click. The downloaded map itself is ODbL OpenStreetMap data.
"""
from __future__ import annotations
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
DATA.mkdir(exist_ok=True)
PBF=Path(tempfile.gettempdir())/'ros_buenosaires.osm.pbf'
TEMP=Path(tempfile.gettempdir())/'ros_mapa_ba_build.sqlite'
SOURCE='https://download.bbbike.org/osm/bbbike/BuenosAires/BuenosAires.osm.pbf'
# BBBike's published BuenosAires.poly: W -58.85 to -58.12, S -34.85 to -34.40
MIN_LAT,MAX_LAT,MIN_LON,MAX_LON=-34.85,-34.40,-58.85,-58.12


def download():
    if PBF.is_file() and PBF.stat().st_size>=8_000_000:return
    tmp=PBF.with_suffix('.partial')
    with requests.get(SOURCE,stream=True,timeout=(18,90),headers={
        'User-Agent':'ROS-Territorial-Data-Builder/1.0 (OpenStreetMap extract for ROS)'}) as r:
        r.raise_for_status()
        with tmp.open('wb') as f:
            for chunk in r.iter_content(chunk_size=1024*1024):
                if chunk:f.write(chunk)
    if tmp.stat().st_size<8_000_000:
        raise RuntimeError('Extracto incompleto: descarga menor a 8 MB')
    os.replace(tmp,PBF)


class WriterBase:
    def __init__(self,db):
        self.db=db
        self.buf=[]
        self.count=0
        self.road_cells=set()
    def point(self,ref,lat,lon,tags,kind):
        if not(MIN_LAT<=lat<=MAX_LAT and MIN_LON<=lon<=MAX_LON):return
        if kind=='road':
            key=(round(lat,3),round(lon,3),str(tags.get('highway','')))
            if key in self.road_cells:return
            self.road_cells.add(key)
        row=(ref,lat,lon,str(tags.get('shop',''))[:50],str(tags.get('amenity',''))[:50],
            str(tags.get('highway',''))[:50],str(tags.get('railway',''))[:50],
            str(tags.get('public_transport',''))[:50],str(tags.get('brand',''))[:100],
            str(tags.get('name',''))[:200],str(tags.get('operator',''))[:100],kind)
        self.buf.append(row)
        if len(self.buf)>=4000:self.flush()
    def flush(self):
        if not self.buf:return
        self.db.executemany('''INSERT INTO places (osm_ref,lat,lon,shop,amenity,highway,
            railway,public_transport,brand,name,operator,kind)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',self.buf)
        self.count+=len(self.buf)
        self.buf=[]
    @staticmethod
    def kind(tags,is_way=False):
        shop=tags.get('shop')
        amen=tags.get('amenity')
        highway=tags.get('highway')
        railway=tags.get('railway')
        pt=tags.get('public_transport')
        amenities={'school','kindergarten','hospital','clinic','doctors','dentist',
                   'pharmacy','bank','atm','university','college','post_office',
                   'marketplace','library','community_centre','restaurant','cafe',
                   'fast_food','bar','pub','food_court'}
        transit=(highway=='bus_stop' or pt in {'platform','stop_position','station'} or
                 railway in {'station','halt','tram_stop'})
        isroad=(is_way and highway in {'primary','secondary','tertiary','residential',
                           'unclassified','living_street','pedestrian','footway','service'})
        if not(shop or amen in amenities or transit or isroad):return None
        return 'road' if isroad and not (shop or amen in amenities or transit) else 'poi'


def build():
    try:import osmium
    except ImportError as e:raise RuntimeError('Falta paquete osmium (requirements.txt)') from e
    download()
    TEMP.unlink(missing_ok=True)
    db=sqlite3.connect(TEMP)
    try:
        db.execute('PRAGMA journal_mode=OFF')
        db.execute('PRAGMA synchronous=OFF')
        db.execute('CREATE TABLE places(id INTEGER PRIMARY KEY,osm_ref TEXT,lat REAL,lon REAL,shop TEXT,amenity TEXT,highway TEXT,railway TEXT,public_transport TEXT,brand TEXT,name TEXT,operator TEXT,kind TEXT)')
        db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)')
        class Writer(osmium.SimpleHandler):
            def __init__(self,db):
                super().__init__()
                self.data=WriterBase(db)
            @property
            def count(self):return self.data.count
            def flush(self):self.data.flush()
            def node(self,n):
                typ=self.data.kind(n.tags)
                if typ and n.location.valid():
                    self.data.point('n'+str(n.id),n.location.lat,n.location.lon,n.tags,typ)
            def way(self,w):
                typ=self.data.kind(w.tags,is_way=True)
                if not typ:return
                coords=[]
                for node in w.nodes:
                    try:
                        if node.location.valid():coords.append((node.location.lat,node.location.lon))
                    except (RuntimeError,ValueError):pass
                if not coords:return
                if typ=='poi':
                    lat,lon=coords[len(coords)//2]
                    self.data.point('w'+str(w.id),lat,lon,w.tags,typ)
                else:
                    # Sampling long streets at ~140 m, not one midpoint per road.
                    prev=None
                    for i,(lat,lon) in enumerate(coords):
                        if prev is None:
                            self.data.point(f'r{w.id}_{i}',lat,lon,w.tags,'road')
                            prev=(lat,lon);continue
                        a,b=prev
                        meters=math.hypot((lat-a)*111320,(lon-b)*111320*math.cos(math.radians(lat)))
                        steps=max(1,min(150,math.ceil(meters/140)))
                        for j in range(1,steps+1):
                            x=j/steps
                            self.data.point(f'r{w.id}_{i}_{j}',a+x*(lat-a),b+x*(lon-b),w.tags,'road')
                        prev=(lat,lon)
        handler=Writer(db)
        print('Procesando extracto OSM Buenos Aires',flush=True)
        handler.apply_file(str(PBF),locations=True,idx='flex_mem')
        handler.flush()
        if handler.count<10000:raise RuntimeError(f'Solo {handler.count} registros OSM; no publicar')
        db.execute('CREATE VIRTUAL TABLE places_geo USING rtree(id,min_lon,max_lon,min_lat,max_lat)')
        db.execute('INSERT INTO places_geo SELECT id,lon,lon,lat,lat FROM places')
        meta={'complete':'1','source':'© OpenStreetMap contributors / BBBike',
              'downloaded_at':dt.datetime.now(dt.timezone.utc).isoformat(),
              'features':str(handler.count), 'min_lat':str(MIN_LAT),'max_lat':str(MAX_LAT),
              'min_lon':str(MIN_LON),'max_lon':str(MAX_LON),
              'method':'OSM PBF completo del extracto BBBike; POI y vías muestreadas'}
        db.executemany('INSERT INTO meta VALUES (?,?)',meta.items())
        db.commit()
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    finally:db.close()
    archive=DATA/'osm_buenosaires.sqlite.gz'
    temp=archive.with_suffix('.gz.partial')
    with TEMP.open('rb') as src, gzip.open(temp,'wb',compresslevel=6) as dst:
        while True:
            block=src.read(1024*1024)
            if not block:break
            dst.write(block)
    os.replace(temp,archive)
    # Validate compressed DB before publish: reader checks real integrity too.
    with gzip.open(archive,'rb') as f:assert f.read(16).startswith(b'SQLite format 3')
    # Keep a single-file archive if small, chunk only when Github's single-file limit requires it.
    for stale in DATA.glob('osm_part_*.bin'):stale.unlink()
    (DATA/'osm_snapshot_manifest.json').unlink(missing_ok=True)
    if archive.stat().st_size>22*1024*1024:
        parts=[];sha=hashlib.sha256();total=0
        with archive.open('rb') as f:
            for i in range(100):
                b=f.read(18*1024*1024)
                if not b:break
                part=f'osm_part_{i:03}.bin'
                (DATA/part).write_bytes(b)
                sha.update(b);total+=len(b);parts.append(part)
        (DATA/'osm_snapshot_manifest.json').write_text(json.dumps({
            'format':'ros-sqlite-gzip-v1','parts':parts,'sha256':sha.hexdigest(),
            'bytes':total,'features':handler.count,'downloaded_at':meta['downloaded_at'],
            'source':[SOURCE]},ensure_ascii=False,indent=2),encoding='utf-8')
        archive.unlink()
    print('OSM listo:',handler.count,'elementos',flush=True)
    return handler.count


if __name__=='__main__':build()
