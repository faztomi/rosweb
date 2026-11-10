"""Adaptación web mínima del evaluador local V37.

El motor de localización y microentorno es el original, no un scoring nuevo.
No utiliza los paquetes ROS Web v2-v5, ninguna base OSM externa ni Actions.
"""
from __future__ import annotations

import re
from pathlib import Path
import streamlit as st
import pandas as pd

from datos_externos import leer_censo_csv, aplicar_censo
from osm_resiliente import locate
import v37_original as v37

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title='ROS | Evaluador comercial V37', page_icon='📍', layout='centered')

@st.cache_data(show_spinner=False)
def censo_incorporado():
    resultado={}
    for nombre in ('CENSO_2022_VERIFICADO.csv','CENSO_2022_MUNICIPIOS.csv'):
        texto=(ROOT / nombre).read_text(encoding='utf-8-sig')
        resultado.update(leer_censo_csv(texto))
    return resultado


def cadena_desde_archivo(nombre, red):
    texto=v37.norm(nombre)
    if not red.empty:
        texto+=' '+' '.join(v37.norm(x) for x in red['Nombre'].head(8).tolist())
    marcas={'Día':('dia','dia supermercado'), 'Carrefour':('carrefour',),
            'Coto':('coto',),'Jumbo':('jumbo',),'Disco':('disco',),'Vea':('vea',),
            'Chango Más':('chango mas','chango'), 'La Anónima':('anonima',)}
    for nombre,alias in marcas.items():
        if any(re.search(r'(?<![a-z])'+re.escape(x)+r'(?![a-z])',texto) for x in alias):
            return nombre
    return ''


def registro_para_ubicacion(geo, entrada):
    rec,matched=v37.resolve_model_location(geo)
    if rec is not None:
        return rec,matched
    # Si Georef devuelve localidad censal distinta del partido, utilizar
    # únicamente los términos declarados en la dirección o jurisdicción.
    addr=geo.get('address') or {}
    candidates=[entrada]+[str(addr.get(k,'')) for k in ('county','city','suburb','municipality','city_district')]
    for c in candidates:
        normalized=' '+v37.norm(c)+' '
        for key,items in v37.LOCATION_INDEX.items():
            if len(key)>=5 and (' '+key+' ') in normalized:
                return items[0],c
    return None,None


def localizar(direccion):
    # Georef Argentina + Nominatim + Photon (de la versión local resiliente).
    candidato=locate(direccion, user_agent='ROS-Expansion-V37/1.0 (evaluador comercial; https://rosweb.streamlit.app)')
    if candidato is not None:
        return candidato
    # V37's old fallback returned the first Nominatim hit without validating
    # locality or house number. That silently routed Lomas de Zamora to Glew.
    # Never use unchecked geocoder results in production.
    return None


