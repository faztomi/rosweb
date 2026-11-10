"""Lectura de sensibilidad microlocal V37.1.

No reemplaza el score oficial V37 ni modifica el simulador de facturación.
No se ha ajustado/validado contra ventas reales: es diagnóstico exploratorio.
Los coeficientes, radii de influencia y factor de sensibilidad son supuestos
explícitos y editables bajo control de cambios, no coeficientes estimados.
"""
import math

# Radios de referencia heurísticos: la influencia se desvanece hasta estos radios.
RADIO_COMPETIDOR_M = 750.0
RADIO_RED_PROPIA_M = 1200.0
# Criterios históricos de V37, sin cambios.
UMBRAL_COMPETIDOR_M = 250.0
UMBRAL_RED_PROPIA_M = 400.0
# Coeficientes heredados de los pesos nominales V37:
# Factor Espacio 25% x subfactor competencia cercana 30%;
# Factor Red 20% x subfactor canibalización 60%.
PESO_COMPETIDOR = 0.25 * 0.30
PESO_RED_PROPIA = 0.20 * 0.60
# Suavización deliberada para limitar la escala de la corrección experimental.
# NO es un coeficiente ajustado con datos de desempeño.
SENSIBILIDAD = 0.35


def _finite_nonnegative(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n >= 0 else None


def _gradual_value(distance, radius):
    """Puntaje gradual 30-100 creciente con distancia real geocodificada."""
    return 30.0 + 70.0 * min(float(distance) / radius, 1.0)


def refined_v37(original, competitor_m=None, own_m=None):
    """Devuelve score diagnóstico ajustado y desglose auditable.

    Es una aproximación de primer orden alrededor del V37 original, no un recálculo
    de la fórmula no lineal con todas las observaciones subyacentes. En caso de
    distancia ausente, NO imputa valores ni agrega correcciones.
    """
    score = _finite_nonnegative(original)
    if score is None:
        return {'refined_score': None, 'delta_total': None, 'delta_competitor': None,
                'delta_own': None, 'coverage': 'Sin score de referencia'}
    dc = _finite_nonnegative(competitor_m)
    dn = _finite_nonnegative(own_m)
    delta_comp = None
    delta_own = None
    if dc is not None:
        old = 30.0 if dc <= UMBRAL_COMPETIDOR_M else 100.0
        delta_comp = SENSIBILIDAD * PESO_COMPETIDOR * (_gradual_value(dc, RADIO_COMPETIDOR_M) - old)
    if dn is not None:
        old = 30.0 if dn < UMBRAL_RED_PROPIA_M else 100.0
        delta_own = SENSIBILIDAD * PESO_RED_PROPIA * (_gradual_value(dn, RADIO_RED_PROPIA_M) - old)
    correction = (delta_comp or 0.0) + (delta_own or 0.0)
    score_out = max(0.0, min(100.0, score + correction))
    return {
        'refined_score': round(score_out, 4),
        'delta_total': round(score_out - score, 4),
        'delta_competitor': None if delta_comp is None else round(delta_comp, 4),
        'delta_own': None if delta_own is None else round(delta_own, 4),
        'coverage': 'Completa' if dc is not None and dn is not None else 'Parcial',
        'competitor_m': dc, 'own_m': dn,
    }
