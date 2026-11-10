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
from services import geocode, geocode_network_georef, overpass_pois, read_network, valid_latlon

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
    st.markdown('**1. Subí las sucursales de tu negocio**')
    upload = st.file_uploader('Archivo CSV o Excel', type=['csv', 'xlsx', 'xlsm'],
                               label_visibility='collapsed')
    if upload is not None:
        try:
            preview_net=_read_upload(upload.name, upload.getvalue())
            st.caption(f'{len(preview_net):,} sucursales seleccionadas'.replace(',', '.'))
        except (ValueError, KeyError, TypeError):
            st.caption('No pudimos leer el archivo. Usá un Excel o CSV con direcciones de sucursales.')
    st.markdown('**2. Ingresá la dirección a evaluar**')
    address = st.text_input('Dirección', label_visibility='collapsed',
                            placeholder='Calle y altura, localidad, provincia')
    submitted = st.button('Calcular score', type='primary', use_container_width=True)


def _brand(df:pd.DataFrame, filename:str) -> str:
    if 'Cadena' in df.columns:
        s=df['Cadena'].astype(str).str.strip().replace('', pd.NA).dropna()
        if not s.empty:
            return str(s.mode()[0])
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
        st.caption('La población se distribuye proporcionalmente entre radios censales; no representa afluencia peatonal real. '
                   'Los elementos de OSM pueden estar incompletos. Las ponderaciones iniciales son heurísticas y aún no están calibradas contra ventas fuera de muestra. '
                   'El índice no evalúa el estado físico del inmueble, su precio, el contrato ni la normativa de apertura. '
                   'Fuente comercial: '+source+'.')


if submitted:
    if upload is None:
        st.warning('Subí un archivo Excel o CSV con las sucursales de tu negocio.')
    elif not address.strip():
        st.warning('Ingresá una dirección para evaluar.')
    else:
        try:
            if upload.size > 15_000_000:
                raise ValueError('El archivo supera el tamaño permitido.')
            network=_read_upload(upload.name, upload.getvalue())
            if network.empty:
                raise ValueError('El archivo no contiene sucursales utilizables.')
        except Exception as exc:
            log.info('Red incompatible: %s', exc)
            st.warning('No pudimos leer la red de sucursales. Revisá el archivo y volvé a cargarlo.')
            st.stop()
        with st.spinner('Localizando la dirección…'):
            try:
                point=geocode(address.strip())
            except Exception:
                point=None
                log.exception('Error de geocodificación')
        if point is None:
            _unavailable('No pudimos confirmar esa dirección. Revisá la calle, la altura y la localidad e intentá nuevamente.')
            st.stop()
        if ('Calle localizada' in point.get('quality','') or
            'verificar' in point.get('quality','').casefold() and
            point.get('quality','')!='Altura indicada en resultado (verificar)'):
            _unavailable('No pudimos determinar con precisión el punto. Probá incluyendo calle, altura, localidad y provincia.')
            st.stop()
        with st.spinner('Analizando el área de influencia…'):
            try:
                pop=population_at(point['lat'], point['lon'])
            except Exception:
                pop=None
                log.exception('Error en base territorial')
        if pop is None:
            # No census file paths or administrator setup messages in the public UI.
            _unavailable('No fue posible completar la evaluación de esta dirección. Probá nuevamente más tarde.')
            st.stop()
        with st.spinner('Contrastando comercios, transporte y red de sucursales…'):
            try:
                if not all(valid_latlon(r.get('Latitud'),r.get('Longitud')) for _,r in network.iterrows()):
                    network,_=geocode_network_georef(network)
                elements,source=overpass_pois(point['lat'],point['lon'])
                outcome=compute_result(point['lat'],point['lon'],osm=elements,
                                       branches=network.to_dict('records'),
                                       own_brand=_brand(network,upload.name))
            except Exception:
                log.exception('Error de cálculo ROS')
                outcome={'ready':False,'issues':['internal']}
                source='no disponible'
        if not outcome.get('ready'):
            _unavailable('No fue posible reunir suficientes indicadores contrastables en esta consulta. Podés volver a intentarlo más tarde.')
        else:
            _render_result(outcome,address.strip(),network,source)

st.divider()
st.caption('Índice exploratorio de atractivo territorial. No predice facturación ni reemplaza una evaluación comercial profesional. '
           'Cada persona utiliza su propia red durante la sesión.')
