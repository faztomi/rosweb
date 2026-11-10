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
    assert result['coverage']==35
    assert result['scores']['Actividad comercial'] is None


def test_pure_coordinates_work():
    assert parse_embedded_coords('-34.77,-58.4') is not None
