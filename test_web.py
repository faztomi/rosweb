from pathlib import Path
from unittest.mock import patch
import io
import pandas as pd
from services import read_network,valid_latlon,parse_embedded_coords
from engine import compute_result


def test_census_file_is_included():
    assert (Path(__file__).parent/'data'/'censo_pba_2022.geojson.gz').stat().st_size>1_000_000


def test_network_import_is_permitted():
    df=read_network('red.csv','Nombre,Dirección,Latitud,Longitud\nLocal Uno,Calle 1,-34.77,-58.4\n'.encode('utf-8'))
    assert len(df)==1 and valid_latlon(df.iloc[0]['Latitud'],df.iloc[0]['Longitud'])


def test_no_fake_score_with_missing_sources():
    result=compute_result(-34.77,-58.4,osm=None,branches=[])
    assert result['coverage']==0
    assert result['score'] is None
    assert result['scores']['Actividad comercial'] is None


def test_pure_coordinates_work():
    assert parse_embedded_coords('-34.77,-58.4') is not None


def test_missing_census_does_not_crash():
    import engine
    original = engine.CENSUS
    engine.CENSUS = Path('/no-existe/censo_pba_2022.geojson.gz')
    engine.census_index.cache_clear()
    try:
        result = engine.compute_result(-34.77,-58.40,osm=None,branches=[])
        assert result['poblacion'] is None
        assert result['scores']['Demanda residencial'] is None
        assert result['coverage'] == 0
    finally:
        engine.CENSUS = original
        engine.census_index.cache_clear()
