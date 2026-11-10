"""ROS: cálculo territorial verificable + análisis adicional NO bloqueante.

El índice territorial original es independiente de Georef y Overpass. No modificar
el motor del Excel sin una revisión explícita de la metodología.
"""
from __future__ import annotations
import streamlit as st
from engine import match_address,find_by_name,load_data,score,normal
from georef import verify_address
from networks import read_network
from geo_extra import fetch_nearby,infer_chain
from analysis_extra import network_analysis,label_branches
from revenue import simulate

st.set_page_config(page_title='ROS | Evaluador comercial',page_icon='📍',layout='centered',initial_sidebar_state='collapsed')
st.markdown('''<style>
.block-container{max-width:910px;padding-top:1.8rem;padding-bottom:3rem}
#MainMenu,footer{visibility:hidden}
h1{letter-spacing:-.04em}
.ros-kicker{font-size:.73rem;font-weight:700;letter-spacing:.1em;color:#8392a6}
.ros-intro{color:#9ca9b8;font-size:.95rem;margin-top:-.5rem;margin-bottom:1.4rem}
.ros-help{font-size:.85rem;color:#94a3b8}
</style>''',unsafe_allow_html=True)
st.markdown('<div class="ros-kicker">ROS · INTELIGENCIA COMERCIAL</div>',unsafe_allow_html=True)
st.title('Evaluador comercial')
st.markdown('<div class="ros-intro">Ingresá una dirección. El ROS territorial funciona incluso cuando el servicio de mapas no responde.</div>',unsafe_allow_html=True)

with st.form('evaluar_direccion'):
    addr=st.text_input('Dirección a evaluar',placeholder='Ej.: Ioria 355, Lomas de Zamora, Buenos Aires')
    network=st.file_uploader('Red de sucursales de tu negocio (opcional)',type=['csv','xlsx'],
            help='Se usa solo para cercanía de tiendas propias. Podés evaluar sin cargar un archivo.')
    submit=st.form_submit_button('Calcular ROS y analizar entorno',type='primary',use_container_width=True)


def prepare(address,zone,rows,network_label='',source_note=''):
    """El ROS siempre se calcula primero, independientemente de servicios externos."""
    evaluation=score(zone)
    report={'address':address,'key':(zone['ambito'],zone['nombre']),
            'score':evaluation,'rows':rows,'label':network_label,
            'geo':None,'osm':None,'network':None,'source_note':source_note}
    return report


def enrich(report):
    zone=find_by_name(*report['key'])
    # Un fallo de geolocalización no altera jamás la evaluación territorial.
    try:
        geo=verify_address(report['address'],zone)
    except Exception:
        geo={'verified':False,'reason':'Servicio de geolocalización no disponible'}
    report['geo']=geo
    if not geo or not geo.get('verified'):
        return report
    lat,lon=geo['lat'],geo['lon']
    try:
        report['osm']=fetch_nearby(lat,lon)
    except Exception:
        report['osm']={'ok':False,'supermarkets':[],'transport':[],'roads':[]}
    osm_shops=(report['osm'] or {}).get('supermarkets',[]) if (report['osm'] or {}).get('ok') else []
    try:
        chain=infer_chain(report.get('label'),report.get('rows') or [])
        report['network']=network_analysis(report.get('rows') or [],lat,lon,osm_shops,chain)
        report['chain']=chain
    except Exception:
        report['network']=None
    return report


if submit:
    st.session_state.pop('sim_result',None)
    if not addr.strip():
        st.warning('Ingresá una dirección con calle, altura y localidad.')
    else:
        try:
            rows=read_network(network) if network is not None else []
            err=''
        except Exception:
            rows=[]
            err='El archivo de sucursales no pudo leerse. El ROS territorial seguirá disponible.'
        zone,match_detail=match_address(addr)
        if zone is None:
            st.session_state['ambiguous']={'address':addr,'rows':rows,'label':network.name if network else '', 'notice':err}
            st.session_state.pop('report',None)
        else:
            st.session_state.pop('ambiguous',None)
            report=prepare(addr,zone,rows,network.name if network else '', match_detail)
            st.session_state['report']=report
            with st.spinner('Verificando la dirección y el entorno disponible...'):
                st.session_state['report']=enrich(report)
            if err:st.warning(err)

