"""Score comercial y decisión geográfica para expansión retail (2026-10).

Reglas heurísticas auditables. Evita recomendar aperturas con cobertura incompleta,
excluye el estado edilicio, no convierte proximidad en ventas canibalizadas y
usa TODOS los competidores observados en lugar del más cercano solamente.
"""
from __future__ import annotations
import math

PESOS_POTENCIAL = {'Demanda y mercado': 40., 'Entorno comercial': 30., 'Espacio competitivo': 30.}
PESO_RED_EN_CONVENIENCIA = 25.
MIN_COBERTURA_TOTAL = 85.0
MIN_COBERTURA_POR_FACTOR = 70.0
PESOS_DEMANDA = {'poblacion':.30, 'densidad':.25, 'movilidad':.30, 'nse':.15}
PESOS_ENTORNO = {'actividad':.50, 'accesibilidad':.30, 'vialidad':.20}
PESOS_COMPETENCIA = {'cupo':.25, 'presion_estructural':.20, 'oferta_cercana':.55}
NIVELES = {'Excelente':100.,'Muy bueno':100.,'Bueno':82.,'Aceptable':67.,
           'Regular':52.,'Malo':28.,'Poco':28.,'Muy alto':100.,'Alto':82.}


def acotar(value, low=0., high=100.):
    return max(low, min(high, float(value)))


def num(x):
    if x is None or isinstance(x,bool): return None
    try:
        out=float(x)
        return out if math.isfinite(out) else None
    except (ValueError,TypeError):
        return None


def media_componentes(pairs):
    denominator=sum(float(w) for _,w in pairs if float(w)>0)
    usable=[(num(v),float(w)) for v,w in pairs if num(v) is not None and w>0]
    return (round(sum(v*w for v,w in usable)/sum(w for _,w in usable),1) if usable else None,
            round(sum(w for _,w in usable)/denominator*100,1) if denominator else 0.)


def nivel_poblacional(level, value, peers=None):
    """Serie legacy: blend leve con peers SÓLO cuando hay datos comparables.
    La app informa el año de origen; nunca rebautiza la serie como Censo 2022.
    """
    anchor=NIVELES.get(str(level))
    x=num(value)
    valid=sorted(num(v) for v in (peers or []) if num(v) is not None)
    if x is None:
        return None
    if len(valid)<5:
        return anchor
    below=sum(v<x for v in valid)
    equals=sum(v==x for v in valid)
    percentile=100*(below+equals/2)/len(valid)
    if x>=valid[-1]:percentile=100.
    return round(percentile if anchor is None else .70*anchor+.30*percentile,1)


def score_presion(indice):
    v=num(indice)
    return None if v is None else round(acotar(100/(1+v/12)),1)


def score_cupo(cupo):
    v=num(cupo)
    return None if v is None else round(acotar(100*(1-math.exp(-max(0.,v)/3.5))),1)


def score_competidor(distancia_m,senal=None,cobertura_osm=False):
    d=num(distancia_m)
    if d is None:
        return 100. if cobertura_osm and senal=='No' else None
    return round(acotar(100*(1-.68*math.exp(-max(0.,d)/300.))),1)


def presion_competidores(items, osm_verificado=False):
    """Curva continua sobre todos los supermercados ajenos hasta 1200 m.
    No confunde proximidad con prueba de canibalización ni incluye locales propios.
    1200 m es una referencia espacial, no un área caminable verificada.
    """
    if not osm_verificado:
        return None
    if items is None:
        return None
    pressure=0.
    for store in items:
        if not isinstance(store,dict):
            continue
        d=num(store.get('distance_m'))
        if d is None or d>1200:
            continue
        # Peso muy cercano > a distancia lejana, sin corte binario.
        pressure += math.exp(-max(d,0.)/380.)
    return round(acotar(100*math.exp(-pressure/2.8)),1)


def score_red(distancia_m, count_5km=None, cann_signal=None):
    d=num(distancia_m)
    if d is None:
        return 25. if cann_signal=='Sí' else None
    return round(acotar(100*(1-math.exp(-max(0.,d)/700.))),1)


