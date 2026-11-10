"""Full mock Streamlit app render with zero uploaded files and an address.

Only simulated sources; this does NOT verify external APIs. It detects the
previous 'upload is mandatory' UI regression and rendering errors.
"""
import sys,types
from contextlib import nullcontext
from unittest.mock import patch

class FakeWidget:
    def markdown(self,*a,**k):pass

class FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__('streamlit')
        self.session_state={}
        self.query_params={}
        self.shown=[]
        self.upload=None
    def set_page_config(self,**kw):pass
    def markdown(self,x,*a,**k):self.shown.append(x)
    def title(self,x,*a,**k):self.shown.append(x)
    def container(self,**kw):return nullcontext()
    def spinner(self,*a,**kw):return nullcontext()
    def expander(self,*a,**kw):return nullcontext()
    def file_uploader(self,*a,**kw):return self.upload
    def text_input(self,*a,**kw):return 'Ioria 358, Lomas de Zamora'
    def button(self,*a,**kw):return True
    def divider(self):pass
    def caption(self,*a,**kw):pass
    def progress(self,*a,**kw):pass
    def write(self,*a,**kw):pass
    def info(self,*a,**kw):raise AssertionError('Unexpected unavailable state: '+str(a))
    def warning(self,*a,**kw):raise AssertionError('Unexpected warning: '+str(a))
    def columns(self,*a,**kw):return [FakeWidget(),FakeWidget()]
    def cache_data(self,**kw):return lambda f:f


def test_full_render():
    st=FakeStreamlit()
    sys.modules['streamlit']=st
    import services,osm_snapshot
    point={'lat':-34.77,'lon':-58.40,'quality':'Altura exacta',
           'provider':'Georef Argentina','label':'Ioria 358, Lomas de Zamora'}
    fake=[]
    fake.extend([{'type':'node','tags':{'shop':'bakery'}} for _ in range(14)])
    fake.extend([{'type':'node','tags':{'amenity':'cafe'}} for _ in range(8)])
    fake.extend([{'type':'node','tags':{'amenity':'school'}} for _ in range(8)])
    fake.extend([{'type':'node','tags':{'highway':'bus_stop'}} for _ in range(6)])
    fake.extend([{'type':'way','tags':{'highway':'residential'}} for _ in range(20)])
    with patch.object(services,'geocode',return_value=point),patch.object(osm_snapshot,'local_osm',return_value=(fake,'OSM de prueba')):
        import app
        assert any('Resultado de ubicación' in s for s in st.shown)
        assert any('Desglose' in s for s in st.shown)
        assert not any('Evaluación no disponible' in s for s in st.shown)
        # A 139-branch file without coordinates must not make the whole
        # evaluation unavailable; this was an actual regression in the web app.
        import importlib
        from pathlib import Path
        class FileUpload:
            name='RED_DIA_REFERENCIAS_PUBLICAS_PARCIAL.csv'
            data=Path('/mnt/data/RED_DIA_REFERENCIAS_PUBLICAS_PARCIAL.csv').read_bytes()
            size=len(data)
            def getvalue(self): return self.data
        st.upload=FileUpload()
        st.shown=[]
        importlib.reload(app)
        assert any('Resultado de ubicación' in s for s in st.shown)
        assert not any('Evaluación no disponible' in s for s in st.shown)

if __name__=='__main__':
    test_full_render()
    print('OK: web completa con dirección, sin red, sin mensajes de error')