if st.session_state.get('ambiguous'):
    st.info('El barrio o municipio no está identificado con certeza. Seleccionalo para calcular el ROS territorial sin asignar una localidad incorrecta.')
    entry=st.session_state['ambiguous']
    zones=load_data()['zonas']
    scopes=['PBA','CABA']
    scope=st.selectbox('Jurisdicción',scopes,format_func=lambda x:'Provincia de Buenos Aires' if x=='PBA' else 'CABA')
    names=sorted(z['nombre'] for z in zones if z['ambito']==scope)
    name=st.selectbox('Barrio o municipio',names)
    if st.button('Confirmar ubicación y calcular ROS',use_container_width=True):
        zone=find_by_name(scope,name)
        st.session_state['report']=enrich(prepare(entry['address'],zone,entry['rows'],entry['label'], 'Zona seleccionada manualmente'))
        st.session_state.pop('ambiguous',None)
        st.rerun()

report=st.session_state.get('report')
if report:
    zone=find_by_name(*report['key'])
    result=report['score']
    st.divider()
    st.caption('ROS TERRITORIAL · FÓRMULA ORIGINAL DEL EXCEL')
    c1,c2=st.columns([1,1.8],vertical_alignment='center')
    with c1:st.metric('Score ROS',f"{result['score100']:.1f} / 100")
    with c2:
        st.subheader(result['clasificacion'])
        st.write(f"{zone['nombre']} · {'CABA' if zone['ambito']=='CABA' else 'Provincia de Buenos Aires'}")
        st.caption('Índice original: '+f"{result['raw']:.3f} / 4")
    st.subheader('Detalle del puntaje')
    for comp in result['parts']:
        left,right=st.columns([3,1],vertical_alignment='center')
        with left:
            st.write(f"**{comp['name']}**")
            st.progress(comp['level']/4)
        with right:st.write(f"{comp['points']:.1f} / {comp['weight']:.1f}")
    with st.expander('Indicadores territoriales del Excel'):
        a,b=st.columns(2)
        with a:
            st.metric('Población',f"{zone['poblacion']:,}".replace(',','.'))
            st.metric('Densidad poblacional',f"{zone['densidad_poblacional']:,.0f}".replace(',','.'))
        with b:
            st.metric('Densidad comercial',f"{zone['densidad_comercial']:,.0f}".replace(',','.'))
            st.metric('Cupo disponible',str(zone['cupo_disponible']))
        st.caption('Fuente: Excel ROS original. Densidad comercial: viajes/km²/día hábil. Indicadores territoriales históricos, no medición de la cuadra.')

    st.divider()
    st.subheader('Supermercados, autoservicios y transporte')
    geo=report.get('geo') or {}
    osm=report.get('osm') or {}
    if not geo.get('verified'):
        st.info('El ROS ya está calculado. No se pudo confirmar la altura y el municipio para buscar locales cercanos; por seguridad no se usan coordenadas aproximadas.')
        with st.expander('Verificar dirección'):
            st.write(report['address'])
            st.caption('La consulta territorial no cambia. Para el análisis por metros se necesitan coordenadas verificadas.')
    else:
        st.caption(f"Punto confirmado: {geo.get('display',report['address'])} · Fuente: {geo.get('provider','Georef')}")
        st.link_button('Ver el punto en OpenStreetMap',
                f"https://www.openstreetmap.org/?mlat={geo['lat']:.6f}&mlon={geo['lon']:.6f}#map=17/{geo['lat']:.6f}/{geo['lon']:.6f}")
        if osm.get('ok'):
            shops=osm.get('supermarkets') or []
            if shops:
                st.caption('Distancias aproximadas en línea recta. OpenStreetMap puede tener sucursales faltantes o desactualizadas.')
                near=label_branches(shops,report.get('rows') or [])[:12]
                st.dataframe([{'Supermercado':p['name'],'Distancia (m)':p['distance_m'],
                                'Tipo':'Supermercado' if p.get('shop')=='supermarket' else 'Autoservicio',
                                'Red':'Cargada' if p['in_uploaded_network'] else 'No identificada'} for p in near],
                            use_container_width=True,hide_index=True)
            else:st.info('La consulta OSM respondió, pero no devolvió supermercados ni autoservicios registrados dentro del radio consultado. Esto no acredita que no existan.')
            t=osm.get('transport') or []
            roads=osm.get('roads') or []
            col1,col2=st.columns(2)
            with col1:st.metric('Paradas/estaciones OSM',len(t))
            with col2:st.metric('Ejes viales OSM',len(roads))
            if t:
                st.caption('Transporte identificado más próximo: '+str(t[0]['kind'])+' · '+f"{t[0]['distance_m']} m aprox.")
            st.caption('Las vías se detectan por cartografía; no representan tiempos ni accesos logísticos verificados.')
        else:
            st.warning('Los servidores de datos comerciales OSM no respondieron. El ROS territorial conserva su valor y no se asignan distancias inventadas.')
            if st.button('Reintentar consulta de supermercados',use_container_width=False):
                with st.spinner('Consultando OpenStreetMap...'):
                    report['osm']=fetch_nearby(geo['lat'],geo['lon'],force=True)
                    shops=(report['osm'] or {}).get('supermarkets',[]) if report['osm'].get('ok') else []
                    report['network']=network_analysis(report['rows'],geo['lat'],geo['lon'],shops,report.get('chain',''))
                    st.session_state['report']=report
                st.rerun()
    st.divider()
    st.subheader('Red propia, canibalización y logística')
    rows=report.get('rows') or []
    if rows:
        st.caption(f"{len(rows)} sucursales cargadas · La red no modifica el ROS territorial")
        analysis=report.get('network')
        if analysis:
            a,b=st.columns(2)
            with a:st.metric('Sucursales con coordenadas',f"{analysis['count_georeferenced']} / {analysis['count_loaded']}")
            with b:st.metric('Más cercana',f"{analysis['nearest_m']:,} m".replace(',','.') if analysis['nearest_m'] is not None else 'Sin datos')
            st.write('**Canibalización:** '+analysis['cannibalization_signal'])
            st.caption('Señal de superposición por cercanía, no estimación de pérdida de ventas. Fuente: '+analysis['nearest_source'])
            st.write('**Proximidad logística a la red**')
            st.caption(f"Dentro de 2 km: {analysis['within_2km']} sucursales · Dentro de 5 km: {analysis['within_5km']} sucursales (distancia lineal)")
            if analysis['nearby']:
                st.dataframe([{'Sucursal propia':p['name'],'Distancia (m)':p['distance_m']} for p in analysis['nearby'][:7]],
                    hide_index=True,use_container_width=True)
            if analysis.get('own_osm_by_brand') and not analysis['count_georeferenced']:
                st.caption('Se detectaron tiendas de la marca en OSM; identificación orientativa sin cotejar cada sucursal del archivo.')
        else:st.caption('No se puede medir la red sin coordenadas verificadas de la dirección.')
    else:
        st.info('No cargaste una red. El score territorial y la búsqueda de supermercados funcionan igualmente. La canibalización se calcula solo cuando se dispone de información de la red propia.')

    st.divider()
    with st.expander('Simulador de facturación · opcional'):
        st.write('10.000 escenarios de facturación anual con una distribución lognormal, como en el simulador del Excel original.')
        st.caption('Ingresá una referencia de ventas propia o comparable. El ROS no permite deducir facturación automáticamente ni reemplaza los datos comerciales.')
        with st.form('simular_facturacion'):
            annual=st.number_input('Facturación anual esperada (en la moneda que uses)',min_value=1.0,value=100000000.0,step=1000000.0,format='%.0f')
            objective=st.number_input('Objetivo de facturación anual (misma moneda)',min_value=1.0,value=100000000.0,step=1000000.0,format='%.0f')
            cv=st.slider('Variabilidad (coeficiente de variación)',min_value=0.05,max_value=0.80,value=0.30,step=0.05,
                help='Debería estimarse a partir de ventas comparables; 30% es solamente un valor ilustrativo.')
            seed=st.number_input('Semilla',min_value=1,max_value=2147483647,value=2026,step=1)
            run=st.form_submit_button('Simular 10.000 escenarios',use_container_width=True)
        if run:
            st.session_state['sim_result']=simulate(annual,objective,cv,10000,seed)
        sim=st.session_state.get('sim_result')
        if sim:
            cols=st.columns(3)
            for col,k,label in zip(cols,('p10','p50','p90'),('P10','P50','P90')):
                with col:st.metric(label,f"{sim[k]:,.0f}".replace(',','.'))
            st.metric('Probabilidad de alcanzar el objetivo',f"{sim['prob_target']:.1%}")
            st.caption(f"Media simulada: {sim['average']:,.0f}".replace(',','.') + ' · Resultado basado en los parámetros ingresados, no una predicción de la dirección.')

    with st.expander('Fuentes y alcance'):
        st.write('ROS de 81 zonas tomado de **ROS integrado con facturación, simulador y ranking.xlsm**. Pesos: población 17,5%, densidad poblacional 17,5%, densidad comercial 27,5% y cupo disponible 37,5%.')
        st.write('La ubicación exacta se verifica con Georef Argentina. Los establecimientos, el transporte y los ejes viales proceden de OpenStreetMap solo cuando la consulta responde. Las distancias son lineales y aproximadas.')
        st.write('Canibalización y logística: señales indicativas de proximidad; no son proyecciones de ventas canibalizadas ni costos operativos.')
else:
    st.caption('Cobertura del ROS histórico: 48 barrios de CABA y 33 municipios de PBA. También se puede evaluar sin subir red de sucursales.')
