"""ROS Web: single-screen public experience, never publishes incomplete scores.

The model is heuristic and does not include building-level or financial variables.
An unavailable source is an unavailable evaluation, never a fabricated low score.
"""
from __future__ import annotations

import logging
from pathlib import Path
import os

import pandas as pd
import streamlit as st

from engine import WEIGHTS, compute_result, norm, population_at
from services import geocode, read_network, valid_latlon, overpass_pois
from osm_snapshot import local_osm, snapshot_metadata

log = logging.getLogger('rosweb')
# En modo público no se guarda el texto de direcciones ingresadas por visitantes.
os.environ.setdefault('ROS_PUBLIC', '1')

st.set_page_config(page_title='ROS | Evaluador comercial', page_icon='📍',
                   layout='centered', initial_sidebar_state='collapsed')
st.markdown('''<style>
  .block-container {max-width: 830px; padding-top: 2.1rem; padding-bottom: 3.0rem}
  #MainMenu, footer {visibility: hidden}
  h1, h2, h3 {letter-spacing: -.025em}
  h1 {font-size: 2.2rem !important; margin-bottom:.4rem !important}
  .ros-kicker {font-size:.76rem; font-weight:750; letter-spacing:.12em; color:#159688; margin-bottom:.4rem}
  .ros-subtitle {font-size:1.06rem; color:#8f969f; line-height:1.55; margin-bottom:1.4rem}
  .ros-panel {padding: 1.1rem 1.4rem; border: 1px solid rgba(127,141,151,.23); border-radius:15px; margin: 1rem 0}
  .ros-big {font-size:3.4rem; font-weight:760; line-height:1.05; letter-spacing:-.045em}
  .ros-grey {color:#9099a2; font-size:.9rem; line-height:1.5}
  .ros-mark {font-weight:750; color:#159688; font-size:.76rem; letter-spacing:.08em}
  [data-testid="stFileUploader"] {border-radius: 13px}
  div.stButton>button[kind="primary"] {font-weight:750; border-radius: 12px; min-height:3rem}
  div[data-testid="stAlert"] {border-radius: 12px}
  @media (max-width:600px){h1{font-size:1.85rem!important}.block-container{padding-top:1.3rem}}
</style>''', unsafe_allow_html=True)

st.markdown('<div class="ros-kicker">ROS / INTELIGENCIA TERRITORIAL</div>', unsafe_allow_html=True)
st.title('Evaluador comercial')
st.markdown('<p class="ros-subtitle">Compará el potencial de una ubicación con criterios territoriales y comerciales.</p>', unsafe_allow_html=True)

@st.cache_data(show_spinner=False, ttl=3600)
def _read_upload(filename:str, contents:bytes):
    return read_network(filename,contents)


with st.container(border=True):
    st.markdown('**1. Subí las sucursales de tu negocio (opcional)**')
    upload = st.file_uploader('Archivo CSV o Excel', type=['csv', 'xlsx', 'xlsm'],
                               label_visibility='collapsed')
    if upload is not None:
        try:
            preview_net=_read_upload(upload.name, upload.getvalue())
            st.caption(f'{len(preview_net):,} sucursales seleccionadas'.replace(',', '.'))
        except (ValueError, KeyError, TypeError):
            st.caption('No pudimos leer el archivo. Usá un Excel o CSV con direcciones de sucursales.')
    st.markdown('**2. Dirección a evaluar**')
    address = st.text_input('Dirección', label_visibility='collapsed',
                            placeholder='Calle y altura, localidad, provincia')
    submitted = st.button('Calcular score', type='primary', use_container_width=True)


def _brand(df:pd.DataFrame, filename:str) -> str:
    if 'Cadena' in df.columns:
        s=df['Cadena'].astype(str).str.strip().replace('', pd.NA).dropna()
        if not s.empty:
            return str(s.mode()[0])
    # Infer only unambiguous known brands from filename; never require it for score.
    normalized = norm(filename)
    known = {'carrefour':'Carrefour','dia':'DIA','coto':'Coto',
             'jumbo':'Jumbo','disco':'Disco','changomas':'ChangoMás',
             'vea':'Vea','la anonima':'La Anónima'}
    for token,value in known.items():
        if token in normalized.split() or (' ' in token and token in normalized):
            return value
    return ''


