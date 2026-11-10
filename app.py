"""ROS Simple — independent single-screen application for Argentine grocery networks."""
from __future__ import annotations
import hashlib,json,re,os
from pathlib import Path
os.environ['ROS_PUBLIC']='1'
import pandas as pd
import streamlit as st
from engine import WEIGHTS, compute_result, HERE
from services import read_network,geocode_network_georef,geocode,overpass_pois,valid_latlon
from engine import norm

DATA=HERE/'data'
SAMPLE=HERE/'RED_DIA_EJEMPLO_PARCIAL.csv'
# The public web edition never saves visitor uploads on the shared server.
# Streamlit session_state is isolated by browser session.
st.set_page_config(page_title='ROS Simple | Score de ubicaciones',page_icon='📍',layout='centered',initial_sidebar_state='collapsed')
st.markdown('''<style>
.block-container {max-width:900px; padding-top:2.0rem; padding-bottom:3rem}
h1 {letter-spacing:-.035em !important; font-size:2.25rem !important}
h2, h3 {letter-spacing:-.02em !important}
div[data-testid="stFileUploader"] {border-radius:16px}
div.stButton>button[kind="primary"], button[kind="primary"] {font-weight:700; border-radius:12px; min-height:46px}
.eyebrow {color:#147d74;font-size:12px;font-weight:750;letter-spacing:.12em;}
.kpi {border:1px solid rgba(120,130,146,.24);padding:20px 22px;border-radius:16px;background:rgba(127,140,150,.055);margin-top:12px}
.kpi-num {font-weight:800;font-size:54px;letter-spacing:-3px;line-height:1}
.kpi-sub {color:gray;font-size:13px;margin-top:10px}
.dim-line {display:flex;justify-content:space-between;align-items:baseline;font-size:15px;margin-top:10px}
.bar-bg {height:9px;border-radius:9px;background:rgba(130,140,150,.18);overflow:hidden}
.bar-fore {height:100%;border-radius:9px;background:#159688}
.meta {font-size:12px;color:gray}
</style>''',unsafe_allow_html=True)


def pct(n,d):return round((n/d)*100) if d else 0


def loaded():
    if 'ros_network' not in st.session_state:
        if SAMPLE.exists():
            data=SAMPLE.read_bytes()
            df=read_network(SAMPLE.name,data)
            st.session_state['ros_network']=df
            st.session_state['ros_meta']={
                'nombre':'DÍA · listado público parcial de ejemplo',
                'hash':hashlib.sha256(data).hexdigest(), 'parcial':True}
        else:
            st.session_state['ros_network']=pd.DataFrame(columns=['Nombre','Dirección','Latitud','Longitud','Cadena'])
            st.session_state['ros_meta']={'nombre':'Sin red','hash':'','parcial':False}
        st.session_state['ros_geo_attempted']=set()
    return st.session_state['ros_network'],st.session_state['ros_meta']


def store_network(filename,data):
    sha=hashlib.sha256(data).hexdigest()
    if sha==loaded()[1].get('hash'):
        return False
    if len(data)>15_000_000:
        raise ValueError('El archivo no puede superar 15 MB.')
    df=read_network(filename,data)
    st.session_state['ros_network']=df
    st.session_state['ros_meta']={'nombre':filename,'hash':sha,'parcial':False}
    st.session_state['ros_geo_attempted']=set()
    return True


def prepare_network(df):
    if df.empty:return df
    attempted=st.session_state.setdefault('ros_geo_attempted',set())
    pending=[i for i,row in df.iterrows()
             if i not in attempted and
             not valid_latlon(row.get('Latitud'),row.get('Longitud')) and
             bool(str(row.get('Dirección','')).strip())]
    if not pending:return df
    # Bounded work per request: avoids saturating the public Georef API.
    subset=pending[:50]
    attempted.update(subset)
    with st.spinner(f'Preparando la red ({len(subset)} direcciones sin coordenadas)…'):
        try:
            part=df.loc[subset].copy()
            located,_=geocode_network_georef(part)
            df=df.copy()
            for i in subset:
                df.at[i,'Latitud']=located.at[i,'Latitud']
                df.at[i,'Longitud']=located.at[i,'Longitud']
            st.session_state['ros_network']=df
        except Exception:
            # Missing branch coordinates never get invented.
            pass
    return df


