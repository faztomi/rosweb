"""Mocked end-to-end tests. Fixtures are never used for real visitors."""
from __future__ import annotations
import sys
import types
from contextlib import nullcontext
from unittest.mock import patch
import pandas as pd


def _test_georef_exact():
    import services
    def fake_json(url,params,timeout):
        return {'direcciones':[{'nomenclatura':'IORIA 358, Lomas de Zamora, Buenos Aires',
          'ubicacion':{'lat':-34.77,'lon':-58.40}, 'calle':{'nombre':'Ioria'},
          'altura':{'valor':358},'localidad_censal':{'nombre':'Lomas de Zamora'},
          'departamento':{'nombre':'Lomas de Zamora'},'provincia':{'nombre':'Buenos Aires'}}]}
    with patch.object(services,'_json_get',side_effect=fake_json):
        a=services.georef_candidates('Ioria 358, Lomas de Zamora')
        assert a[0]['street']=='Ioria'
        p=services.geocode('Ioria 358, Lomas de Zamora')
        assert p is not None and p['quality']=='Altura exacta'


def _test_no_red_does_not_block():
    # Stand-in for Streamlit's view layer so the real app's pure _evaluate
    # function is exercised without network or any third-party Streamlit UI.
    st=types.ModuleType('streamlit')
    st.query_params={}
    st.session_state={}
    st.set_page_config=lambda **kw:None
    st.markdown=lambda *args,**kw:None
    st.title=lambda *args,**kw:None
    st.container=lambda **kw:nullcontext()
    st.file_uploader=lambda *args,**kw:None
    st.text_input=lambda *args,**kw:''
    st.button=lambda *args,**kw:False
    st.divider=lambda :None
    st.caption=lambda *args,**kw:None
    st.cache_data=lambda **kw: lambda f:f
    sys.modules['streamlit']=st
    import app
    fake_osm=[]
    fake_osm += [{'type':'node','id':i,'tags':{'shop':'bakery'}} for i in range(1,14)]
    fake_osm += [{'type':'node','id':i,'tags':{'amenity':'school'}} for i in range(20,29)]
    fake_osm += [{'type':'node','id':i,'tags':{'highway':'bus_stop'}} for i in range(30,38)]
    fake_osm += [{'type':'way','id':i,'tags':{'highway':'residential'}} for i in range(40,61)]
    fake_osm += [{'type':'node','id':70,'tags':{'shop':'supermarket','name':'Carrefour'}}]
    point={'lat':-34.77,'lon':-58.40,'quality':'Altura exacta','provider':'Georef Argentina'}
    with patch.object(app,'_geocode_cached',return_value=point), patch.object(app,'_osm_cached',return_value=(fake_osm,'OSM prueba')):
        empty=pd.DataFrame(columns=['Nombre','Dirección','Latitud','Longitud','Cadena'])
        a=app._evaluate('Ioria 358, Lomas de Zamora',empty,'')
        assert a['status']=='ok' and a['result']['ready'], a
        assert all(v is not None for v in a['result']['scores'].values())
        assert 0 <= a['result']['score'] <= 100
        red=pd.DataFrame([{'Nombre':'DIA 1','Dirección':'Sin coords','Latitud':'','Longitud':''},
             {'Nombre':'DIA 2','Dirección':'Lomas','Latitud':'-34.771','Longitud':'-58.401'}])
        b=app._evaluate('Ioria 358, Lomas de Zamora',red,'DIA')
        assert b['status']=='ok' and b['result']['ready'],b
        assert b['result']['red']['proximas_1500']==1


def test_main():
    _test_georef_exact()
    _test_no_red_does_not_block()


if __name__=='__main__':
    test_main()
    print('OK Georef exacta, web sin red, web con red parcial y cinco dimensiones')
