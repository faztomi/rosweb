import json
from pathlib import Path
from io import BytesIO
from unittest.mock import patch
import pandas as pd
from engine import population_at, demand_score, compute_result, geo_dist, poi_classify
from services import read_network,parse_embedded_coords,valid_latlon,_requested_house,_quality,geocode_network_georef
from tiles import xy,tile_latlon,read_tiles


def test_censo_carga_y_pop_interior():
    x=population_at(-34.77,-58.40)
    assert x and x[400]>0 and x[800]>x[400] and x[1200]>x[800]
    assert x['coverage_800'] >= 95
    assert 0 <= demand_score(x) <= 100

def test_censo_caba_consulta_capas_2022():
    with patch('engine.population_caba_at', return_value={400:2000,800:7000,1200:14000,'coverage_800':100,'fuente':'CABA'}) as mocked:
        assert population_at(-34.60,-58.42)[800] == 7000
    assert mocked.called

def test_no_inventa_sin_osm():
    res=compute_result(-34.77,-58.40,None,[])
    assert res['coverage']==0
    assert not res['ready']
    assert res['score'] is None
    assert all(v is None for v in res['scores'].values())


def test_cinco_componentes_con_fuente_osm():
    fake=[{'type':'node','id':i,'tags':{'shop':'bakery'}} for i in range(12)]
    fake += [{'type':'node','id':100+i,'tags':{'highway':'bus_stop'}} for i in range(6)]
    fake += [{'type':'node','id':200+i,'tags':{'amenity':'school'}} for i in range(8)]
    fake += [{'type':'way','id':300+i,'tags':{'highway':'tertiary'}} for i in range(12)]
    fake += [{'type':'node','id':400,'tags':{'shop':'supermarket','name':'Mercado ABC'}}]
    network=[{'Nombre':'Otro local','Latitud':-34.7703,'Longitud':-58.4100}]
    res=compute_result(-34.77,-58.40,fake,network)
    assert res['coverage']==100 and res['ready']
    assert all(v is not None for v in res['scores'].values())
    assert res['osm']['supermercados_competidores']==1
    assert 0<=res['score']<=100


def test_con_pocos_comercios_no_producir_falso_score():
    tiny=[{'type':'node','tags':{'shop':'bakery'}},
          {'type':'node','tags':{'shop':'supermarket'}},
          {'type':'way','tags':{'highway':'tertiary'}}]
    net=[{'Latitud':-34.7702,'Longitud':-58.4201}]
    result=compute_result(-34.77,-58.40,tiny,net)
    assert result['score'] is None and not result['ready']


def test_red_incompleta_no_bloquea_el_score():
    network=[{'Latitud':-34.772,'Longitud':-58.412},
             {'Latitud':None,'Longitud':None}]
    res=compute_result(-34.77,-58.4,[],network)
    assert 'network' not in res['issues']

def test_no_competencia_de_cadena_propia():
    data=[{'tags':{'shop':'supermarket','name':'Supermercado DÍA'},'type':'node'},
          {'tags':{'shop':'supermarket','name':'Carrefour'},'type':'node'}]
    x=poi_classify(data,'DIA')
    assert len(x['competitors'])==1 and len(x['own_osm'])==1

def test_coordenadas_maps():
    for val in ['-34.7701,-58.4112','https://www.google.com/maps/@-34.7701,-58.4112,17z',
                'https://maps.google.com/?q=-34.7701,-58.4112']:
        r=parse_embedded_coords(val)
        assert r and abs(r['lat']+34.7701)<.0001

def test_numero_de_calle():
    assert _requested_house('20 de Septiembre 341, Lanús')=='341'
    assert _requested_house('Julio Sánchez Gardel 28, Temperley')=='28'
    assert _requested_house('9 de Julio, CABA') is None

def test_no_aceptar_otra_calle():
    score,quality=_quality('Sánchez Gardel 28, Temperley', {'label':'Lavalle 28, Temperley'})
    assert score==-1

def test_red_csv_aliases():
    df=read_network('red.csv', 'Sucursal;Domicilio;lat;lon\nA;Calle 123;-34.76;-58.39\n'.encode())
    assert df.iloc[0]['Nombre']=='A' and df.iloc[0]['Dirección']=='Calle 123'
    assert valid_latlon(df.iloc[0]['Latitud'],df.iloc[0]['Longitud'])

def test_red_no_debe_geocodificar_si_tiene_coords():
    df=pd.DataFrame([{'Nombre':'A','Dirección':'Calle 1','Latitud':'-34.77','Longitud':'-58.4'}])
    r,n=geocode_network_georef(df)
    assert n==0 and len(r)==1

def test_red_no_cuenta_sucursal_en_el_mismo_sitio():
    res=compute_result(-34.77,-58.4,[],[{'Nombre':'Sitio','Latitud':-34.77,'Longitud':-58.4},{'Nombre':'Otra','Latitud':-34.774,'Longitud':-58.4}])
    assert res['red']['proximas_1500']==1

def test_xy_mercator():
    x,y=xy(-34.77,-58.4)
    assert 0<=x<2**14 and 0<=y<2**14
    la,lo=tile_latlon(int(x),int(y),14,(x%1)*4096,(y%1)*4096,4096)
    assert abs(la+34.77)<1e-6 and abs(lo+58.4)<1e-6

if __name__=='__main__':
    tests=[v for k,v in list(globals().items()) if k.startswith('test_')]
    for t in tests:t();print('OK',t.__name__)
    print('Pruebas:',len(tests))