def _unavailable(message:str) -> None:
    st.markdown('### Evaluación no disponible')
    st.info(message)
    st.caption('No se genera una puntuación cuando la información disponible no permite un cálculo confiable.')


def _render_result(out:dict, location:str, network:pd.DataFrame, source:str) -> None:
    score=out['score']
    label='Muy favorable' if score>=80 else 'Favorable' if score>=65 else 'Intermedio' if score>=45 else 'A revisar'
    st.markdown('### Resultado de ubicación')
    st.caption(location)
    st.markdown('<div class="ros-panel"><div class="ros-mark">ÍNDICE DE ATRACTIVO COMERCIAL</div>'
                f'<div class="ros-big">{score:.1f}<span style="font-size:1.15rem; color:#89929e"> / 100</span></div>'
                f'<div class="ros-grey">{label} · Evaluación territorial estimativa</div></div>',
                unsafe_allow_html=True)
    st.markdown('#### Desglose del score')
    for label,weight in WEIGHTS.items():
        value=out['scores'][label]
        c1,c2=st.columns([4,1],vertical_alignment='center')
        c1.markdown('**'+label+'**')
        c2.markdown(f'**{value:.0f}/100**')
        st.progress(float(value)/100.0)
        st.caption(f'Participación en el total: {weight}% · Aporte: {value*weight/100:.1f} puntos')
    pop=out.get('poblacion') or {}
    osm=out.get('osm') or {}
    with st.expander('Datos y metodología'):
        st.write('Indicadores de población del Censo 2022 y establecimientos registrados en OpenStreetMap.')
        st.write('Fuente de población:',pop.get('fuente','Censo 2022') if pop else '—')
        st.write('Población residencial estimada dentro de 800 m:',f"{int(pop[800]):,}".replace(',','.') if pop else '—')
        st.write('Comercios OSM registrados en el entorno:',osm.get('comercios','—'))
        st.write('Transporte registrado:',osm.get('transporte','—'))
        st.write('Servicios registrados:',osm.get('servicios','—'))
        st.write('Sucursales de la red incorporada:',len(network))
        st.write('Sucursales con coordenadas verificables:',out.get('red',{}).get('total_verificadas',0))
        st.caption('La población se distribuye proporcionalmente entre radios censales; no representa afluencia peatonal real. '
                   'Los elementos de OSM pueden estar incompletos. Las ponderaciones iniciales son heurísticas y aún no están calibradas contra ventas fuera de muestra. '
                   'El índice no evalúa el estado físico del inmueble, su precio, el contrato ni la normativa de apertura. '
                   'Fuente comercial: '+source+'.')


def _geocode_cached(address_text:str):
    # Caching only positive results: negative lookups can work on later attempts.
    return geocode(address_text)


def _osm_cached(lat:float,lon:float):
    local, source = local_osm(lat,lon)
    if local is not None:
        return local,source
    # If the GitHub map snapshot is not yet available, try a *small* live
    # Overpass query. No map-tile scraping or bulk queries from each visitor.
    return overpass_pois(lat,lon)


def _evaluate(addr:str,net:pd.DataFrame,brand:str):
    # This function intentionally works with an empty network.
    try:
        point = _geocode_cached(addr)
    except Exception:
        log.exception('Falló la geolocalización')
        return {'status':'geocode_error'}
    if not point:
        return {'status':'geocode_missing'}
    try:
        lat,lon=float(point['lat']),float(point['lon'])
        if not valid_latlon(lat,lon):
            return {'status':'geocode_missing'}
        pop=population_at(lat,lon)
    except Exception:
        log.exception('Falló la base demográfica')
        pop=None
    try:
        osm,source=_osm_cached(round(lat,5),round(lon,5))
    except Exception:
        log.exception('Falló la consulta comercial')
        osm,source=None,'Fuente de comercios temporalmente no disponible'
    # This call does not require branch coordinates; the known coordinates,
    # if provided, only improve identification of nearby own branches.
    try:
        outcome=compute_result(lat,lon,osm=osm,
                    branches=net.to_dict('records'),own_brand=brand,population=pop)
    except Exception:
        log.exception('Falló el motor ROS')
        return {'status':'calculation_error','point':point}
    return {'status':'ok','point':point,'result':outcome,'osm_source':source}


