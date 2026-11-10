"""Build a local, bounded OSM POI+roads SQLite RTree from Geofabrik Argentina.

Intended to run once/month on GitHub Actions (large download). NOT on a web visitor
request. Features are OSM *records*, never asserted to be every real business.
"""
from __future__ import annotations
import datetime as dt
import gzip
import math
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import requests

try:
    import osmium
except ImportError as e:
    raise SystemExit('Run: pip install osmium requests') from e

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
DATA.mkdir(exist_ok=True)
DEST=DATA/'osm_pba_caba.sqlite.gz'
PBF_FILES=[Path('/tmp/ros_pba.osm.pbf'),Path('/tmp/ros_caba.osm.pbf')]
TEMP=Path('/tmp/ros_osm_2022.sqlite')
SOURCES=[
    'https://download.openstreetmap.fr/extracts/south-america/argentina/buenos_aires-latest.osm.pbf',
    'https://download.openstreetmap.fr/extracts/south-america/argentina/buenos_aires_city-latest.osm.pbf',
]
# Bounded regional coverage: CABA + PBA (bbox includes some neighboring strips).
MIN_LAT,MAX_LAT,MIN_LON,MAX_LON=-41.6,-33.1,-63.9,-56.0


def download():
    """Use smaller current PBA and CABA regional OSM extracts instead of the country PBF.

    The OSM data source is a public French mirror; no user key is necessary.
    Avoid publishing when either download fails, rather than silently losing CABA.
    """
    for url, target, min_size in zip(SOURCES,PBF_FILES,[5_000_000,1_000_000]):
        if target.is_file() and target.stat().st_size>=min_size:
            continue
        print('Downloading:',url,flush=True)
        temp=target.with_suffix('.part')
        with requests.get(url,stream=True,timeout=(25,90),headers={
              'User-Agent':'ROS-Web-geographic-snapshot-builder/2.0 (https://rosweb.streamlit.app)'}) as r:
            r.raise_for_status()
            with temp.open('wb') as f:
                for chunk in r.iter_content(1<<20):
                    if chunk: f.write(chunk)
        if temp.stat().st_size<min_size:
            raise RuntimeError('OSM extract too small; refusing to publish '+url)
        temp.replace(target)


