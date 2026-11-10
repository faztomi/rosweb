"""End-to-end OSM fixture tests. FAKE TEST DATA; NEVER packaged as production map."""
from __future__ import annotations
import gzip
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import osm_snapshot
import engine


def _fixture_db(path:Path):
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE places(id INTEGER PRIMARY KEY,osm_ref TEXT,lat REAL,lon REAL,shop TEXT,amenity TEXT,highway TEXT,railway TEXT,public_transport TEXT,brand TEXT,name TEXT,operator TEXT,kind TEXT)')
    db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
    db.execute('CREATE VIRTUAL TABLE places_geo USING rtree(id,min_lon,max_lon,min_lat,max_lat)')
    base=(-34.77,-58.4)
    rows=[]
    for n in range(1,10002):
        if n<=11: tags=('bakery','','','','','','','','poi')
        elif n<=20:tags=('','school','','','','','','','poi')
        elif n<=28:tags=('','','bus_stop','','','','','','poi')
        elif n<=50:tags=('','','residential','','','','','','road')
        elif n==51:tags=('supermarket','','','','','Carrefour','Carrefour Local','','poi')
        else:tags=('bakery','','','','','','','','poi')
        lat=base[0]+.00010*(n%6) if n<=51 else -35.25
        lon=base[1]+.00008*(n%7) if n<=51 else -59.6
        rows.append((n,'t'+str(n),lat,lon,*tags))
    db.executemany('INSERT INTO places VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
    db.execute('INSERT INTO places_geo SELECT id,lon,lon,lat,lat FROM places')
    db.executemany('INSERT INTO meta VALUES (?,?)',[
        ('complete','1'),('features','10001'),('min_lat','-41.6'),('max_lat','-33.1'),
        ('min_lon','-63.9'),('max_lon','-56.0'),('downloaded_at','2026-11-10')])
    db.commit();db.close()


def test_snapshot_end_to_end_with_manifest_chunks(tmp_path):
    raw=tmp_path/'raw.sqlite';_fixture_db(raw)
    archive=gzip.compress(raw.read_bytes())
    parts=[archive[:len(archive)//2],archive[len(archive)//2:]]
    for i,part in enumerate(parts):
        (tmp_path/('osm_part_%03d.bin'%i)).write_bytes(part)
    (tmp_path/'osm_snapshot_manifest.json').write_text(json.dumps({
        'format':'ros-sqlite-gzip-v1', 'parts':['osm_part_000.bin','osm_part_001.bin'],
        'sha256':hashlib.sha256(archive).hexdigest(),'bytes':len(archive)}))
    old=osm_snapshot.DATA;oldm=osm_snapshot.MANIFEST;oldlegacy=osm_snapshot.LEGACY
    try:
        osm_snapshot.DATA=tmp_path
        osm_snapshot.MANIFEST=tmp_path/'osm_snapshot_manifest.json'
        osm_snapshot.LEGACY=tmp_path/'no_legacy.gz'
        osm_snapshot.database_path.cache_clear();osm_snapshot.snapshot_metadata.cache_clear()
        elements,source=osm_snapshot.local_osm(-34.77,-58.4)
        assert len(elements)==51 and 'OSM' in source
        assert sum((e['tags'].get('shop')=='bakery') for e in elements)==11
        result=engine.compute_result(-34.77,-58.4,osm=elements,branches=[
          {'Dirección':'Sucursal con coordenadas','Latitud':'-34.771','Longitud':'-58.401'},
          {'Dirección':'Sucursal sin coordenadas','Latitud':'','Longitud':''}],
          own_brand='DÍA')
        assert result['ready'] and result['score'] is not None
        assert 0<=result['score']<=100
        assert len(result['scores'])==5
    finally:
        osm_snapshot.DATA=old;osm_snapshot.MANIFEST=oldm;osm_snapshot.LEGACY=oldlegacy
        osm_snapshot.database_path.cache_clear();osm_snapshot.snapshot_metadata.cache_clear()


def test_corrupt_snapshot_fails_closed(tmp_path):
    path=tmp_path/'osm_part_000.bin';path.write_bytes(b'invalid')
    (tmp_path/'osm_snapshot_manifest.json').write_text(json.dumps({
        'format':'ros-sqlite-gzip-v1','parts':[path.name],
        'sha256':'0'*64,'bytes':len(path.read_bytes())}))
    with patch.object(osm_snapshot,'DATA',tmp_path),patch.object(osm_snapshot,'MANIFEST',tmp_path/'osm_snapshot_manifest.json'),patch.object(osm_snapshot,'LEGACY',tmp_path/'missing.gz'):
        osm_snapshot.database_path.cache_clear()
        assert osm_snapshot.database_path() is None
        osm_snapshot.database_path.cache_clear()