def label_score(value):
    if value is None:return 'SIN DATOS'
    return f'{value:.1f}/100'

st.markdown('<div class="eyebrow">ROS SIMPLE 1.0 · UBICACIONES COMERCIALES</div>',unsafe_allow_html=True)
st.title('Un domicilio. Un score claro.')
st.caption('Cargá la red de tu cadena, escribí la dirección y recibí el score detallado. Todo desde el navegador, sin instalaciones.')

st.subheader('1 · Red de supermercados')
df,metadata=loaded()
count_geo=sum(valid_latlon(r.get('Latitud'),r.get('Longitud')) for _,r in df.iterrows())
st.info(f"**{metadata.get('nombre','Red cargada')}** · {len(df)} sucursales · {count_geo} con coordenadas verificables." + (' Es un listado parcial, no un padrón vigente.' if metadata.get('parcial') else ''))
upload=st.file_uploader('Para reemplazar la red, subí tu CSV o Excel',type=['csv','xlsx','xlsm'],help='Acepta Dirección, Nombre y, opcionalmente, Latitud y Longitud. La red se utiliza solo en esta sesión; no se comparte con otros visitantes.')
if upload is not None:
    try:
        changed=store_network(upload.name,upload.getvalue())
        if changed:st.success('Red cargada en tu sesión. Otros visitantes no pueden modificarla.')
        df,metadata=loaded()
    except Exception as ex:st.error(f'No pude leer la red: {ex}')

if not (HERE / 'data' / 'censo_pba_2022.geojson.gz').is_file():
    st.warning('Falta la base del Censo 2022 en el servidor. Para completar la demanda residencial, subí `data/censo_pba_2022.geojson.gz` a GitHub. Las otras dimensiones pueden evaluarse si responden las fuentes cartográficas.')

st.subheader('2 · Dirección a evaluar')
with st.form('eval',clear_on_submit=False):
    address=st.text_input('Dirección',placeholder='Julio Sánchez Gardel 28, Temperley, Buenos Aires',help='También admite coordenadas o un enlace de Google Maps con coordenadas visibles.')
    submit=st.form_submit_button('Calcular score',type='primary',use_container_width=True)

