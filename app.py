from __future__ import annotations
import streamlit as st
from engine import load_data,match_address,score,find_by_name
from georef import verify_address
from networks import read_network,own_nearby

st.set_page_config(page_title='ROS | Evaluador territorial',page_icon='📍',layout='centered',initial_sidebar_state='collapsed')
st.markdown('''<style>
.block-container {max-width:850px;padding-top:2rem;padding-bottom:2rem}
#MainMenu, footer {visibility:hidden}
[data-testid="stFileUploader"] {border-radius:14px}
h1 {font-size:2.1rem!important;letter-spacing:-.025em}
.ros-eyebrow{color:#8997a5;font-weight:700;letter-spacing:.11em;font-size:.74rem}
.ros-subtitle{color:#9ba6b0;margin-top:-.1rem;margin-bottom:1.4rem}
.ros-soft{font-size:.85rem;color:#94a3b8}
</style>''',unsafe_allow_html=True)

st.markdown('<div class="ros-eyebrow">ROS · INTELIGENCIA TERRITORIAL</div>',unsafe_allow_html=True)
st.title('Evaluador comercial')
st.markdown('<div class="ros-subtitle">Evaluá una ubicación a partir de datos territoriales del modelo ROS original.</div>',unsafe_allow_html=True)

with st.form('evaluar',clear_on_submit=False):
    network=st.file_uploader('Red de sucursales de tu empresa (opcional)',type=['csv','xlsx'],help='Permite identificar tiendas de tu red. No es necesaria para el ROS territorial.')
    address=st.text_input('Dirección a evaluar',placeholder='Ej.: Ioria 355, Lomas de Zamora, Buenos Aires')
    evaluate=st.form_submit_button('Calcular score',type='primary',use_container_width=True)

if evaluate:
    if not address.strip():st.warning('Ingresá la calle, altura y localidad para continuar.');st.stop()
    zone,match_detail=match_address(address)
    if zone is None:
        st.session_state['pending_address']=address
        st.info('No pude identificar un barrio o municipio de nuestra base. Seleccionalo para hacer la evaluación territorial.')
    else:
        st.session_state['pending_address']=None
        st.session_state['selected_zone']=(zone['ambito'],zone['nombre'])
        st.session_state['matched_by']=match_detail
        st.session_state['pending_network']=network
        st.session_state['last_address']=address
        st.session_state['last_georef']=None

if st.session_state.get('pending_address'):
    available=load_data()['zonas']
    areas=sorted(set(z['ambito'] for z in available))
    area=st.selectbox('Jurisdicción',areas,key='area_ambiguous')
    names=sorted(z['nombre'] for z in available if z['ambito']==area)
    chosen=st.selectbox('Barrio o municipio',names,key='zone_ambiguous')
    if st.button('Calcular ROS territorial',type='primary'):
        st.session_state['selected_zone']=(area,chosen)
        st.session_state['matched_by']='Zona indicada manualmente'
        st.session_state['last_address']=st.session_state.pop('pending_address')
        st.session_state['pending_network']=network
        st.session_state['last_georef']=None
        st.rerun()

if 'selected_zone' in st.session_state and not st.session_state.get('pending_address'):
    area,name=st.session_state['selected_zone']
    zone=find_by_name(area,name)
    if zone is None:st.error('No se encontró la información territorial.');st.stop()
    result=score(zone)
    st.divider()
    st.caption('PUNTUACIÓN TERRITORIAL · BASE ROS ORIGINAL')
    col1,col2=st.columns([1,2],vertical_alignment='center')
    with col1:
        st.metric('ROS',f"{result['score100']:.1f} / 100")
    with col2:
        st.subheader(result['clasificacion'])
        st.write(f"{zone['nombre']} · {'CABA' if area=='CABA' else 'Provincia de Buenos Aires'}")
        st.caption(f"Índice original del Excel: {result['raw']:.3f} / 4")
    st.subheader('Detalle del puntaje')
    for comp in result['parts']:
        c1,c2=st.columns([2,1])
        with c1:st.write(f"**{comp['name']}**");st.progress(comp['level']/4)
        with c2:st.markdown(f"**{comp['points']:.1f} / {comp['weight']:.1f}** puntos")
    with st.expander('Datos territoriales utilizados'):
        c1,c2=st.columns(2)
        with c1:
            st.metric('Población',f"{zone['poblacion']:,}".replace(',','.'))
            st.metric('Densidad poblacional',f"{zone['densidad_poblacional']:,.0f}".replace(',','.'))
        with c2:
            st.metric('Densidad comercial',f"{zone['densidad_comercial']:,.0f}".replace(',','.'))
            st.metric('Cupo disponible',str(zone['cupo_disponible']))
        st.caption('Densidad comercial: viajes/km²/día hábil según el Excel original. Los cupos y los indicadores corresponden a la fecha de actualización de esa base.')
    st.info('Este ROS corresponde al barrio o municipio completo. No mide el flujo real frente al local, sus condiciones físicas ni predice ventas de esa dirección.')
    try:
        rows=read_network(st.session_state.get('pending_network'))
    except Exception as exc:
        rows=[]
        st.warning('La red cargada no pudo leerse. El ROS territorial se calculó igualmente.')
    if rows:
        st.caption(f'Red opcional: {len(rows)} sucursales cargadas. No altera el puntaje territorial histórico.')
    if st.button('Verificar altura y ubicación exacta',key='verificar'):
        with st.spinner('Consultando Georef Argentina...'):
            st.session_state['last_georef']=verify_address(st.session_state['last_address'],zone)
    found=st.session_state.get('last_georef')
    if found:
        if found.get('verified'):
            st.success(f"Dirección confirmada por {found['provider']}: {found['display']}")
            if rows:
                nearby=own_nearby(rows,found)
                if nearby:
                    st.caption('Sucursales propias próximas (distancia en línea recta, no incluida en el ROS):')
                    for meters,label in nearby:st.write(f'• {label} — {meters:,.0f} m'.replace(',','.'))
        else:st.caption('El punto exacto no se pudo confirmar. El ROS del municipio o barrio sigue disponible.')
    with st.expander('Fuente y metodología'):
        st.write('Fuente: **ROS integrado con facturación, simulador y ranking.xlsm**, hojas **Barrios CABA** y **Municipios**.')
        st.write('Se reproducen los cuatro componentes del Excel original con pesos 17,5%, 17,5%, 27,5% y 37,5%. La escala original 1–4 se presenta también sobre 100 dividiéndola por 4.')
        st.write('No requiere OpenStreetMap ni una clave de mapas. La verificación opcional de la dirección usa Georef Argentina y no modifica el score.')
else:
    st.caption('Cobertura inicial: 48 barrios de CABA y 33 municipios de Provincia de Buenos Aires.')
