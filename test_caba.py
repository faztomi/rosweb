from unittest.mock import patch
from pathlib import Path
import caba_censo
from engine import population_at, poi_classify


def geojson_caba():
    return {'type':'FeatureCollection', 'features':[{'type':'Feature',
        'geometry':{'type':'Polygon','coordinates':[[[-58.43,-34.63],[-58.37,-34.63],[-58.37,-34.57],[-58.43,-34.57],[-58.43,-34.63]]]},
        'properties':{'cpr':'02','provincia':'Ciudad Autónoma de Buenos Aires','Población_2022':28000}}]}

class FakeResult:
    def raise_for_status(self):pass
    def json(self):return geojson_caba()

def test_caba_radio_api_y_geometria_sin_subir_archivos():
    caba_censo.population_caba_at.cache_clear()
    with patch.object(caba_censo.requests,'get',return_value=FakeResult()) as get:
        p=population_at(-34.60,-58.40)
    assert p is not None and p[800]>p[400]>0 and p['coverage_800']>=90
    assert 'CABA' in p['fuente']
    assert get.call_args.kwargs['params']['outFields'].find('Población_2022')>=0

def test_no_acepta_poblacion_ajena_a_caba():
    p=geojson_caba();p['features'][0]['properties']['cpr']='06';p['features'][0]['properties']['provincia']='Buenos Aires'
    class Bad: 
        def raise_for_status(self):pass
        def json(self):return p
    with patch.object(caba_censo.requests,'get',return_value=Bad()):
        assert caba_censo.query_caba_polygons(-34.6,-58.4)==[]

def test_no_inventa_valores_si_la_api_no_responde():
    import requests
    caba_censo.population_caba_at.cache_clear()
    with patch.object(caba_censo.requests,'get',side_effect=requests.Timeout):
        assert caba_censo.population_caba_at(-34.6,-58.4) is None

def test_osm_excluye_sucursal_de_red_por_coordenadas_sin_importar_cadena():
    elements=[{'type':'node','id':1,'lat':-34.60001,'lon':-58.40001,'tags':{'shop':'supermarket','name':'Cualquier supermercado'}},
              {'type':'node','id':2,'lat':-34.601,'lon':-58.40001,'tags':{'shop':'supermarket','name':'Competidor'}}]
    r=poi_classify(elements,own_brand='',own_locations=[(-34.60,-58.40)])
    assert len(r['own_osm'])==1 and len(r['competitors'])==1

def test_interfaz_no_exige_dia_ni_bloquea_por_censo_pba_ausente():
    txt=Path(__file__).with_name('app.py').read_text()
    assert 'RED_DIA' not in txt
    assert 'if not CENSUS.is_file()' not in txt
    assert 'Subí las sucursales de tu negocio' in txt