if submitted:
    if not address.strip():
        st.warning('Escribí la calle, la altura y la localidad que querés evaluar.')
    else:
        network=pd.DataFrame(columns=['Nombre','Dirección','Latitud','Longitud','Cadena'])
        own_brand=''
        if upload is not None:
            try:
                if upload.size > 15_000_000:
                    raise ValueError('Archivo demasiado grande')
                network=_read_upload(upload.name, upload.getvalue())
                own_brand=_brand(network, upload.name)
            except Exception:
                st.warning('El archivo de sucursales no se pudo leer. Podés retirarlo y calcular igualmente el score de la dirección.')
                st.stop()
        with st.spinner('Buscando la dirección y analizando su entorno…'):
            response=_evaluate(address.strip(),network,own_brand)
        status=response.get('status')
        if status=='geocode_missing':
            _unavailable('No encontramos una coincidencia suficientemente clara para esa dirección. Probá agregando partido y provincia, o pegá las coordenadas de Google Maps.')
        elif status!='ok':
            _unavailable('La búsqueda presentó un error de conexión. Probá nuevamente. El problema no es la red de sucursales.')
        else:
            outcome=response['result']
            point=response['point']
            quality=str(point.get('quality') or '')
            if not outcome.get('ready'):
                issues=set(outcome.get('issues',[]))
                if 'osm' in issues or 'osm_sparse' in issues:
                    _unavailable('La dirección se localizó, pero no se pudo obtener suficiente información comercial del mapa. La evaluación no tendrá un puntaje ficticio.')
                elif 'population' in issues:
                    _unavailable('La dirección se localizó, pero no hay información demográfica suficiente para completar el resultado.')
                else:
                    _unavailable('La dirección se localizó, pero faltan indicadores verificables para completar el score.')
                st.caption('Dirección localizada: '+str(point.get('label') or address.strip()))
                with st.expander('Ver qué información se obtuvo'):
                    st.write('Geolocalización:',point.get('provider','—'),quality)
                    st.write('Censo:', 'disponible' if outcome.get('poblacion') else 'no disponible')
                    st.write('Mapa comercial:',response['osm_source'])
                    st.write('Códigos de diagnóstico:',', '.join(outcome.get('issues',[])))
            else:
                _render_result(outcome,address.strip(),network,response['osm_source'])
                if quality not in {'Altura indicada en resultado (verificar)','Coordenadas aportadas','Altura exacta'}:
                    st.caption('Ubicación aproximada: el proveedor cartográfico no confirmó la altura exacta. El score describe el entorno de ese punto, no la puerta del inmueble.')

st.divider()
st.caption('© OpenStreetMap contributors (ODbL). Índice exploratorio de atractivo territorial. No predice facturación ni reemplaza una evaluación comercial profesional. '
           'Cada persona utiliza su propia red durante la sesión.')

# Soporte técnico optativo, no visible en la interfaz pública habitual.
if st.query_params.get('diagnostico') == '1':
    with st.expander('Estado técnico de las fuentes'):
        meta = snapshot_metadata()
        st.write('Base OSM:', 'disponible' if meta else 'no cargada')
        if meta:
            st.write('Fecha de corte:', meta.get('downloaded_at', '—'))
            st.write('Elementos cartográficos:', meta.get('features', '—'))
        from engine import CENSUS
        st.write('Censo PBA:', 'disponible' if CENSUS.is_file() else 'no cargado')
        from pathlib import Path
        st.write('Censo CABA:', 'disponible' if (Path(__file__).parent/'data/censo_caba_2022.geojson.gz').is_file() else 'no cargado')