class Writer(osmium.SimpleHandler):
    def __init__(self, db):
        super().__init__()
        self.db=db
        self.buf=[]
        self.count=0
        self.road_cells=set()

    def keep_point(self,lat,lon):
        return MIN_LAT<=lat<=MAX_LAT and MIN_LON<=lon<=MAX_LON

    def record(self,ref,lat,lon,tags,kind):
        if not self.keep_point(lat,lon):return
        if kind=='road':
            # Count at most one road type per ~100m cell to prevent huge PBF
            # geometry datasets and unstable segment-based counts.
            k=(round(lat,3),round(lon,3),str(tags.get('highway','')))
            if k in self.road_cells:return
            self.road_cells.add(k)
        row=(ref,lat,lon,str(tags.get('shop',''))[:50],str(tags.get('amenity',''))[:50],
             str(tags.get('highway',''))[:50],str(tags.get('railway',''))[:50],
             str(tags.get('public_transport',''))[:50],str(tags.get('brand',''))[:100],
             str(tags.get('name',''))[:200],str(tags.get('operator',''))[:100],kind)
        self.buf.append(row)
        if len(self.buf)>=3000:self.flush()

    def flush(self):
        if not self.buf:return
        self.db.executemany('''INSERT INTO places (osm_ref,lat,lon,shop,amenity,highway,
                   railway,public_transport,brand,name,operator,kind)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', self.buf)
        self.count+=len(self.buf)
        self.buf=[]
        if self.count%60000<3000: print('Features:',self.count,flush=True)

    def classify(self,entity,is_way=False):
        tags=entity.tags
        shop=tags.get('shop')
        amen=tags.get('amenity')
        highway=tags.get('highway')
        railway=tags.get('railway')
        pt=tags.get('public_transport')
        wanted_amen={'school','kindergarten','hospital','clinic','doctors','dentist',
          'pharmacy','bank','atm','university','college','post_office',
          'marketplace','library','community_centre',
          'restaurant','cafe','fast_food','bar','pub','food_court'}
        transport=(highway=='bus_stop' or pt in {'platform','stop_position','station'} or
                   railway in {'station','halt','tram_stop'})
        roads={'primary','secondary','tertiary','residential','unclassified','living_street',
               'pedestrian','footway','service'}
        isroad=(highway in roads and is_way)
        if not (shop or amen in wanted_amen or transport or isroad): return None
        # A location can be both a shop and an amenity; one record captures both.
        return 'road' if isroad and not (shop or amen in wanted_amen or transport) else 'poi'

    @staticmethod
    def coordinates_of_way(w):
        # Locations provided by SimpleHandler.apply_file(locations=True).
        coords=[]
        for n in w.nodes:
            try:
                if n.location.valid():coords.append((n.location.lat,n.location.lon))
            except (RuntimeError,ValueError):pass
        if not coords:return None
        # Central node avoids centroid of huge multiline streets outside the location.
        return coords[len(coords)//2]

    def node(self,n):
        kind=self.classify(n)
        if not kind:return
        try:
            if n.location.valid():self.record('n'+str(n.id),n.location.lat,n.location.lon,n.tags,kind)
        except (ValueError,RuntimeError):pass

    def way(self,w):
        kind=self.classify(w,is_way=True)
        if not kind:return
        if kind!='road':
            coord=self.coordinates_of_way(w)
            if coord:self.record('w'+str(w.id),*coord,w.tags,kind)
            return
        # Roads are extended geometries. One point at a road's midpoint
        # undercounts access across neighborhoods; sample every ~140 m.
        coords=[]
        for node in w.nodes:
            try:
                if node.location.valid():coords.append((node.location.lat,node.location.lon))
            except (ValueError,RuntimeError):pass
        for i,(lat,lon) in enumerate(coords):
            if i==0:
                self.record('r'+str(w.id)+'_0',lat,lon,w.tags,'road')
                continue
            a,b=coords[i-1]
            dy=(lat-a)*111320
            dx=(lon-b)*111320*math.cos(math.radians(lat))
            steps=max(1,min(300,math.ceil(math.hypot(dx,dy)/140)))
            for j in range(1,steps+1):
                x=j/steps
                self.record('r'+str(w.id)+'_'+str(i)+'_'+str(j),a+x*(lat-a),b+x*(lon-b),w.tags,'road')


def build():
    download()
    if TEMP.exists():TEMP.unlink()
    db=sqlite3.connect(TEMP)
    db.execute('PRAGMA journal_mode=OFF')
    db.execute('PRAGMA synchronous=OFF')
    db.execute('CREATE TABLE places(id INTEGER PRIMARY KEY,osm_ref TEXT,lat REAL,lon REAL,shop TEXT, amenity TEXT,highway TEXT,railway TEXT, public_transport TEXT,brand TEXT,name TEXT,operator TEXT,kind TEXT)')
    db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)')
    proc=Writer(db)
    print('Reading OSM nodes and ways', flush=True)
    for pbf in PBF_FILES:
        print('Processing',pbf.name,flush=True)
        proc.apply_file(str(pbf),locations=True,idx='flex_mem')
    proc.flush()
    if proc.count < 40000:
        raise RuntimeError(f'Only {proc.count} records; refusing to publish incomplete OSM data')
    print('Building SQLite RTree for',proc.count,'features',flush=True)
    db.execute('CREATE VIRTUAL TABLE places_geo USING rtree(id,min_lon,max_lon,min_lat,max_lat)')
    db.execute('INSERT INTO places_geo SELECT id,lon,lon,lat,lat FROM places')
    vals={'complete':'1', 'source':'© OpenStreetMap contributors / Geofabrik',
          'downloaded_at':dt.datetime.now(dt.timezone.utc).isoformat(),
          'features':str(proc.count),'min_lat':str(MIN_LAT),'max_lat':str(MAX_LAT),
          'min_lon':str(MIN_LON),'max_lon':str(MAX_LON),'method':'OSM node+way, roads sampled ~140m and deduplicated ~100m cell'}
    db.executemany('INSERT INTO meta VALUES (?,?)',vals.items())
    db.commit()
    ok=db.execute('PRAGMA quick_check').fetchone()[0]
    db.close()
    if ok!='ok':raise RuntimeError('SQLite integrity failed: '+str(ok))
    temp_gz=DEST.with_suffix('.tmp')
    with TEMP.open('rb') as src, gzip.open(temp_gz,'wb',compresslevel=6) as dst:
        while True:
            chunk=src.read(2<<20)
            if not chunk:break
            dst.write(chunk)
    # GitHub refuses a single file >=100 MiB. Split the compressed stream into
    # fixed chunks rather than silently truncating OSM coverage.
    digest=hashlib.sha256()
    parts=[];size=0
    with temp_gz.open('rb') as src:
        for n in range(100):
            b=src.read(35*1024*1024)
            if not b:break
            part='osm_part_%03d.bin'%n
            (DATA/part).write_bytes(b)
            digest.update(b);size+=len(b);parts.append(part)
        if src.read(1):raise RuntimeError('Snapshot > 3.5GB. Cannot publish')
    manifest={'format':'ros-sqlite-gzip-v1','parts':parts,
              'sha256':digest.hexdigest(),'bytes':size,'features':proc.count,
              'downloaded_at':vals['downloaded_at'],'source':SOURCES}
    (DATA/'osm_snapshot_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    # Old parts are not used and should not stay in the repository.
    for file in DATA.glob('osm_part_*.bin'):
        if file.name not in parts:file.unlink()
    temp_gz.unlink()
    print('Snapshot ready:',len(parts),'parts; features:',proc.count,'compressed bytes:',size,flush=True)

if __name__=='__main__':build()
