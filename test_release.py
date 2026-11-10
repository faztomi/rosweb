"""Second verification pass for web release: no live network is mocked as verified."""
import gzip,sqlite3,json
from pathlib import Path
from unittest.mock import patch
import pytest
import engine,services,osm_snapshot
from scripts.build_osm_snapshot import WriterBase

@pytest.fixture
def fake_snapshot(tmp_path):
    raw=tmp_path/'map.sqlite'
    conn=sqlite3.connect(raw)
    conn.execute('CREATE TABLE places(id INTEGER PRIMARY KEY,osm_ref TEXT,lat REAL,lon REAL,shop TEXT,amenity TEXT,highway TEXT,railway TEXT,public_transport TEXT,brand TEXT,name TEXT,operator TEXT,kind TEXT)')
    conn.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
    conn.execute('CREATE VIRTUAL TABLE places_geo USING rtree(id,min_lon,max_lon,min_lat,max_lat)')
    writer=WriterBase(conn)
    lat,lon=-34.7700,-58.4000
    for i in range(11000):
        # A complete regional extract normally contains many different features.
        # This is a deterministic fixture to verify the SQLite read path.
        if i < 100:
            x=lat+(i%10-5)*.00017
            y=lon+(i//10-5)*.00017
            tags=({'shop':'bakery'} if i<25 else {'amenity':'cafe'} if i<40 else
                  {'amenity':'school'} if i<60 else {'highway':'bus_stop'} if i<75 else
                  {'highway':'residential'})
            writer.point(f'poi_{i}',x,y,tags,'road' if i>=75 else 'poi')
        else:
            writer.point(f'junk_{i}',lat+.06+(i%100)*.00001,lon+.06+(i%100)*.00001,{'shop':'bakery'},'poi')
    writer.flush()
    assert writer.count>=10900
    conn.execute('INSERT INTO places_geo SELECT id,lon,lon,lat,lat FROM places')
    conn.executemany('INSERT INTO meta VALUES(?,?)',{
        'complete':'1','features':str(writer.count),'source':'OSM fixture',
        'min_lat':'-34.85','max_lat':'-34.4','min_lon':'-58.85','max_lon':'-58.12',
        'downloaded_at':'2026-11-10T12:00:00Z'}.items())
    conn.commit(); conn.close()
    archive=tmp_path/'osm_buenosaires.sqlite.gz'
    with raw.open('rb') as r,gzip.open(archive,'wb') as f:f.write(r.read())
    with patch.object(osm_snapshot,'DATA',tmp_path),patch.object(osm_snapshot,'LEGACY',archive),patch.object(osm_snapshot,'MANIFEST',tmp_path/'missing.json'):
        osm_snapshot.database_path.cache_clear()
        yield
        osm_snapshot.database_path.cache_clear()


def test_real_census_pba():
    assert engine.CENSUS.is_file()
    index=engine.census_index()
    assert index is not None and len(index[0])>23000
    for lat,lon in [(-34.77,-58.40),(-34.92,-57.95)]:
        population=engine.population_at(lat,lon)
        assert population and 1_000<population[800]<150_000
        assert population['coverage_800']>=90


def test_local_map_and_real_census_full_calculation_without_branches(fake_snapshot):
    data,source=osm_snapshot.local_osm(-34.7700,-58.4000)
    assert data and len(data)>=50 and source.startswith('OSM / BBBike')
    result=engine.compute_result(-34.77,-58.40,osm=data,branches=[],
                                 osm_verified_snapshot=True)
    assert result['ready'] is True and 0<=result['score']<=100
    assert all(v is not None for v in result['scores'].values())


def test_local_map_and_real_census_full_calculation_with_branches(fake_snapshot):
    data,_=osm_snapshot.local_osm(-34.77,-58.4)
    branches=[{'Nombre':'Sucursal 1','Latitud':'-34.77','Longitud':'-58.402'},
              {'Nombre':'Sucursal sin coordenadas','Latitud':'','Longitud':''}]
    result=engine.compute_result(-34.77,-58.4,osm=data,branches=branches,
                                 osm_verified_snapshot=True)
    assert result['ready'] and result['red']['proximas_1500'] >= 1


def test_wrong_city_with_matching_street_and_number_is_rejected():
    query='Ioria 355, Lomas de Zamora, Buenos Aires'
    fake={'lat':-34.89,'lon':-58.38,'label':'Loria 355, Glew, Almirante Brown',
          'street':'Loria','house_number':'355','locality':'Glew',
          'county':'Almirante Brown','region':'Buenos Aires'}
    assert services._quality(query,fake)[0] == -1
    assert not services._district_match(query,fake)
    true={'lat':-34.77,'lon':-58.4,'label':'Mariano S. Ioria 355, Lomas de Zamora',
          'street':'Mariano S. Ioria','house_number':'355','locality':'Lomas de Zamora',
          'county':'Lomas de Zamora','region':'Buenos Aires'}
    assert services._quality(query,true)[0] >= 4
    assert services._district_match(query,true)


def test_network_optional_and_bad_osm_no_score():
    result=engine.compute_result(-34.77,-58.4,osm=None,branches=[],population=engine.population_at(-34.77,-58.4))
    assert result['score'] is None and not result['ready']


def test_builder_classification():
    assert WriterBase.kind({'shop':'supermarket'})=='poi'
    assert WriterBase.kind({'amenity':'restaurant'})=='poi'
    assert WriterBase.kind({'highway':'primary'},is_way=True)=='road'
    assert WriterBase.kind({'railway':'station'})=='poi'
    assert WriterBase.kind({'highway':'residential'}) is None


def test_builder_end_to_end_from_fake_pbf(tmp_path, monkeypatch):
    """Run the entire SQLite builder without network with an osmium-compatible fixture."""
    import types,sys
    from scripts import build_osm_snapshot as builder
    class Location:
        def __init__(self,lat,lon):self.lat=lat;self.lon=lon
        def valid(self):return True
    class Node:
        def __init__(self,i,lat,lon,tags):
            self.id=i;self.location=Location(lat,lon);self.tags=tags
    class SimpleHandler:
        def apply_file(self,path,locations=True,idx=None):
            for i in range(11000):
                a=-34.77 + ((i%15)-7)*.00008 if i<140 else -34.66+(i%100)*.00001
                b=-58.40 + ((i%10)-5)*.00008 if i<140 else -58.44
                tags={'shop':'bakery'} if i<40 else {'amenity':'school'} if i<70 else {'highway':'bus_stop'} if i<90 else {'shop':'books'}
                self.node(Node(i,a,b,tags))
    monkeypatch.setitem(sys.modules,'osmium',types.SimpleNamespace(SimpleHandler=SimpleHandler))
    monkeypatch.setattr(builder,'DATA',tmp_path)
    monkeypatch.setattr(builder,'PBF',tmp_path/'sample.pbf')
    monkeypatch.setattr(builder,'TEMP',tmp_path/'build.sqlite')
    (tmp_path/'sample.pbf').write_bytes(b'FAKE'*2_100_000)
    count=builder.build()
    assert count>=10000
    with patch.object(osm_snapshot,'DATA',tmp_path),patch.object(osm_snapshot,'LEGACY',tmp_path/'osm_buenosaires.sqlite.gz'),patch.object(osm_snapshot,'MANIFEST',tmp_path/'osm_snapshot_manifest.json'):
        osm_snapshot.database_path.cache_clear()
        rows,source=osm_snapshot.local_osm(-34.77,-58.40)
        assert rows is not None and len(rows)>=50 and 'BBBike' in source
        assert engine.compute_result(-34.77,-58.4,osm=rows,population=engine.population_at(-34.77,-58.4),osm_verified_snapshot=True)['ready']
        osm_snapshot.database_path.cache_clear()