def mostrar_resultado(direccion, archivo, formato):
    red=pd.DataFrame(columns=v37.NETWORK_COLUMNS)
    marca=''
    if archivo is not None:
        archivo.seek(0)
        red=v37.load_network_upload(archivo)
        marca=cadena_desde_archivo(archivo.name,red)
    with st.spinner('Localizando dirección y consultando el entorno comercial…'):
        geo=localizar(direccion)
        if not geo:
            st.error('No pude confirmar esa calle y altura. Revisá la dirección e intentá de nuevo.')
            return
        rec,matched=registro_para_ubicacion(geo,direccion)
        if rec is None:
            st.warning('Encontré la dirección, pero está fuera de las 81 ubicaciones territoriales de la V37. No asigno una población de otro municipio.')
            st.caption(geo.get('display_name',''))
            return
        lat,lon=float(geo['lat']),float(geo['lon'])
        try:
            osm=v37.query_osm_microlocation(lat,lon)
        except Exception:
            st.error('La dirección está localizada, pero el servicio de OpenStreetMap no respondió. No se asignará un score sin los comercios verificados.')
            st.caption(f'Dirección confirmada: {geo.get("display_name", direccion)}')
            return
        metrics=v37.derive_micro_metrics(lat,lon,osm)
        metrics=v37.apply_competition_sources(metrics,lat,lon)
        census=censo_incorporado()
        st.session_state['censo2022']=census
        rec=aplicar_censo(rec,census)
        red_metricas=v37.company_network_metrics(lat,lon,red)
        obs=v37.build_company_observations(rec,metrics,red_metricas,company_name=marca)
        ref=v37.referencias_censales(rec.get('Tipo'))
        resultado=v37.score_company_location(None,obs,format_name=formato,referencias=ref)

    st.divider()
    st.subheader('Resultado de la ubicación')
    st.caption(str(geo.get('display_name') or direccion))
    if resultado['sufficient_data'] and resultado['commercial_score'] is not None:
        st.metric('Potencial comercial · V37',f'{resultado["commercial_score"]:.1f} / 100')
        st.markdown(f'**Lectura:** {v37.v37_output_level(resultado["commercial_score"])}')
    else:
        st.warning('La V37 no alcanzó la cobertura mínima para publicar un puntaje total. Se muestran las dimensiones verificadas.')
    st.markdown('#### Desglose original V37')
    for row in resultado['rows']:
        nombre=row['Factor']
        if nombre=='Red propia' and not red_metricas['available']:
            continue
        score=row['Score actual']
        if score is None:
            st.write(f'**{nombre}:** Sin datos suficientes')
        else:
            st.write(f'**{nombre}: {score:.1f}/100** · {row["Cobertura %"]:.0f}% de cobertura')
            st.progress(max(0.0,min(1.0,float(score)/100.0)))
    cols=st.columns(3)
    cols[0].metric('Comercios relevados',str(metrics['activity_relevant_pois_500']))
    cols[1].metric('Paradas cercanas',str(metrics['transit_500']))
    cols[2].metric('Supermercados ≤1 km',str(metrics['supermarkets_1000']))
    if metrics['nearby_supermarkets']:
        with st.expander('Supermercados identificados y distancias'):
            for p in metrics['nearby_supermarkets'][:12]:
                if p.get('distance_m') is not None:
                    st.write(f"{p.get('name') or 'Supermercado'} · {p['distance_m']:.0f} m (línea recta)")
    with st.expander('Datos territoriales utilizados'):
        st.write(f"Unidad: {rec.get('Ubicación')} · {rec.get('Tipo')}")
        st.write(f"Población de la unidad territorial: {rec.get('Población'):,}" if rec.get('Población') is not None else 'Población no disponible')
        st.write(f"Fuente: {rec.get('Fuente población', 'serie histórica territorial sin año verificado')}")
        st.caption('La V37 utiliza datos por barrio o municipio, no por radio caminable. Este índice es exploratorio y no constituye una previsión de ventas.')
    if archivo is not None and not red_metricas['available']:
        st.caption('Red cargada sin coordenadas verificadas: se calculó el potencial comercial sin penalizar ni premiar la proximidad a sucursales propias.')
    if red_metricas['available'] and resultado.get('expansion_score') is not None and resultado['sufficient_data']:
        st.metric('Conveniencia de apertura · con red',f'{resultado["expansion_score"]:.1f} / 100')
    st.map(pd.DataFrame([{'lat':lat,'lon':lon}]), zoom=14)


st.title('Evaluador comercial')
st.caption('Motor territorial y OpenStreetMap de la versión local V37.')
red_subida=st.file_uploader('Red de sucursales (opcional)',type=['csv','xlsx'])
direccion=st.text_input('Dirección a evaluar',placeholder='Mariano S. Ioria 355, Lomas de Zamora, Buenos Aires')
with st.expander('Formato de supermercado (opcional)'):
    formato=st.selectbox('Formato', ['Express','Market','Hiper','Maxi'],index=0)
if st.button('Calcular score', type='primary', use_container_width=True):
    if not direccion.strip():
        st.warning('Ingresá una dirección para comenzar.')
    else:
        try:
            mostrar_resultado(direccion.strip(),red_subida,formato)
        except (ValueError,KeyError,TypeError) as exc:
            st.error('No pude procesar la información de entrada. Revisá el formato del archivo y la dirección.')
            st.caption(type(exc).__name__)

st.caption('© OpenStreetMap contributors · ROS V37 · Análisis comercial exploratorio, sin evaluación edilicia.')