if submit:
    if not address.strip():st.warning('Escribí una dirección para calcular el score.')
    else:
        with st.spinner('Localizando la dirección…'):
            point=geocode(address)
        if not point:
            st.error('No fue posible verificar las coordenadas con los servicios disponibles. Podés pegar en el mismo campo un enlace de Google Maps que incluya las coordenadas del punto. No vamos a inventar una ubicación.')
        else:
            with st.spinner('Calculando población y consultando el entorno…'):
                df=prepare_network(df)
                osm,source=overpass_pois(point['lat'],point['lon'])
                red_name=norm(metadata.get('nombre',''))
                brands=['DIA','Carrefour','Coto','Jumbo','Chango Mas','Vea','Disco','La Anonima']
                brand=next((b for b in brands if norm(b) in red_name), '')
                if not brand and 'Cadena' in df:
                    b=df['Cadena'].replace('',pd.NA).dropna()
                    if len(b):brand=str(b.mode()[0])
                result=compute_result(point['lat'],point['lon'],osm=osm,branches=df.to_dict('records'),own_brand=brand)
            st.divider()
            st.markdown('### Resultado de la dirección')
            st.write(f"**{address.strip()}**")
            st.caption(f"Geolocalización: {point.get('provider','–')} · {point.get('quality','punto a verificar')} · {point['lat']:.5f}, {point['lon']:.5f}")
            if result['score'] is None:
                st.error('No hay datos suficientes para emitir un score. Probá con una dirección de Provincia de Buenos Aires o repetí cuando vuelvan los servicios de mapas.')
            else:
                coverage=result['coverage']
                full=coverage==100
                st.markdown(f'''<div class="kpi"><div class="eyebrow">{'SCORE COMERCIAL · EXPLORATORIO' if full else 'SCORE PARCIAL · NO COMPARABLE CON UNO COMPLETO'}</div><div class="kpi-num">{result['score']:.1f}<span style="font-size:21px;color:gray;letter-spacing:0"> / 100</span></div><div class="kpi-sub">Cobertura de indicadores: {coverage}% de la ponderación total. {'Los 5 componentes fueron medidos.' if full else 'Solo se ponderaron las variables verificadas. No se inventaron las faltantes.'}</div></div>''',unsafe_allow_html=True)
            st.markdown('#### Desglose del score')
            for name,weight in WEIGHTS.items():
                s=result['scores'][name]
                value=0 if s is None else s
                st.markdown(f'<div class="dim-line"><strong>{name}</strong><span><strong>{label_score(s)}</strong> <span class="meta">· peso {weight}%</span></span></div><div class="bar-bg"><div class="bar-fore" style="width:{value}%;background:{"#159688" if s is not None else "#a2abb3"}"></div></div>',unsafe_allow_html=True)
            st.divider()
            p=result['poblacion'];o=result['osm'];red=result['red']
            st.markdown('#### Datos utilizados')
            if p:
                a,b,c=st.columns(3)
                a.metric('Población ~400 m',f"{p[400]:,}".replace(',','.'))
                b.metric('Población ~800 m',f"{p[800]:,}".replace(',','.'))
                c.metric('Población ~1.200 m',f"{p[1200]:,}".replace(',','.'))
                st.caption(f"Censo 2022 Provincia de Buenos Aires. Estimación proporcional al área de los radios. Cobertura geométrica del círculo de 800 m: {p['coverage_800']}%. Son radios, no tiempos caminando.")
            else:st.caption('Demanda: la base censal incorporada cubre Provincia de Buenos Aires, no CABA ni otras provincias. No se inventa población donde falta.')
            if o:
                st.write(f"**OpenStreetMap:** {o['comercios']} comercios registrados · {o['paradas']} paradas/estaciones · {o['vias']} vías · {o['supermercados_terceros']} supermercados de otras marcas · {o['servicios']} servicios.")
                st.caption('Actividad y transporte son indicadores de presencia registrada, no aforos ni conteos reales de peatones. Los establecimientos de OSM pueden estar incompletos.')
            else:st.caption('Entorno OSM: '+source+'.')
            st.write(f"**Red cargada:** {len(df)} sucursales, {sum(valid_latlon(r.get('Latitud'),r.get('Longitud')) for _,r in df.iterrows())} con coordenadas.")
            st.write(f"**Sucursales propias dentro de 1.500 m:** {red['proximas_1500']} (solo entre las geolocalizadas).")
            st.caption('Si quedan direcciones de la red sin coordenadas, no se interpreta su ausencia como inexistencia de tiendas.')
            if point.get('quality','').startswith('Calle localizada'):
                st.warning('La calle se localizó, pero la altura no quedó verificada. Confirmá el punto antes de utilizarlo en una decisión.')
            if len(df)>0 and sum(valid_latlon(r.get('Latitud'),r.get('Longitud')) for _,r in df.iterrows())<len(df):
                st.caption('La red no está geolocalizada por completo; la presión de sucursales propias puede estar subestimada.')
            if result['coverage']<100:
                st.warning('Score parcial: las dimensiones sin fuente no se toman como 0. Este resultado no debe compararse directamente con scores completos.')
            else:
                st.caption('Score heurístico de atractivo comercial, no una predicción de ventas ni una probabilidad de éxito. No considera las características edilicias del inmueble.')

st.divider()
st.caption('Uso público: cada visitante utiliza su propia red durante la sesión. El archivo no se guarda como red global ni se publica para otros usuarios; si recargás o se reinicia el servidor podrías tener que subirlo de nuevo. Los servicios externos reciben la dirección necesaria para geolocalizarla.')
st.caption('Fuentes: INDEC Censo 2022 / radios de PBA; OpenFreeMap © OpenMapTiles, datos de OpenStreetMap (ODbL); Georef Argentina. El modelo no evalúa el inmueble, la rentabilidad ni la normativa de apertura.')