def evaluar(obs, referencias=None, pesos=None):
    referencias=referencias or {}
    weights=dict(PESOS_POTENCIAL)
    if pesos:
        for k in weights:
            z=num(pesos.get(k))
            if z is not None and z>=0:weights[k]=z
    if sum(weights.values())<=0:
        weights=dict(PESOS_POTENCIAL)

    population_score=nivel_poblacional(obs.get('Población nivel V37'),obs.get('Población'),referencias.get('Población'))
    demand,covd=media_componentes([
        (population_score,.30),
        (nivel_poblacional(obs.get('Densidad nivel V37'),obs.get('Densidad poblacional'),referencias.get('Densidad poblacional')),.25),
        (nivel_poblacional(obs.get('Movilidad nivel V37'),obs.get('Movilidad / flujo comercial'),referencias.get('Movilidad / flujo comercial')),.30),
        (NIVELES.get(obs.get('NSE V37 nivel')),.15),
    ])
    entorno,cove=media_componentes([
        (obs.get('Flujo V37 índice'),.50),
        (obs.get('Accesibilidad V37 índice'),.30),
        (obs.get('Visibilidad V37 índice'),.20),
    ])
    # Cuando hay verificación OSM, considerar el conjunto completo de tiendas.
    verified=bool(obs.get('Cobertura OSM verificada',False))
    multidist=presion_competidores(obs.get('Competidores OSM'),verified)
    if multidist is None:
        multidist=score_competidor(obs.get('Distancia competencia directa m'),
                                   obs.get('Competencia directa'),verified)
    competencia,covc=media_componentes([
        (score_cupo(obs.get('Cupo disponible')),.25),
        (score_presion(obs.get('Competencia ponderada V37')),.20),
        (multidist,.55),
    ])
    red=score_red(obs.get('Distancia misma cadena m'),
                  obs.get('Sinergia logística / red'),obs.get('Canibalización propia'))
    factors={'Demanda y mercado':demand,'Entorno comercial':entorno,
             'Espacio competitivo':competencia,'Red propia':red}
    commercial,_=media_componentes([(factors[f],w) for f,w in weights.items()])
    expansion=(round(.75*commercial+.25*red,1) if commercial is not None and red is not None else None)
    distance=num(obs.get('Distancia misma cadena m'))
    near=(distance is not None and distance<400) or (distance is None and obs.get('Canibalización propia')=='Sí')
    factor_coverage={'Demanda y mercado':covd,'Entorno comercial':cove,
                     'Espacio competitivo':covc,'Red propia':100. if red is not None else 0.}
    coverage=sum(factor_coverage[k]*w for k,w in weights.items())/sum(weights.values())
    essential=[k for k in weights if factor_coverage[k]<MIN_COBERTURA_POR_FACTOR]
    if population_score is None:
        essential.append('Población comparable')
    sufficient=coverage>=MIN_COBERTURA_TOTAL and not essential
    if not sufficient:
        recommendation='DATOS INSUFICIENTES'
    elif red is None:
        recommendation='VERIFICAR RED PROPIA'
    elif near:
        recommendation='EVALUAR CANIBALIZACIÓN'
    elif expansion>=80:
        recommendation='AVANZAR'
    elif expansion>=65:
        recommendation='EVALUAR'
    else:
        recommendation='NO PRIORIZAR'
    return {
        'commercial_score':commercial,'expansion_score':expansion,
        'network_score':red,'factors':factors,'coverage':round(coverage,1),
        'factor_coverage':factor_coverage,'recommendation':recommendation,
        'near_own_m':distance,'cannibalization_alert':near,
        'weights_potential':weights,'sufficient_data':sufficient,
        'missing_essential_factors':essential,
        'method':'Score heurístico; recomendación sujeta a cobertura >=85% y >=70% por dimensión',
        'min_coverage':MIN_COBERTURA_TOTAL,
    }


def reponderar_preliminar(demanda,entorno,espacio,distancia_carrefour_m):
    potencial=round(.4*demanda+.3*entorno+.3*espacio,1)
    red=score_red(distancia_carrefour_m)
    return {'potencial':potencial,'red':red,
            'conveniencia':round(.75*potencial+.25*red,1)}
