# Código original V37 de la app local (funciones y parámetros antes de la interfaz Streamlit).
# Se conserva el cálculo; la pantalla pública está en app.py.

import json
import html
import math
import os
import re
import time
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

from refinamiento_v37 import refined_v37
from score_comercial import evaluar as evaluar_potencial_y_expansion, PESOS_POTENCIAL
from datos_externos import leer_censo_csv, aplicar_censo, normalizar_ventas_mensuales

# Índice de presentación sobre base 73 (no sustituye el score del modelo).
# La base 73 es una referencia solicitada por el usuario, NO un máximo teórico.
INDICE_RELATIVO_REFERENCIA = 73.0


def indice_relativo_73(score):
    try:
        n = float(score)
        if not math.isfinite(n):
            return None
        return round(100 * n / INDICE_RELATIVO_REFERENCIA, 4)
    except (TypeError, ValueError):
        return None

BASE_DIR = Path(__file__).resolve().parent

with open(BASE_DIR / "model_data.json", "r", encoding="utf-8") as f:
    DATA = json.load(f)

LOCATIONS = DATA["locations"]
# La población Censo 2022 se toma del CSV documentado de los 48 barrios de CABA
# y, opcionalmente, del archivo del usuario para municipios y localidades PBA.
# La serie histórica del JSON no se rotula como si fuera población 2022.

SIM_PARAMS = DATA["simulation_params"]
ZONE_FORMATS = DATA["zone_formats"]
META = DATA["meta"]

NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
OVERPASS_URLS = list(dict.fromkeys([
    OVERPASS_URL,
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]))

# Geocodificación de red propia:
# - Nominatim público admite como máximo 1 solicitud por segundo.
# - Procesamos la red en bloques pequeños y guardamos el avance en session_state.
NOMINATIM_MIN_INTERVAL_SECONDS = float(
    os.getenv("NOMINATIM_MIN_INTERVAL_SECONDS", "1.10")
)
NETWORK_GEOCODE_BATCH_SIZE = int(
    os.getenv("NETWORK_GEOCODE_BATCH_SIZE", "40")
)
NOMINATIM_MAX_RETRIES = int(
    os.getenv("NOMINATIM_MAX_RETRIES", "3")
)

_LAST_NOMINATIM_REQUEST_AT = 0.0


class GeocodingRateLimitError(RuntimeError):
    pass


class GeocodingTemporaryError(RuntimeError):
    pass


def _wait_for_nominatim_slot():
    """Respeta el máximo de 1 solicitud/segundo del Nominatim público."""
    global _LAST_NOMINATIM_REQUEST_AT
    elapsed = time.monotonic() - _LAST_NOMINATIM_REQUEST_AT
    wait_seconds = NOMINATIM_MIN_INTERVAL_SECONDS - elapsed
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _LAST_NOMINATIM_REQUEST_AT = time.monotonic()


# Identificación utilizada por Nominatim y Overpass (OpenStreetMap).
USER_AGENT = os.getenv(
    "OSM_USER_AGENT",
    "AsistenteExpansionRetail/1.0 (analisis de ubicaciones retail)"
)


# Ajuste microlocal fijo: 25% del desvío histórico.
MICRO_INTENSITY_FIXED = 0.25

# Rango ideal del índice de actividad comercial según formato.
ACTIVITY_OPTIMAL_RANGES = {
    "Express": {"ideal_low": 60.0, "ideal_high": 90.0},
    "Market": {"ideal_low": 45.0, "ideal_high": 80.0},
    "Hiper": {"ideal_low": 30.0, "ideal_high": 65.0},
    "Maxi": {"ideal_low": 20.0, "ideal_high": 55.0},
}

# -----------------------------
# Utilidades del modelo
# -----------------------------

def norm(text):
    if text is None:
        return ""
    text = str(text).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\b(partido de|partido del|ciudad autonoma de|caba)\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_road_name(text):
    """
    Devuelve una clave estable para agrupar variantes del mismo nombre vial.
    Ej.: 'Av. José Garibaldi' y 'Avenida José Garibaldi'
    pasan a la misma clave.
    """
    value = norm(text)
    if not value:
        return ""

    replacements = [
        (r"\bavda\b", "avenida"),
        (r"\bav\b", "avenida"),
        (r"\bbv\b", "boulevard"),
        (r"\bblvd\b", "boulevard"),
        (r"\bbulevar\b", "boulevard"),
        (r"\bgral\b", "general"),
        (r"\bpte\b", "presidente"),
        (r"\bdr\b", "doctor"),
        (r"\bing\b", "ingeniero"),
        (r"\bprof\b", "profesor"),
        (r"\bsta\b", "santa"),
        (r"\bsto\b", "santo"),
    ]
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value)

    value = re.sub(r"\s+", " ", value).strip()

    # Para deduplicar también casos como
    # "Avenida José Garibaldi" vs "José Garibaldi".
    core = re.sub(
        r"^(avenida|calle|boulevard|autopista|camino|colectora)\s+",
        "",
        value,
    ).strip()

    return core or value

LOCATION_INDEX = {}
for rec in LOCATIONS:
    LOCATION_INDEX.setdefault(norm(rec["Ubicación"]), []).append(rec)

ALIASES = {
    "general san martin": "General San Martin",
    "san martin": "General San Martin",
    "general rodriguez": "General Rodriguez",
    "lomas de zamora": "Lomas De Zamora",
    "vicente lopez": "Vicente Lopez",
    "jose c paz": "José C. Paz",
    "tres de febrero": "Tres de Febrero",
    "esteban echeverria": "Esteban Echeverria",
    "ituzaingo": "Ituzaingo",
    "lanus": "Lanus",
    "moron": "Morón",
    "zarate": "Zárate",
    "nunez": "Núñez",
    "villa pueyrredon": "Villa Pueyrredón",
    "villa ortuzar": "Villa Ortúzar",
    "velez sarsfield": "Vélez Sarsfield",
}

def sim_param(zone, fmt):
    for row in SIM_PARAMS:
        if row["Zona"] == zone and row["Formato"] == fmt:
            return row
    return None

def fmt_num(x):
    if x is None:
        return "—"
    return f"{x:,.0f}".replace(",", ".")

def fmt_money_k(x):
    if x is None:
        return "—"
    return f"${fmt_num(x)} K"

def pct(x):
    if x is None:
        return "—"
    return f"{100*x:.1f}%".replace(".", ",")

def inject_styles():
    st.markdown(
        """
        <style>
        :root {
            --navy: #15324A;
            --navy-2: #214A67;
            --teal: #2A6F7E;
            --green: #2F7D5B;
            --amber: #B7791F;
            --red: #B54747;
            --ink: #17212B;
            --muted: #667085;
            --line: #E5EAF0;
            --panel: #FFFFFF;
            --bg: #F6F8FB;
        }

        .stApp {
            background: var(--bg);
        }

        .block-container {
            max-width: 1320px;
            padding-top: 1.6rem;
            padding-bottom: 3rem;
        }

        h1, h2, h3, h4 {
            color: var(--ink);
            letter-spacing: -0.01em;
        }

        .hero {
            background: linear-gradient(135deg, var(--navy) 0%, var(--navy-2) 65%, var(--teal) 100%);
            border-radius: 20px;
            padding: 26px 28px;
            color: #fff;
            margin-bottom: 18px;
            box-shadow: 0 8px 28px rgba(21,50,74,.16);
        }
        .hero .eyebrow {
            text-transform: uppercase;
            letter-spacing: .12em;
            font-size: .76rem;
            font-weight: 700;
            opacity: .78;
            margin-bottom: 8px;
        }
        .hero .title {
            font-size: 2.0rem;
            line-height: 1.12;
            font-weight: 780;
            margin-bottom: 8px;
        }
        .hero .subtitle {
            font-size: 1rem;
            line-height: 1.5;
            opacity: .90;
            max-width: 930px;
        }

        .section-kicker {
            color: var(--navy);
            font-size: .78rem;
            text-transform: uppercase;
            letter-spacing: .11em;
            font-weight: 800;
            margin: 18px 0 3px 0;
        }

        .app-card {
            border: 1px solid var(--line);
            border-radius: 16px;
            padding: 15px 16px;
            background: var(--panel);
            box-shadow: 0 2px 8px rgba(16,24,40,.04);
            min-height: 112px;
            height: 100%;
            margin-bottom: 9px;
        }
        .app-card .label {
            font-size: 0.82rem;
            color: var(--muted);
            margin-bottom: 7px;
            font-weight: 650;
        }
        .app-card .value {
            font-size: 1.16rem;
            line-height: 1.25;
            font-weight: 760;
            color: var(--ink);
            overflow-wrap: anywhere;
            white-space: normal;
        }
        .app-card .sub {
            margin-top: 7px;
            font-size: 0.84rem;
            color: var(--muted);
            line-height: 1.35;
            overflow-wrap: anywhere;
            white-space: normal;
        }

        .tone-green { border-top: 4px solid var(--green); }
        .tone-blue { border-top: 4px solid #3A6EA5; }
        .tone-amber { border-top: 4px solid var(--amber); }
        .tone-red { border-top: 4px solid var(--red); }
        .tone-purple { border-top: 4px solid #7564B6; }

        .big-banner {
            border-radius: 17px;
            padding: 17px 19px;
            background: var(--panel);
            border: 1px solid var(--line);
            box-shadow: 0 2px 10px rgba(16,24,40,.05);
            margin: 8px 0 15px 0;
            border-left: 6px solid var(--teal);
        }
        .big-banner .label {
            font-size: 0.82rem;
            color: var(--muted);
            margin-bottom: 6px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: .05em;
        }
        .big-banner .value {
            font-size: 1.32rem;
            line-height: 1.35;
            font-weight: 800;
            color: var(--ink);
            overflow-wrap: anywhere;
            white-space: normal;
        }
        .big-banner .sub {
            margin-top: 7px;
            font-size: 0.90rem;
            color: #475467;
            line-height: 1.45;
        }

        .report-box {
            border-radius: 15px;
            padding: 17px 18px;
            background: #FFFFFF;
            border: 1px solid var(--line);
            line-height: 1.68;
            color: #344054;
            font-size: 0.98rem;
            white-space: normal;
            box-shadow: 0 2px 8px rgba(16,24,40,.035);
        }

        .recommend-box {
            border-radius: 16px;
            padding: 16px 18px;
            background: #F8FAFC;
            border: 1px solid var(--line);
            margin: 8px 0 15px 0;
        }
        .recommend-box .label {
            font-size: .78rem;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: .08em;
            font-weight: 800;
            margin-bottom: 6px;
        }
        .recommend-box .value {
            font-size: 1.27rem;
            line-height: 1.35;
            font-weight: 800;
            color: var(--ink);
            overflow-wrap: anywhere;
        }

        .panel-pro, .panel-con {
            border-radius: 15px;
            padding: 15px 17px;
            min-height: 100%;
            border: 1px solid var(--line);
            background: #fff;
        }
        .panel-pro { border-left: 5px solid var(--green); }
        .panel-con { border-left: 5px solid var(--amber); }

        div[data-testid="stMetric"] {
            background: #FFFFFF;
            border: 1px solid var(--line);
            padding: 12px 14px;
            border-radius: 14px;
            box-shadow: 0 2px 8px rgba(16,24,40,.035);
        }
        div[data-testid="stMetricLabel"] {
            color: var(--muted);
            font-weight: 650;
        }
        div[data-testid="stMetricValue"] {
            color: var(--ink);
            font-weight: 760;
        }

        div[data-testid="stSidebar"] {
            background: #F1F5F9;
            border-right: 1px solid var(--line);
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
        }
        .stTabs [data-baseweb="tab"] {
            background: #FFFFFF;
            border: 1px solid var(--line);
            border-radius: 10px 10px 0 0;
            padding: 8px 14px;
        }

        .stButton > button {
            border-radius: 10px;
            font-weight: 700;
        }

        /* ---------------------------------------------------------
           LEGIBILIDAD / CONTRASTE
           Fuerza una interfaz clara incluso si Streamlit o el navegador
           tienen un tema oscuro activo.
        ---------------------------------------------------------- */
        .stApp,
        .stAppViewContainer,
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"] {
            background: var(--bg) !important;
            color: var(--ink) !important;
        }

        /* Texto general */
        .stApp [data-testid="stMarkdownContainer"] p,
        .stApp [data-testid="stMarkdownContainer"] li,
        .stApp [data-testid="stMarkdownContainer"] span,
        .stApp [data-testid="stMarkdownContainer"] strong,
        .stApp [data-testid="stMarkdownContainer"] em,
        .stApp [data-testid="stText"],
        .stApp label,
        .stApp small {
            color: var(--ink);
        }

        /* La portada es la única zona con texto blanco intencional */
        .hero,
        .hero * {
            color: #FFFFFF !important;
        }

        /* Subtítulos, captions y ayudas */
        [data-testid="stCaptionContainer"],
        [data-testid="stCaptionContainer"] *,
        .stApp .stCaption,
        .stApp .stCaption * {
            color: #5F6B7A !important;
        }

        /* Sidebar */
        [data-testid="stSidebar"],
        [data-testid="stSidebar"] > div {
            background: #F1F5F9 !important;
            color: var(--ink) !important;
        }
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] div {
            color: var(--ink);
        }

        /* Inputs */
        div[data-baseweb="input"] > div,
        div[data-baseweb="base-input"],
        .stTextInput input,
        .stNumberInput input,
        .stSelectbox div[data-baseweb="select"] > div {
            background: #FFFFFF !important;
            color: var(--ink) !important;
            border-color: #CBD5E1 !important;
        }
        .stTextInput input,
        .stNumberInput input {
            -webkit-text-fill-color: var(--ink) !important;
            caret-color: var(--navy) !important;
        }
        .stTextInput input::placeholder,
        .stNumberInput input::placeholder {
            color: #8A94A3 !important;
            -webkit-text-fill-color: #8A94A3 !important;
            opacity: 1 !important;
        }

        /* Sliders */
        [data-testid="stSlider"] p,
        [data-testid="stSlider"] span,
        [data-testid="stSlider"] label {
            color: var(--ink) !important;
        }

        /* Métricas */
        div[data-testid="stMetric"] {
            background: #FFFFFF !important;
        }
        div[data-testid="stMetricLabel"],
        div[data-testid="stMetricLabel"] * {
            color: #667085 !important;
        }
        div[data-testid="stMetricValue"],
        div[data-testid="stMetricValue"] * {
            color: var(--ink) !important;
        }
        div[data-testid="stMetricDelta"],
        div[data-testid="stMetricDelta"] * {
            color: #475467;
        }

        /* Tabs: nunca blanco sobre blanco */
        .stTabs [data-baseweb="tab-list"] {
            background: transparent !important;
        }
        .stTabs [data-baseweb="tab"] {
            background: #FFFFFF !important;
            color: #475467 !important;
        }
        .stTabs [data-baseweb="tab"] * {
            color: #475467 !important;
        }
        .stTabs [aria-selected="true"] {
            background: var(--navy) !important;
            border-color: var(--navy) !important;
        }
        .stTabs [aria-selected="true"] *,
        .stTabs [aria-selected="true"] {
            color: #FFFFFF !important;
        }

        /* Botones */
        .stButton > button,
        .stFormSubmitButton > button {
            background: var(--navy) !important;
            color: #FFFFFF !important;
            border: 1px solid var(--navy) !important;
            min-height: 42px;
        }
        .stButton > button *,
        .stFormSubmitButton > button * {
            color: #FFFFFF !important;
        }
        .stButton > button:hover,
        .stFormSubmitButton > button:hover {
            background: var(--navy-2) !important;
            border-color: var(--navy-2) !important;
        }
        .stButton > button:disabled {
            background: #E2E8F0 !important;
            border-color: #E2E8F0 !important;
            color: #64748B !important;
        }
        .stButton > button:disabled * {
            color: #64748B !important;
        }

        /* Expanders */
        [data-testid="stExpander"] {
            background: #FFFFFF !important;
            border: 1px solid var(--line) !important;
            border-radius: 12px !important;
        }
        [data-testid="stExpander"] summary,
        [data-testid="stExpander"] summary *,
        [data-testid="stExpanderDetails"],
        [data-testid="stExpanderDetails"] * {
            color: var(--ink) !important;
        }

        /* Alertas */
        [data-testid="stAlert"] {
            color: var(--ink) !important;
        }
        [data-testid="stAlert"] p,
        [data-testid="stAlert"] span,
        [data-testid="stAlert"] div {
            color: var(--ink);
        }

        /* Tablas markdown */
        .stApp table {
            background: #FFFFFF !important;
            color: var(--ink) !important;
            border-collapse: collapse;
            width: 100%;
        }
        .stApp table th {
            background: #EAF0F5 !important;
            color: var(--navy) !important;
            font-weight: 750;
        }
        .stApp table td {
            background: #FFFFFF !important;
            color: var(--ink) !important;
        }
        .stApp table th,
        .stApp table td {
            border-color: #D9E1E8 !important;
        }

        /* Links */
        .stApp a {
            color: #1E5F8A;
        }
        .stApp a:hover {
            color: var(--navy);
        }
        .hero a,
        .hero a:hover {
            color: #FFFFFF !important;
        }

        /* Componentes personalizados: texto siempre explícito */
        .app-card,
        .big-banner,
        .report-box,
        .recommend-box,
        .panel-pro,
        .panel-con {
            color: var(--ink) !important;
        }
        .app-card *,
        .big-banner *,
        .report-box *,
        .recommend-box *,
        .panel-pro *,
        .panel-con * {
            color: inherit;
        }
        .app-card .label,
        .big-banner .label,
        .recommend-box .label,
        .app-card .sub,
        .big-banner .sub {
            color: var(--muted) !important;
        }
        .app-card .value,
        .big-banner .value,
        .recommend-box .value {
            color: var(--ink) !important;
        }

        /* Evitar textos comprimidos/cortados */
        .app-card .value,
        .big-banner .value,
        .recommend-box .value,
        div[data-testid="stMetricValue"] {
            overflow: visible !important;
            text-overflow: clip !important;
            white-space: normal !important;
            word-break: normal !important;
            overflow-wrap: anywhere !important;
        }

        /* Mejor contraste de la navegación superior */
        header[data-testid="stHeader"] {
            background: rgba(246,248,251,.96) !important;
        }

        /* Mobile / pantallas angostas */
        @media (max-width: 900px) {
            .hero .title { font-size: 1.65rem; }
            .app-card .value { font-size: 1.05rem; }
            .big-banner .value { font-size: 1.16rem; }
            .block-container { padding-left: 1rem; padding-right: 1rem; }
        }


        /* Tarjetas compactas para evitar cortes de etiquetas y números */
        .compact-card {
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 12px 13px;
            background: #FFFFFF;
            box-shadow: 0 2px 7px rgba(16,24,40,.035);
            min-height: 92px;
            height: 100%;
            margin-bottom: 9px;
        }
        .compact-card .label {
            color: #667085 !important;
            font-size: 0.73rem !important;
            line-height: 1.20 !important;
            font-weight: 700 !important;
            margin-bottom: 7px;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
        }
        .compact-card .value {
            color: var(--ink) !important;
            font-size: 1.00rem !important;
            line-height: 1.22 !important;
            font-weight: 780 !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            word-break: normal !important;
        }
        .compact-card .sub {
            color: #667085 !important;
            font-size: 0.76rem !important;
            line-height: 1.28 !important;
            margin-top: 5px;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
        }

        .money-card {
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 13px 14px;
            background: #FFFFFF;
            box-shadow: 0 2px 7px rgba(16,24,40,.035);
            min-height: 98px;
            height: 100%;
            margin-bottom: 9px;
        }
        .money-card .label {
            color: #667085 !important;
            font-size: 0.74rem !important;
            line-height: 1.20 !important;
            font-weight: 700 !important;
            margin-bottom: 7px;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
        }
        .money-card .value {
            color: var(--ink) !important;
            font-size: 0.96rem !important;
            line-height: 1.20 !important;
            font-weight: 800 !important;
            letter-spacing: -0.01em;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
        }
        .money-card .sub {
            color: #667085 !important;
            font-size: 0.75rem !important;
            line-height: 1.25 !important;
            margin-top: 5px;
        }

        @media (max-width: 1050px) {
            .compact-card .label,
            .money-card .label {
                font-size: 0.70rem !important;
            }
            .compact-card .value {
                font-size: 0.92rem !important;
            }
            .money-card .value {
                font-size: 0.88rem !important;
            }
        }


        /* Fallback global: métricas nativas un poco más compactas */
        div[data-testid="stMetricValue"],
        div[data-testid="stMetricValue"] * {
            font-size: 1.05rem !important;
            line-height: 1.18 !important;
        }
        div[data-testid="stMetricLabel"],
        div[data-testid="stMetricLabel"] * {
            font-size: 0.76rem !important;
            line-height: 1.18 !important;
            white-space: normal !important;
        }



        .executive-result {
            border: 1px solid var(--line);
            border-radius: 20px;
            background: #FFFFFF;
            padding: 22px 24px;
            box-shadow: 0 8px 26px rgba(16,24,40,.07);
            margin: 10px 0 16px 0;
        }
        .executive-result .meta {
            color: #667085 !important;
            font-size: .78rem !important;
            text-transform: uppercase;
            letter-spacing: .09em;
            font-weight: 800;
            margin-bottom: 8px;
        }
        .executive-result .row {
            display: flex;
            gap: 28px;
            align-items: flex-end;
            justify-content: space-between;
            flex-wrap: wrap;
        }
        .executive-result .score {
            font-size: 2.65rem !important;
            line-height: 1 !important;
            font-weight: 850 !important;
            letter-spacing: -.04em;
            color: var(--ink) !important;
        }
        .executive-result .score span {
            font-size: 1rem !important;
            color: #667085 !important;
            letter-spacing: 0;
            font-weight: 700 !important;
        }
        .executive-result .decision {
            font-size: 1.35rem !important;
            line-height: 1.15 !important;
            font-weight: 850 !important;
            text-align: right;
        }
        .executive-result .foot {
            margin-top: 12px;
            color: #667085 !important;
            font-size: .86rem !important;
            line-height: 1.4 !important;
        }
        .executive-chip {
            display: inline-block;
            border-radius: 999px;
            padding: 5px 10px;
            margin-right: 6px;
            margin-top: 6px;
            background: #F1F5F9;
            color: #475467 !important;
            font-size: .76rem !important;
            font-weight: 700 !important;
        }

        /* V14: lectura más cómoda de tablas y textos explicativos */
        [data-testid="stDataFrame"] {
            border: 1px solid #D9E1E8;
            border-radius: 12px;
            overflow: hidden;
        }
        .stAlert p {
            line-height: 1.45 !important;
        }

        </style>
        """,
        unsafe_allow_html=True,
    )

def render_hero():
    st.markdown(
        """
        <div class="hero">
            <div class="eyebrow">Evaluador comercial · nueva ubicación</div>
            <div class="title">Evaluador de ubicaciones</div>
            <div class="subtitle">
                Ingresá una dirección y obtené una lectura ejecutiva: score de ubicación,
                fortalezas, riesgos, competencia, red propia y facturación estimada.
                Empezá con una dirección nueva: no hay ubicaciones de evaluación precargadas.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def section_title(kicker, title):
    st.markdown(f'<div class="section-kicker">{html.escape(kicker)}</div>', unsafe_allow_html=True)
    st.subheader(title)

def render_recommendation(label, value, sub=None):
    tone = _tone_from_value(value)
    border = {
        "green": "#2F7D5B",
        "blue": "#3A6EA5",
        "amber": "#B7791F",
        "red": "#B54747",
        "purple": "#7564B6",
    }.get(tone, "#2A6F7E")
    extra = f'<div class="sub">{html.escape(str(sub))}</div>' if sub else ''
    st.markdown(
        f"""
        <div class="recommend-box" style="border-left:6px solid {border};">
            <div class="label">{html.escape(str(label))}</div>
            <div class="value">{html.escape(str(value))}</div>
            {extra}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_executive_result(score, recommendation, company, fmt_label, coverage, confidence):
    tone = _tone_from_value(recommendation)
    accent = {
        "green": "#2F7D5B",
        "blue": "#3A6EA5",
        "amber": "#B7791F",
        "red": "#B54747",
        "purple": "#7564B6",
    }.get(tone, "#2A6F7E")

    score_value = "—" if score is None else f"{float(score):.1f}"
    st.markdown(
        f"""
        <div class="executive-result" style="border-top:5px solid {accent};">
            <div class="meta">Resultado ejecutivo</div>
            <div class="row">
                <div>
                    <div class="score">{html.escape(score_value)} <span>/ 100</span></div>
                    <div class="foot">Potencial comercial</div>
                    <div class="foot">{html.escape(company)} · {html.escape(fmt_label)}</div>
                </div>
                <div>
                    <div class="decision" style="color:{accent} !important;">
                        {html.escape(str(recommendation))}
                    </div>
                    <div class="foot" style="text-align:right;">
                        Cobertura {coverage:.0f}% · Calidad de datos {html.escape(str(confidence))}
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_compact_card(label, value, sub=None):
    extra = f'<div class="sub">{html.escape(str(sub))}</div>' if sub else ''
    st.markdown(
        f"""
        <div class="compact-card">
            <div class="label">{html.escape(str(label))}</div>
            <div class="value">{html.escape(str(value))}</div>
            {extra}
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_money_card(label, value, sub=None):
    extra = f'<div class="sub">{html.escape(str(sub))}</div>' if sub else ''
    st.markdown(
        f"""
        <div class="money-card">
            <div class="label">{html.escape(str(label))}</div>
            <div class="value">{html.escape(str(value))}</div>
            {extra}
        </div>
        """,
        unsafe_allow_html=True,
    )

def _tone_from_value(value):
    txt = str(value).strip().upper()
    if "NO PRIORIZAR" in txt or "CASO DÉBIL" in txt or "CASO DEBIL" in txt or "MALO" in txt:
        return "red"
    if "PRIORIDAD ALTA" in txt or "AVANZAR" in txt or "MUY BUENO" in txt:
        return "green"
    if "EVALUAR" in txt or "REGULAR" in txt or "MEDIA" in txt:
        return "amber"
    if "BUENO" in txt or "OPORTUNIDAD ATRACTIVA" in txt or "ALTA" in txt:
        return "blue"
    return "purple"

def render_card(label, value, sub=None, tone=None):
    tone = tone or _tone_from_value(value)
    extra = f'<div class="sub">{html.escape(str(sub))}</div>' if sub else ''
    st.markdown(
        f"""
        <div class="app-card tone-{tone}">
            <div class="label">{html.escape(str(label))}</div>
            <div class="value">{html.escape(str(value))}</div>
            {extra}
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_banner(label, value, sub=None):
    extra = f'<div class="sub">{html.escape(str(sub))}</div>' if sub else ''
    st.markdown(
        f"""
        <div class="big-banner">
            <div class="label">{html.escape(str(label))}</div>
            <div class="value">{html.escape(str(value))}</div>
            {extra}
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_report(text):
    st.markdown(
        f'<div class="report-box">{html.escape(str(text))}</div>',
        unsafe_allow_html=True,
    )

def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlambda/2)**2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1-a))


def canonical_supermarket_name(text):
    value = norm(text)
    if not value or value == "sin nombre":
        return ""
    # Normalizaciones ligeras de cadena/formato para comparar duplicados.
    value = re.sub(r"\b(carrefour)\s+(market|express|maxi|hiper)\b", r"\1", value)
    value = re.sub(r"\bdia\s*%\b", "dia", value)
    return re.sub(r"\s+", " ", value).strip()


def same_osm_supermarket(a, b):
    if (
        a.get("lat") is None or a.get("lon") is None
        or b.get("lat") is None or b.get("lon") is None
    ):
        return False

    distance = haversine_m(
        a["lat"], a["lon"],
        b["lat"], b["lon"],
    )

    # Dos objetos OSM casi en el mismo punto suelen ser nodo + polígono
    # del mismo establecimiento.
    if distance <= 45:
        return True

    name_a = canonical_supermarket_name(a.get("name"))
    name_b = canonical_supermarket_name(b.get("name"))

    # Mismo nombre/cadena a corta distancia: una sola sucursal.
    if name_a and name_b and name_a == name_b and distance <= 110:
        return True

    # Si uno quedó sin nombre, usamos una tolerancia menor.
    if (not name_a or not name_b) and distance <= 30:
        return True

    return False


def dedupe_osm_supermarkets(items):
    unique = []

    for item in sorted(
        [dict(x) for x in items if x.get("distance_m") is not None],
        key=lambda x: x["distance_m"],
    ):
        duplicate_index = None
        for idx, existing in enumerate(unique):
            if same_osm_supermarket(item, existing):
                duplicate_index = idx
                break

        if duplicate_index is None:
            unique.append(item)
        else:
            existing = unique[duplicate_index]

            # Preferir el registro con nombre y luego el más cercano.
            item_named = canonical_supermarket_name(item.get("name"))
            existing_named = canonical_supermarket_name(existing.get("name"))

            replace = False
            if item_named and not existing_named:
                replace = True
            elif bool(item_named) == bool(existing_named):
                if item["distance_m"] < existing["distance_m"]:
                    replace = True

            if replace:
                unique[duplicate_index] = item

    return sorted(unique, key=lambda x: x["distance_m"])


def element_point(el):
    if "lat" in el and "lon" in el:
        return float(el["lat"]), float(el["lon"])
    center = el.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None

# -----------------------------
# Geocodificación y OSM
# -----------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def geocode_address(
    address,
    respect_rate_limit=False,
    max_retries=None,
):
    """
    Geocodifica una dirección sin dejar que un HTTP 429 rompa toda la app.

    Para cargas masivas:
    - una sola hebra;
    - intervalo > 1 segundo;
    - reintentos con espera;
    - el proceso se detiene de forma controlada si el servicio sigue limitado.
    """
    address = str(address or "").strip()
    if not address:
        return None

    retries = (
        NOMINATIM_MAX_RETRIES
        if max_retries is None
        else max(int(max_retries), 0)
    )

    params = {
        "q": address,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": 5,
        "countrycodes": "ar",
    }
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(retries + 1):
        if respect_rate_limit:
            _wait_for_nominatim_slot()

        try:
            resp = requests.get(
                NOMINATIM_URL,
                params=params,
                headers=headers,
                timeout=float(os.getenv("ROS_GEOCODE_TIMEOUT", "15")),
            )
        except requests.RequestException as exc:
            if attempt >= retries:
                raise GeocodingTemporaryError(
                    "No se pudo conectar con el servicio de geocodificación."
                ) from exc
            time.sleep(min(2.0 * (attempt + 1), 6.0))
            continue

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            try:
                retry_seconds = float(retry_after)
            except (TypeError, ValueError):
                retry_seconds = min(3.0 * (2 ** attempt), 20.0)

            if attempt >= retries:
                raise GeocodingRateLimitError(
                    "OpenStreetMap limitó temporalmente las consultas. "
                    "El avance ya localizado se conserva."
                )

            time.sleep(max(retry_seconds, NOMINATIM_MIN_INTERVAL_SECONDS))
            continue

        if resp.status_code in (500, 502, 503, 504):
            if attempt >= retries:
                raise GeocodingTemporaryError(
                    "El servicio de geocodificación está temporalmente ocupado."
                )
            time.sleep(min(2.0 * (2 ** attempt), 10.0))
            continue

        resp.raise_for_status()
        results = resp.json()
        return results[0] if results else None

    return None

def resolve_model_location(geo):
    addr = (geo or {}).get("address", {})
    display = (geo or {}).get("display_name", "")

    candidates = []
    fields = [
        "suburb", "neighbourhood", "quarter", "borough",
        "city_district", "municipality", "county",
        "town", "city", "village"
    ]
    for field in fields:
        if addr.get(field):
            candidates.append(addr[field])
    candidates.extend([p.strip() for p in display.split(",") if p.strip()])

    for candidate in candidates:
        n = norm(candidate)
        if n in ALIASES:
            n = norm(ALIASES[n])
        if n in LOCATION_INDEX:
            return LOCATION_INDEX[n][0], candidate

    for candidate in candidates:
        n = norm(candidate)
        for key, rows in LOCATION_INDEX.items():
            if len(key) >= 5 and (key in n or n in key):
                return rows[0], candidate
    return None, None

@st.cache_data(ttl=86400, show_spinner=False)
def query_osm_microlocation(lat, lon):
    """
    Misma consulta microlocal del motor V37.
    Si Overpass devuelve 429/502/503/504 o timeout, rota de servidor y reintenta.
    """
    q = f"""
    [out:json][timeout:30];
    (
      nwr(around:1200,{lat},{lon})["shop"="supermarket"];
      nwr(around:5000,{lat},{lon})["shop"="supermarket"]["name"~"Carrefour",i];
      nwr(around:5000,{lat},{lon})["shop"="supermarket"]["brand"~"Carrefour",i];
      nwr(around:600,{lat},{lon})["shop"];
      nwr(around:600,{lat},{lon})["amenity"~"cafe|restaurant|fast_food|pharmacy"];
      nwr(around:600,{lat},{lon})["highway"="bus_stop"];
      nwr(around:600,{lat},{lon})["public_transport"~"platform|stop_position|station"];
      nwr(around:600,{lat},{lon})["railway"~"station|halt|tram_stop|subway_entrance"];
      way(around:500,{lat},{lon})["highway"~"motorway|trunk|primary|secondary|tertiary"];
    );
    out center tags;
    """
    headers = {"User-Agent": USER_AGENT}
    last_error = None

    # Límite de espera por servidor. Sin límite, 9 * 75 segundos
    # hacían que la consola pareciera congelada.
    attempts = max(1, min(6, int(os.getenv("ROS_OVERPASS_ATTEMPTS", "3"))))
    timeout_seconds = max(8, min(90, float(os.getenv("ROS_OVERPASS_TIMEOUT", "25"))))
    for attempt in range(attempts):
        endpoint = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
        print(f"       [OSM] Consulta {attempt+1}/{attempts} a {endpoint} (límite {timeout_seconds:g} s)", flush=True)
        try:
            resp = requests.post(
                endpoint,
                data={"data": q},
                headers=headers,
                timeout=(8, timeout_seconds),
            )
            if resp.status_code in (429, 502, 503, 504):
                last_error = RuntimeError(
                    f"Overpass temporal {resp.status_code} en {endpoint}"
                )
            else:
                resp.raise_for_status()
                return resp.json()
        except requests.RequestException as exc:
            last_error = exc

        print(f"       [OSM] Sin respuesta válida: {type(last_error).__name__ if last_error else 'error HTTP'}", flush=True)
        if attempt < attempts-1:
            time.sleep(min(5.0, 1.5 + attempt * 1.2))

    raise RuntimeError(
        "OpenStreetMap/Overpass no respondió después de los intentos configurados. "
        "Usá 'Reintentar fallidas' sin volver a correr las direcciones que ya salieron."
    ) from last_error

def derive_micro_metrics(lat, lon, osm_json):
    elements = (osm_json or {}).get("elements", [])
    seen = set()

    supermarkets = []
    transit_500 = []
    raw_poi_candidates = []

    # -----------------------------
    # POI: clasificación + ponderación
    # -----------------------------
    food_retail = {
        "supermarket", "convenience", "grocery", "greengrocer",
        "butcher", "bakery", "deli", "beverages", "dairy",
        "frozen_food", "seafood", "health_food", "confectionery",
    }

    def classify_poi(tags):
        shop = tags.get("shop")
        amenity = tags.get("amenity")

        if shop in food_retail:
            return "Alimentos / supermercado", 1.00
        if amenity == "pharmacy" or shop in ("pharmacy", "chemist"):
            return "Farmacia", 0.80
        if amenity in ("restaurant", "cafe", "fast_food"):
            return "Gastronomía", 0.70
        if shop:
            return "Comercio minorista", 0.50
        return None, 0.0

    def distance_weight(distance_m):
        if distance_m is None or distance_m > 500:
            return 0.0
        if distance_m <= 150:
            return 1.00
        if distance_m <= 300:
            return 0.75
        return 0.50

    def same_physical_poi(a, b):
        if (
            a.get("lat") is None or a.get("lon") is None
            or b.get("lat") is None or b.get("lon") is None
        ):
            return False

        d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        name_a = norm(a.get("name"))
        name_b = norm(b.get("name"))

        # Mismo nombre muy cerca: asumir mismo establecimiento representado
        # por nodo/polígono distintos.
        if name_a and name_b and name_a == name_b and d <= 60:
            return True

        # POI sin nombre: criterio mucho más conservador.
        if (
            not name_a and not name_b
            and a.get("source_type") == b.get("source_type")
            and a.get("source_value") == b.get("source_value")
            and d <= 15
        ):
            return True
        return False

    # -----------------------------
    # Vías principales
    # -----------------------------
    road_candidates = {}

    road_class_weights = {
        "motorway": 1.00,
        "trunk": 1.00,
        "primary": 0.85,
        "secondary": 0.60,
        "tertiary": 0.40,
    }
    road_class_labels = {
        "motorway": "Autopista",
        "trunk": "Corredor troncal",
        "primary": "Vía primaria",
        "secondary": "Vía secundaria",
        "tertiary": "Vía terciaria",
    }

    for el in elements:
        uid = (el.get("type"), el.get("id"))
        if uid in seen:
            continue
        seen.add(uid)

        tags = el.get("tags") or {}
        pt = element_point(el)
        dist = None
        if pt:
            dist = haversine_m(lat, lon, pt[0], pt[1])

        shop = tags.get("shop")
        amenity = tags.get("amenity")
        highway = tags.get("highway")
        public_transport = tags.get("public_transport")
        railway = tags.get("railway")

        if shop == "supermarket":
            supermarkets.append({
                "name": tags.get("name") or tags.get("brand") or "Sin nombre",
                "brand": tags.get("brand") or "",
                "operator": tags.get("operator") or "",
                "type": shop,
                "distance_m": dist,
                "lat": pt[0] if pt else None,
                "lon": pt[1] if pt else None,
                "source": "OpenStreetMap",
            })

        # POI comerciales a 500 m.
        if dist is not None and dist <= 500 and pt:
            category, type_weight = classify_poi(tags)
            if category:
                raw_name = tags.get("name") or tags.get("brand") or ""
                source_value = shop or amenity or ""
                d_weight = distance_weight(dist)
                raw_poi_candidates.append({
                    "uid": uid,
                    "name": raw_name,
                    "category": category,
                    "type_weight": type_weight,
                    "distance_weight": d_weight,
                    "contribution": type_weight * d_weight,
                    "distance_m": float(dist),
                    "lat": float(pt[0]),
                    "lon": float(pt[1]),
                    "source_type": "shop" if shop else "amenity",
                    "source_value": source_value,
                })

        if dist is not None and dist <= 500:
            if (
                highway == "bus_stop"
                or public_transport in ("platform", "stop_position", "station")
                or railway in ("station", "halt", "tram_stop", "subway_entrance")
            ):
                transit_500.append(uid)

        if el.get("type") == "way" and highway in road_class_weights:
            raw_name = (
                tags.get("name")
                or tags.get("ref")
                or tags.get("official_name")
                or ""
            )
            clean_name = canonical_road_name(raw_name)

            if clean_name:
                road_key = ("named", clean_name)
                display_name = raw_name
            else:
                road_key = ("unnamed", highway)
                display_name = f"{road_class_labels.get(highway, highway)} sin nombre"

            weight = road_class_weights[highway]
            existing = road_candidates.get(road_key)
            if existing is None or weight > existing["weight"]:
                road_candidates[road_key] = {
                    "name": display_name,
                    "highway": highway,
                    "class_label": road_class_labels.get(highway, highway),
                    "weight": weight,
                }

    # -----------------------------
    # Deduplicación de POI
    # -----------------------------
    unique_pois = []
    for candidate in sorted(
        raw_poi_candidates,
        key=lambda x: (-x["contribution"], x["distance_m"])
    ):
        duplicate_index = None
        for i, existing in enumerate(unique_pois):
            if same_physical_poi(candidate, existing):
                duplicate_index = i
                break

        if duplicate_index is None:
            unique_pois.append(candidate)
        else:
            # Conservar la representación más útil: mayor aporte / menor distancia.
            existing = unique_pois[duplicate_index]
            if (
                candidate["contribution"] > existing["contribution"]
                or (
                    candidate["contribution"] == existing["contribution"]
                    and candidate["distance_m"] < existing["distance_m"]
                )
            ):
                unique_pois[duplicate_index] = candidate

    unique_pois = sorted(unique_pois, key=lambda x: x["distance_m"])

    poi_weighted_raw = sum(p["contribution"] for p in unique_pois)
    # Saturación: evita que zonas hipercomerciales "exploten" el indicador.
    # El salto de 5 a 20 POI relevantes importa mucho más que el de 100 a 115.
    activity_score = 100.0 * saturating(poi_weighted_raw, 12.0)

    poi_breakdown = {}
    for p in unique_pois:
        row = poi_breakdown.setdefault(
            p["category"],
            {"count": 0, "weighted": 0.0},
        )
        row["count"] += 1
        row["weighted"] += p["contribution"]

    poi_top = sorted(
        unique_pois,
        key=lambda x: (-x["contribution"], x["distance_m"])
    )[:15]

    # -----------------------------
    # Índice vial
    # -----------------------------
    major_roads = sorted(
        road_candidates.values(),
        key=lambda x: (-x["weight"], norm(x["name"]))
    )
    road_weighted_total = sum(r["weight"] for r in major_roads)
    road_access_component = saturating(road_weighted_total, 2.5)
    road_access_score = 100.0 * road_access_component

    supermarkets_raw = [
        s for s in supermarkets
        if s["distance_m"] is not None
    ]
    supermarkets = dedupe_osm_supermarkets(supermarkets_raw)

    supers_500 = [
        s for s in supermarkets
        if s["distance_m"] <= 500
    ]
    supers_1000 = [
        s for s in supermarkets
        if s["distance_m"] <= 1000
    ]
    nearest = (
        supermarkets[0]["distance_m"]
        if supermarkets else None
    )

    named_supers = supermarkets[:12]

    return {
        "supermarkets_500": len(supers_500),
        "supermarkets_1000": len(supers_1000),
        "nearest_supermarket_m": nearest,

        # Compatibilidad + métricas nuevas de actividad.
        "shops_500": sum(1 for p in unique_pois if p["source_type"] == "shop"),
        "activity_500": len(unique_pois),
        "activity_raw_records_500": len(raw_poi_candidates),
        "activity_relevant_pois_500": len(unique_pois),
        "activity_weighted_raw": poi_weighted_raw,
        "activity_score": activity_score,
        "poi_breakdown": poi_breakdown,
        "poi_top": poi_top,

        "transit_500": len(set(transit_500)),
        "major_roads_500": len(major_roads),
        "major_roads": major_roads,
        "road_weighted_total": road_weighted_total,
        "road_access_score": road_access_score,

        "nearby_supermarkets": named_supers,
        "all_supermarkets": supermarkets,
        "supermarkets_raw_records": len(supermarkets_raw),
        "supermarkets_unique_records": len(supermarkets),
        "competition_source": "OpenStreetMap",
        "competition_weighted": None,
    }


def _chain_aliases(company_name):
    aliases = CHAIN_OSM_ALIASES.get(str(company_name or "").strip(), [])
    return [canonical_supermarket_name(x) for x in aliases if x]


def _supermarket_matches_chain(item, company_name):
    fields = [
        canonical_supermarket_name(item.get("name")),
        canonical_supermarket_name(item.get("brand")),
        canonical_supermarket_name(item.get("operator")),
    ]
    fields = [x for x in fields if x]
    if not fields:
        return False
    for value in fields:
        padded = f" {value} "
        for alias in _chain_aliases(company_name):
            if not alias:
                continue
            if value == alias or padded.find(f" {alias} ") >= 0 or value.startswith(alias + " "):
                return True
    return False


def _osm_store_matches_loaded_network(item, own_metrics, max_distance_m=80.0):
    if not own_metrics or not own_metrics.get("available"):
        return False
    ilat, ilon = item.get("lat"), item.get("lon")
    if ilat is None or ilon is None:
        return False
    for store in own_metrics.get("stores_all", []):
        slat, slon = store.get("lat"), store.get("lon")
        if slat is None or slon is None:
            continue
        if haversine_m(float(ilat), float(ilon), float(slat), float(slon)) <= max_distance_m:
            return True
    return False


def binary_proximity_signals(metrics, own_metrics=None, company_name=None):
    """
    Devuelve las dos señales binarias del modelo:
    - competencia directa = Sí cuando existe OTRO supermercado a <= 250 m;
    - canibalización = Sí cuando existe un supermercado de la MISMA cadena a < 400 m.

    Para reconocer la misma cadena se usa el nombre/brand de OSM y, si la empresa
    cargó su red, también la proximidad a los puntos propios geocodificados.
    """
    items = dedupe_osm_supermarkets(metrics.get("all_supermarkets", []))

    own_osm = []
    competitors = []
    for item in items:
        same_chain = (
            _supermarket_matches_chain(item, company_name)
            or _osm_store_matches_loaded_network(item, own_metrics)
        )
        if same_chain:
            own_osm.append(item)
        else:
            competitors.append(item)

    own_distances = [
        float(x["distance_m"]) for x in own_osm
        if x.get("distance_m") is not None
    ]
    if own_metrics and own_metrics.get("available") and own_metrics.get("nearest_m") is not None:
        own_distances.append(float(own_metrics["nearest_m"]))

    competitor_distances = [
        float(x["distance_m"]) for x in competitors
        if x.get("distance_m") is not None
    ]

    nearest_same = min(own_distances) if own_distances else None
    nearest_comp = min(competitor_distances) if competitor_distances else None

    cannibalization = (
        "Sí"
        if nearest_same is not None and nearest_same < CANNIBALIZATION_DISTANCE_M
        else "No"
    )
    direct_competition = (
        "Sí"
        if nearest_comp is not None and nearest_comp <= DIRECT_COMPETITION_DISTANCE_M
        else "No"
    )

    return {
        "competition_direct": direct_competition,
        "cannibalization": cannibalization,
        "nearest_competitor_m": nearest_comp,
        "nearest_same_chain_m": nearest_same,
        "competitors": competitors,
        "same_chain_osm": own_osm,
    }


def weighted_competition(items):
    # Se conserva sólo por compatibilidad con pantallas históricas.
    # Ya NO interviene en el score V37: competencia directa es binaria.
    total = 0.0
    for item in items:
        distance = item.get("distance_m")
        if distance is None or distance > 1000:
            continue
        if distance <= 250:
            total += 1.00
        elif distance <= 500:
            total += 0.75
        elif distance <= 750:
            total += 0.50
        else:
            total += 0.25
    return total

def apply_competition_sources(metrics, lat=None, lon=None):
    """
    Competencia basada exclusivamente en OpenStreetMap.
    Cuenta únicamente supermercados etiquetados como shop=supermarket,
    deduplicados por proximidad y nombre.
    """
    items = dedupe_osm_supermarkets(
        metrics.get("all_supermarkets", [])
    )

    metrics["all_supermarkets"] = items
    metrics["nearby_supermarkets"] = items[:20]
    metrics["supermarkets_500"] = sum(
        1 for item in items
        if item.get("distance_m") is not None
        and item["distance_m"] <= 500
    )
    metrics["supermarkets_1000"] = sum(
        1 for item in items
        if item.get("distance_m") is not None
        and item["distance_m"] <= 1000
    )
    metrics["nearest_supermarket_m"] = (
        items[0]["distance_m"] if items else None
    )
    metrics["competition_weighted"] = weighted_competition(items)
    metrics["competition_source"] = "OpenStreetMap"
    return metrics


def saturating(value, scale):
    if value is None or value <= 0:
        return 0.0
    return 1.0 - math.exp(-float(value) / float(scale))

def micro_score(metrics):
    # Índice transparente, diseñado para diferenciar microlocalizaciones.
    # No es un coeficiente estadísticamente calibrado.
    activity = min(max(float(metrics.get("activity_score", 0.0)) / 100.0, 0.0), 1.0)
    transit = saturating(metrics["transit_500"], 10)
    access = min(max(float(metrics.get("road_access_score", 0.0)) / 100.0, 0.0), 1.0)

    comp_count = min(metrics["supermarkets_1000"] / 8.0, 1.0)
    count_whitespace = 1.0 - comp_count
    nearest = metrics["nearest_supermarket_m"]
    distance_whitespace = 1.0 if nearest is None else min(max(nearest / 1000.0, 0.0), 1.0)
    whitespace = 0.65 * count_whitespace + 0.35 * distance_whitespace

    score = (
        0.35 * activity
        + 0.25 * transit
        + 0.20 * access
        + 0.20 * whitespace
    )
    score = min(max(score, 0.0), 1.0)
    index = 2.0 * (score - 0.5)

    return {
        "score_0_100": score * 100,
        "micro_index": index,
        "activity_component": activity,
        "transit_component": transit,
        "access_component": access,
        "whitespace_component": whitespace,
    }

# -----------------------------
# Simulación
# -----------------------------

def analyze_address(
    rec, metrics, seed, scenarios=10000, format_override=None,
    force_format_override=False, address_text='', lat=None, lon=None,
    network_df=None,
):
    """Simulación por comparables históricos; no aplica coeficientes locales inventados."""
    fmt = format_override if format_override in ACTIVITY_OPTIMAL_RANGES else rec['Formato sugerido']
    result = project_sales(
        zone=rec['Zona'], fmt=fmt, localidad=rec['Ubicación'],
        address=address_text, seed=seed, scenarios=scenarios,
        lat=lat, lon=lon, network_df=network_df,
        normalized_sales=(st.session_state.get('verified_sales')
                          if st.session_state.get('company_name')=='Carrefour' else None),
    )
    if result is not None:
        result['micro'] = micro_score(metrics)
    return result


def _band(value, strong, good, weak):
    if value >= strong:
        return "Fuerte"
    if value >= good:
        return "Favorable"
    if value >= weak:
        return "Intermedio"
    return "Débil"

def build_investment_case(rec, sim, metrics):
    micro = sim["micro"]
    pros = []
    cons = []
    observations = []

    # 1. Mercado estructural / ROS
    ros = rec.get("ROS", "")
    if ros == "Muy bueno":
        pros.append("El mercado estructural tiene ROS **Muy bueno**, por lo que población, densidad, actividad comercial y cupo resultan favorables en conjunto.")
    elif ros == "Bueno":
        pros.append("El mercado estructural tiene ROS **Bueno**, una base positiva para continuar el análisis.")
    elif ros == "Regular":
        observations.append("El ROS del área es **Regular**: la ubicación puntual puede mejorar el caso, pero el mercado de base no es especialmente fuerte.")
    elif ros == "Malo":
        cons.append("El ROS del área es **Malo**, por lo que el punto parte de condiciones estructurales desfavorables.")

    cupo = rec.get("Cupo disponible")
    if isinstance(cupo, (int, float)):
        if cupo >= 6:
            pros.append(f"Existe **cupo disponible alto ({int(cupo)})**, señal de espacio potencial según el criterio de cobertura del modelo.")
        elif cupo >= 3:
            pros.append(f"Existe **cupo disponible ({int(cupo)})**, por lo que el mercado no aparece completamente cubierto bajo el criterio actual.")
        elif cupo == 0:
            cons.append("El modelo estima **cupo disponible igual a 0**, lo que sugiere un mercado ya cubierto según el criterio de población/cobertura.")
        else:
            observations.append(f"El cupo disponible es **acotado ({int(cupo)})**.")

    # 2. Benchmark de formato
    perf = rec.get("Performance formato", "")
    if perf == "Muy bueno":
        pros.append(f"El formato sugerido **{rec['Formato sugerido']}** muestra performance histórica **Muy buena** en {rec['Zona']}.")
    elif perf == "Bueno":
        pros.append(f"El formato sugerido **{rec['Formato sugerido']}** muestra performance histórica **Buena** en {rec['Zona']}.")
    elif perf == "Regular":
        observations.append(f"El formato sugerido **{rec['Formato sugerido']}** tiene performance histórica **Regular** en {rec['Zona']}.")
    elif perf == "Malo":
        cons.append(f"El formato sugerido **{rec['Formato sugerido']}** presenta performance histórica **Mala** en {rec['Zona']}.")

    n = sim.get("sample_n", 0)
    if n >= 5:
        pros.append(f"El benchmark Zona + Formato cuenta con **{n} locales**, una base relativamente sólida dentro del modelo.")
    elif n >= 2:
        observations.append(f"El benchmark Zona + Formato se apoya en **{n} locales**; conviene interpretar la referencia con cautela.")
    elif n == 1:
        cons.append("El benchmark Zona + Formato se apoya en **un solo local**, por lo que la evidencia histórica es limitada.")

    # 3. Actividad / transporte / acceso
    activity = micro["activity_component"]
    transit = micro["transit_component"]
    access = micro["access_component"]
    whitespace = micro["whitespace_component"]

    if activity >= 0.65:
        pros.append(f"Se detecta **actividad comercial intensa** alrededor del punto ({metrics['activity_500']} POIs de actividad a 500 m).")
    elif activity < 0.35:
        cons.append(f"La **actividad comercial detectada es baja** ({metrics['activity_500']} POIs de actividad a 500 m).")
    else:
        observations.append(f"La actividad comercial a 500 m es **intermedia** ({metrics['activity_500']} POIs detectados).")

    if transit >= 0.60:
        pros.append(f"La dirección presenta **buena conectividad por transporte público** ({metrics['transit_500']} elementos detectados a 500 m).")
    elif transit < 0.25:
        cons.append(f"Hay **poca infraestructura de transporte público detectada** a 500 m ({metrics['transit_500']} elementos).")
    else:
        observations.append(f"La conectividad por transporte público es **intermedia** ({metrics['transit_500']} elementos a 500 m).")

    if access >= 0.50:
        pros.append(
            f"Se detecta **buena accesibilidad vial**, con "
            f"{metrics['major_roads_500']} vías principales únicas y un índice vial de "
            f"{metrics.get('road_access_score', 0):.0f}/100."
        )
    elif access < 0.20:
        cons.append("La dirección muestra **baja accesibilidad a vías principales** según los datos geográficos disponibles.")
    else:
        observations.append(
            f"La accesibilidad vial es **intermedia** "
            f"({metrics['major_roads_500']} vías principales únicas; "
            f"índice vial {metrics.get('road_access_score', 0):.0f}/100)."
        )

    # 4. Competencia
    supers = metrics["supermarkets_1000"]
    nearest = metrics["nearest_supermarket_m"]
    comp_weighted = metrics.get("competition_weighted")
    comp_source = metrics.get("competition_source", "OpenStreetMap")
    if whitespace >= 0.65:
        pros.append(f"La **presión competitiva detectada es relativamente baja**: {supers} supermercados/convenience a 1 km.")
    elif whitespace <= 0.35:
        cons.append(f"Existe **presión competitiva elevada**: {supers} supermercados/convenience detectados dentro de 1 km.")
    else:
        observations.append(f"La competencia en 1 km es **moderada** ({supers} supermercados/convenience detectados).")

    if nearest is not None:
        if nearest < 250:
            cons.append(
                f"Hay un supermercado a aproximadamente **{nearest:.0f} m**, "
                "lo que implica competencia inmediata."
            )
        elif nearest >= 700:
            pros.append(
                f"El supermercado más cercano detectado está a aproximadamente "
                f"**{nearest:.0f} m**, dejando cierto espacio de proximidad."
            )
    else:
        observations.append(
            "No se detectó un supermercado cercano; esto puede indicar baja "
            "competencia o una limitación de cobertura de las fuentes."
        )

    if comp_weighted is not None:
        if comp_weighted >= 5:
            cons.append(
                f"La **presión competitiva ponderada por distancia es alta "
                f"({comp_weighted:.2f})**."
            )
        elif comp_weighted <= 1.5:
            pros.append(
                f"La **presión competitiva ponderada por distancia es baja "
                f"({comp_weighted:.2f})**."
            )
        else:
            observations.append(
                f"La presión competitiva ponderada por distancia es "
                f"**intermedia ({comp_weighted:.2f})**."
            )

    # 5. Índice microlocal descriptivo, independiente de la facturación
    micro_score = micro["score_0_100"]
    if micro_score >= 65:
        pros.append(f"El índice microlocal de entorno es alto ({micro_score:.1f}/100). No se convierte automáticamente en ventas.")
    elif micro_score < 40:
        cons.append(f"El índice microlocal de entorno es bajo ({micro_score:.1f}/100); requiere relevamiento de campo.")
    else:
        observations.append(f"El índice microlocal es intermedio ({micro_score:.1f}/100). Es independiente del motor económico.")

    # 6. Comparables históricos. No utilizar P(>media) como criterio de inversión.
    local_n = sim.get('local_comparables_n', 0)
    if local_n:
        observations.append(
            f"La proyección usa {local_n} comparable(s) del partido y "
            f"{sim['sample_n']} sucursales de referencia. "
            f"Confianza comercial: {sim.get('quality', 'Sin clasificar')}."
        )
    else:
        observations.append(
            "No se encontraron comparables identificados del mismo partido; "
            "se utiliza la media histórica de zona y formato."
        )
    observations.append(
        f"La probabilidad de superar el promedio histórico de zona es {pct(sim['prob_target'])}; "
        "no representa una probabilidad de éxito del proyecto."
    )

    # 7. Signal score
    points = 0
    points += {"Muy bueno": 2, "Bueno": 1, "Regular": 0, "Malo": -2}.get(ros, 0)
    points += {"Muy bueno": 2, "Bueno": 1, "Regular": 0, "Malo": -2}.get(perf, 0)
    # El resultado comercial no altera la puntuación de ubicación: evita circularidad.
    points += 2 if micro_score >= 65 else 1 if micro_score >= 55 else 0 if micro_score >= 45 else -1 if micro_score >= 35 else -2
    points += 1 if whitespace >= 0.60 else -1 if whitespace <= 0.30 else 0
    points += 1 if n >= 5 else 0 if n >= 2 else -1

    base_rec = rec.get("Recomendación integrada", "")
    if base_rec == "NO PRIORIZAR":
        signal = "No priorizar sin nueva evidencia"
    elif points >= 7:
        signal = "Avanzar a evaluación comercial"
    elif points >= 4:
        signal = "Oportunidad atractiva con validaciones"
    elif points >= 1:
        signal = "Evaluar selectivamente"
    else:
        signal = "Caso débil / requiere justificación adicional"

    # Confidence of this output
    confidence = "Alta"
    confidence_reasons = []
    if n < 5:
        confidence = "Media"
        confidence_reasons.append("muestra histórica acotada")
    if metrics["activity_500"] == 0 and metrics["transit_500"] == 0:
        confidence = "Baja"
        confidence_reasons.append("poca información microlocal detectada en OSM")
    elif metrics["activity_500"] < 5 and confidence == "Alta":
        confidence = "Media"
        confidence_reasons.append("cobertura microlocal limitada")

    validations = [

        "Medir **flujo peatonal y vehicular real** por franja horaria y día de la semana.",
        "Validar en campo los **competidores cercanos**, su formato, tamaño, precios y nivel de tráfico.",
        "Analizar **canibalización** con locales propios de la cadena.",
        "Incorporar **poder adquisitivo / perfil socioeconómico** si se dispone de una fuente confiable.",
        "Verificar **zonificación y restricciones regulatorias** aplicables a la ubicación."
    ]

    factor_rows = [
        ("Mercado estructural", ros, "ROS del barrio/municipio"),
        ("Formato en la zona", perf, f"{rec['Formato sugerido']} · {n} locales de referencia"),
        ("Actividad comercial", _band(activity, 0.65, 0.45, 0.30), f"{metrics['activity_500']} POIs a 500 m"),
        ("Transporte público", _band(transit, 0.60, 0.40, 0.20), f"{metrics['transit_500']} elementos a 500 m"),
        (
            "Accesibilidad vial",
            _band(access, 0.50, 0.35, 0.20),
            f"{metrics['major_roads_500']} vías únicas · índice {metrics.get('road_access_score', 0):.0f}/100"
        ),
        ("Espacio competitivo", _band(whitespace, 0.65, 0.45, 0.30), f"{supers} competidores a 1 km · {comp_source}"),
        ("Microlocalización", _band(micro_score / 100.0, 0.65, 0.55, 0.40), f"{micro_score:.1f}/100"),
        ("Facturación", sim.get("quality", "Limitada"), f"{n} locales · {sim.get('local_comparables_n', 0)} comparables de partido"),
    ]

    return {
        "pros": pros,
        "cons": cons,
        "observations": observations,
        "validations": validations,
        "signal": signal,
        "points": points,
        "confidence": confidence,
        "confidence_reasons": confidence_reasons,
        "factor_rows": factor_rows,
    }

def automatic_report(rec, sim, metrics):
    nearest = metrics["nearest_supermarket_m"]
    nearest_txt = (
        "sin competidor cercano detectado"
        if nearest is None
        else f"competidor más cercano a {nearest:.0f} m"
    )

    return (
        f"La dirección se ubica en {rec['Ubicación']} ({rec['Zona']}). "
        f"El entorno registra un índice de actividad comercial de {metrics.get('activity_score', 0):.0f}/100, "
        f"{metrics['transit_500']} elementos de transporte, "
        f"{metrics['major_roads_500']} vías principales únicas y "
        f"{metrics['supermarkets_1000']} supermercados a 1 km ({nearest_txt}). "
        f"El índice vial es {metrics.get('road_access_score', 0):.0f}/100 y el índice "
        f"microlocal es {sim['micro']['score_0_100']:.1f}/100. "
        f"La media estimada para la dirección es {fmt_money_k(sim['address_mean'])} "
        f"(confianza comercial {sim.get('quality', 'limitada')}), "
        f"con una probabilidad de {pct(sim['prob_target'])} de superar el promedio anual histórico del formato en esa zona (no una meta comercial). "
        "La decisión final requiere validar ventas incrementales y habilitaciones aplicables."
    )

# -----------------------------
# Motor de scoring configurable por empresa
# -----------------------------

SCORE_VARIABLES = [
    {
        "Variable": "Población",
        "Peso %": 10.0,
        "Sentido": "Más es mejor",
        "Umbral 1": 40000.0,
        "Umbral 2": 300000.0,
        "Excluyente": False,
        "Unidad": "habitantes",
    },
    {
        "Variable": "Densidad poblacional",
        "Peso %": 8.0,
        "Sentido": "Más es mejor",
        "Umbral 1": 5000.0,
        "Umbral 2": 14000.0,
        "Excluyente": False,
        "Unidad": "hab/km²",
    },
    {
        "Variable": "Movilidad / flujo comercial",
        "Peso %": 15.0,
        "Sentido": "Más es mejor",
        "Umbral 1": 6000.0,
        "Umbral 2": 22000.0,
        "Excluyente": False,
        "Unidad": "viajes/km²/día",
    },
    {
        "Variable": "Cupo disponible",
        "Peso %": 10.0,
        "Sentido": "Más es mejor",
        "Umbral 1": 0.0,
        "Umbral 2": 4.0,
        "Excluyente": False,
        "Unidad": "locales",
    },
    {
        "Variable": "Actividad comercial microlocal",
        "Peso %": 8.0,
        "Sentido": "Rango según formato",
        "Umbral 1": 0.0,
        "Umbral 2": 100.0,
        "Excluyente": False,
        "Unidad": "índice 0-100",
    },
    {
        "Variable": "Competencia directa",
        "Peso %": 12.0,
        "Sentido": "No es mejor",
        "Umbral 1": 0.0,
        "Umbral 2": 1.0,
        "Excluyente": False,
        "Unidad": "Sí / No · corte 250 m",
    },
    {
        "Variable": "Canibalización propia",
        "Peso %": 10.0,
        "Sentido": "No es mejor",
        "Umbral 1": 0.0,
        "Umbral 2": 1.0,
        "Excluyente": False,
        "Unidad": "Sí / No · corte 400 m",
    },
    {
        "Variable": "Sinergia logística / red",
        "Peso %": 10.0,
        "Sentido": "Más es mejor",
        "Umbral 1": 0.0,
        "Umbral 2": 3.0,
        "Excluyente": False,
        "Unidad": "locales propios a 5 km",
    },
    {
        "Variable": "Accesibilidad",
        "Peso %": 9.0,
        "Sentido": "Más es mejor",
        "Umbral 1": 30.0,
        "Umbral 2": 80.0,
        "Excluyente": False,
        "Unidad": "índice 0-100",
    },
    {
        "Variable": "Afinidad socioeconómica",
        "Peso %": 8.0,
        "Sentido": "Más es mejor",
        "Umbral 1": 0.0,
        "Umbral 2": 100.0,
        "Excluyente": False,
        "Unidad": "nivel socioeconómico 0-100",
    },
]

NETWORK_COLUMNS = ["Nombre", "Latitud", "Longitud", "Formato", "Dirección", "EstadoGeo"]
NETWORK_PUBLIC_COLUMNS = ["Nombre", "Dirección", "Formato"]

SCORE_VARIABLE_INFO = {
    "Población": {
        "short": "Demanda potencial",
        "description": "Personas que viven en el barrio/municipio de referencia.",
        "simple": "Cuanta más población tenga el mercado, mayor base potencial de clientes.",
    },
    "Densidad poblacional": {
        "short": "Concentración de demanda",
        "description": "Habitantes por km².",
        "simple": "Mide qué tan concentrada está la población alrededor del mercado.",
    },
    "Movilidad / flujo comercial": {
        "short": "Movimiento de la zona",
        "description": "Intensidad de viajes y movimiento comercial de la zona.",
        "simple": "Busca zonas donde efectivamente se mueve gente, no solo donde vive gente.",
    },
    "Cupo disponible": {
        "short": "Espacio de mercado",
        "description": "Diferencia entre capacidad estimada del mercado y oferta existente.",
        "simple": "Indica si todavía parece haber espacio para sumar oferta.",
    },
    "Actividad comercial microlocal": {
        "short": "Actividad adecuada al formato",
        "description": "Índice 0-100 comparado contra un rango ideal distinto para cada tipo de local.",
        "simple": "No usa 'más es mejor': Express admite actividad alta; Hiper y Maxi pueden ser más adecuados con actividad moderada.",
    },
    "Competencia directa": {
        "short": "Presión competitiva",
        "description": "Señal binaria: Sí si existe un supermercado de otra cadena a 250 m o menos.",
        "simple": "Si hay otro supermercado a 250 m o menos, marca Sí; si no, No.",
    },
    "Canibalización propia": {
        "short": "Solapamiento de red",
        "description": "Señal binaria: Sí si existe un supermercado de la misma cadena a menos de 400 m.",
        "simple": "Si la misma cadena está a menos de 400 m, marca Sí; si no, No.",
    },
    "Sinergia logística / red": {
        "short": "Red propia a 5 km",
        "description": "Cantidad de locales de la misma cadena dentro de 5 km.",
        "simple": "Premia zonas donde la empresa ya tiene una red que puede generar sinergias operativas.",
    },
    "Accesibilidad": {
        "short": "Acceso vial + transporte",
        "description": "Combina jerarquía de vías principales y transporte público cercano.",
        "simple": "Mide qué tan fácil es llegar al punto.",
    },
    "Afinidad socioeconómica": {
        "short": "Nivel socioeconómico de la zona",
        "description": "Clasificación socioeconómica estructural predefinida para el barrio o localidad.",
        "simple": "Se asigna automáticamente según el barrio o localidad del modelo y no puede ser modificada por la empresa.",
    },
}

CHAIN_OPTIONS = [
    "Carrefour",
    "Coto",
    "Día",
    "Jumbo",
    "Disco",
    "Vea",
    "Chango Más",
    "La Anónima",
    "Cooperativa Obrera",
    "Makro",
    "Vital",
    "Diarco",
    "Yaguar",
    "Libertad",
]

# Señales binarias solicitadas para la lectura microlocal.
# Canibalización: SÍ si hay un supermercado de la misma cadena a MENOS de 400 m.
# Competencia directa: SÍ si hay un supermercado de otra cadena a 250 m O MENOS.
CANNIBALIZATION_DISTANCE_M = 400.0
DIRECT_COMPETITION_DISTANCE_M = 250.0

CHAIN_OSM_ALIASES = {
    "Carrefour": ["carrefour"],
    "Coto": ["coto"],
    "Día": ["dia"],
    "Jumbo": ["jumbo"],
    "Disco": ["disco"],
    "Vea": ["vea"],
    "Chango Más": ["chango mas", "changomas"],
    "La Anónima": ["la anonima", "anonima"],
    "Cooperativa Obrera": ["cooperativa obrera"],
    "Makro": ["makro"],
    "Vital": ["vital"],
    "Diarco": ["diarco"],
    "Yaguar": ["yaguar"],
    "Libertad": ["libertad"],
}

FORMAT_LABEL_TO_MODEL = {
    "Express / Proximidad": "Express",
    "Supermercado / Market": "Market",
    "Hipermercado": "Hiper",
    "Mayorista / Maxi": "Maxi",
}

FORMAT_MODEL_TO_LABEL = {
    value: key for key, value in FORMAT_LABEL_TO_MODEL.items()
}


NETWORK_FORMAT_OPTIONS = [
    "Express / Proximidad",
    "Supermercado / Market",
    "Hipermercado",
    "Mayorista / Maxi",
]

EXECUTIVE_FACTORS = [
    {'Factor':'Demanda y mercado','Peso %':40.0,'Nivel buscado':'Automático',
     'Componentes':['Población','Densidad poblacional','Movilidad / flujo comercial','Afinidad socioeconómica'],
     'Descripción':'Mercado residencial, densidad, movimiento y adecuación sociodemográfica.'},
    {'Factor':'Entorno comercial','Peso %':30.0,'Nivel buscado':'Automático',
     'Componentes':['Actividad comercial microlocal','Accesibilidad','Visibilidad V37 índice'],
     'Descripción':'Actividad, circulación y conectividad de la zona.'},
    {'Factor':'Espacio competitivo','Peso %':30.0,'Nivel buscado':'Automático',
     'Componentes':['Capacidad comercial estimada','Presión estructural','Oferta de supermercados a 1.200 m'],
     'Descripción':'Espacio comercial estimado y presión competitiva gradual.'},
    {'Factor':'Red propia','Peso %':25.0,'Nivel buscado':'Automático',
     'Componentes':['Distancia misma cadena m'],
     'Descripción':'Riesgo de superposición geográfica; pesa únicamente en conveniencia.'},
]

QUALITATIVE_LEVELS = ["Poco", "Regular", "Alto", "Muy alto"]
QUALITATIVE_RANK = {name: idx for idx, name in enumerate(QUALITATIVE_LEVELS)}


# Nivel socioeconómico estructural por unidad territorial del modelo.
# En CABA se usa el barrio. En GBA/Interior se usa la localidad/municipio
# representado por el registro del modelo.
#
# IMPORTANTE: es una clasificación base del prototipo, no una medición oficial
# de ingreso del hogar ni un dato censal en tiempo real.
SOCIOECONOMIC_LEVEL_BY_LOCATION = {
    # CABA Centro
    "Almagro": "Alto",
    "Balvanera": "Regular",
    "Caballito": "Alto",
    "Monserrat": "Regular",
    "Puerto Madero": "Muy alto",
    "Retiro": "Alto",
    "San Cristóbal": "Regular",
    "San Nicolás": "Alto",
    "San Telmo": "Alto",
    "Villa Crespo": "Alto",

    # CABA Norte
    "Belgrano": "Muy alto",
    "Coghlan": "Alto",
    "Colegiales": "Alto",
    "Núñez": "Muy alto",
    "Palermo": "Muy alto",
    "Recoleta": "Muy alto",
    "Saavedra": "Alto",
    "Villa Urquiza": "Alto",

    # CABA Oeste
    "Agronomía": "Alto",
    "Chacarita": "Regular",
    "Flores": "Regular",
    "Floresta": "Regular",
    "Liniers": "Regular",
    "Monte Castro": "Alto",
    "Parque Chas": "Alto",
    "Paternal": "Regular",
    "Versalles": "Alto",
    "Villa Devoto": "Muy alto",
    "Villa General Mitre": "Regular",
    "Villa Luro": "Alto",
    "Villa Ortúzar": "Alto",
    "Villa Pueyrredón": "Alto",
    "Villa Real": "Alto",
    "Villa Santa Rita": "Regular",
    "Villa del Parque": "Alto",
    "Vélez Sarsfield": "Regular",

    # CABA Sur
    "Barracas": "Regular",
    "Boedo": "Alto",
    "Constitución": "Poco",
    "La Boca": "Poco",
    "Mataderos": "Regular",
    "Nueva Pompeya": "Poco",
    "Parque Avellaneda": "Regular",
    "Parque Chacabuco": "Regular",
    "Parque Patricios": "Regular",
    "Villa Lugano": "Poco",
    "Villa Riachuelo": "Poco",
    "Villa Soldati": "Poco",

    # GBA Norte
    "Campana": "Regular",
    "Escobar": "Alto",
    "General San Martin": "Regular",
    "José C. Paz": "Poco",
    "Malvinas Argentinas": "Regular",
    "Pilar": "Alto",
    "San Fernando": "Alto",
    "San Isidro": "Muy alto",
    "San Miguel": "Regular",
    "Tigre": "Alto",
    "Vicente Lopez": "Muy alto",
    "Zárate": "Regular",

    # GBA Oeste
    "General Rodriguez": "Regular",
    "Hurlingham": "Regular",
    "Ituzaingo": "Alto",
    "La Matanza": "Regular",
    "Merlo": "Regular",
    "Moreno": "Poco",
    "Morón": "Alto",
    "Tres de Febrero": "Regular",

    # GBA Sur
    "Almirante Brown": "Regular",
    "Avellaneda": "Alto",
    "Berazategui": "Regular",
    "Cañuelas": "Regular",
    "Esteban Echeverria": "Regular",
    "Ezeiza": "Regular",
    "Florencio Varela": "Poco",
    "La Plata": "Alto",
    "Lanus": "Regular",
    "Lomas De Zamora": "Alto",
    "Quilmes": "Alto",
    "San Vicente": "Regular",

    # Interior
    "Tandil": "Alto",
}

SOCIOECONOMIC_SCORE_BY_LEVEL = {
    "Poco": 25.0,
    "Regular": 50.0,
    "Alto": 75.0,
    "Muy alto": 100.0,
}

SOCIOECONOMIC_FACTOR = "Nivel socioeconómico"
SOCIOECONOMIC_TECHNICAL_VARIABLE = "Afinidad socioeconómica"


def socioeconomic_location_name(rec):
    return str((rec or {}).get("Ubicación", "")).strip()


def socioeconomic_level_for_record(rec):
    location = socioeconomic_location_name(rec)
    return SOCIOECONOMIC_LEVEL_BY_LOCATION.get(location, "Regular")


def socioeconomic_score_for_record(rec):
    level = socioeconomic_level_for_record(rec)
    return SOCIOECONOMIC_SCORE_BY_LEVEL.get(level, 50.0)



def default_score_rules_df():
    # Señales técnicas internas. Ya no se editan desde la interfaz principal.
    return pd.DataFrame(SCORE_VARIABLES)


def default_group_profile_df():
    return pd.DataFrame([
        {
            "Factor": item["Factor"],
            "Peso %": item["Peso %"],
            "Nivel buscado": item["Nivel buscado"],
        }
        for item in EXECUTIVE_FACTORS
    ])


def executive_factor_definition(name):
    for item in EXECUTIVE_FACTORS:
        if item["Factor"] == name:
            return item
    return None


def qualitative_level_from_score(score):
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return "Sin dato"
    value = float(score)
    if value < 40:
        return "Poco"
    if value < 60:
        return "Regular"
    if value < 80:
        return "Alto"
    return "Muy alto"


def qualitative_alignment_score(found_level, required_level):
    if found_level not in QUALITATIVE_RANK or required_level not in QUALITATIVE_RANK:
        return None
    gap = QUALITATIVE_RANK[required_level] - QUALITATIVE_RANK[found_level]
    if gap <= 0:
        return 100.0
    return {1: 70.0, 2: 40.0, 3: 10.0}.get(gap, 10.0)


def _clean_network_df(df):
    if df is None:
        return pd.DataFrame(columns=NETWORK_COLUMNS)

    out = df.copy()
    rename = {}
    for col in out.columns:
        n = norm(col)
        if n in ("nombre", "local", "sucursal"):
            rename[col] = "Nombre"
        elif n in ("latitud", "latitude", "lat"):
            rename[col] = "Latitud"
        elif n in ("longitud", "longitude", "lon", "lng"):
            rename[col] = "Longitud"
        elif n in ("formato", "format"):
            rename[col] = "Formato"
        elif n in ("direccion", "address"):
            rename[col] = "Dirección"
        elif n in ("estadogeo", "estado geo", "estado_geocodificacion", "geocode_status"):
            rename[col] = "EstadoGeo"
    out = out.rename(columns=rename)

    for col in NETWORK_COLUMNS:
        if col not in out.columns:
            out[col] = ""

    out = out[NETWORK_COLUMNS]
    out["Latitud"] = pd.to_numeric(out["Latitud"], errors="coerce")
    out["Longitud"] = pd.to_numeric(out["Longitud"], errors="coerce")
    out["EstadoGeo"] = out["EstadoGeo"].fillna("").astype(str)

    # Si ya tiene coordenadas, siempre se considera localizada.
    has_coords = out["Latitud"].notna() & out["Longitud"].notna()
    out.loc[has_coords, "EstadoGeo"] = "Localizado"

    # Las filas nuevas con dirección quedan pendientes.
    has_address = out["Dirección"].fillna("").astype(str).str.strip() != ""
    empty_status = out["EstadoGeo"].str.strip() == ""
    out.loc[has_address & ~has_coords & empty_status, "EstadoGeo"] = "Pendiente"
    out.loc[~has_address & ~has_coords & empty_status, "EstadoGeo"] = "Sin dirección"

    return out


def load_network_upload(uploaded):
    name = (uploaded.name or "").lower()

    if name.endswith(".json"):
        raw = uploaded.getvalue()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)

        # Acepta tanto una lista directa como:
        # {"sucursales": [{...}, {...}]}
        if isinstance(payload, dict):
            records = payload.get("sucursales", payload.get("red", payload.get("data")))
        else:
            records = payload

        if not isinstance(records, list):
            raise ValueError(
                "El JSON debe contener una lista de sucursales o una clave 'sucursales'."
            )

        df = pd.DataFrame(records)

    elif name.endswith(".csv"):
        try:
            df = pd.read_csv(uploaded)
        except UnicodeDecodeError:
            uploaded.seek(0)
            df = pd.read_csv(uploaded, encoding="latin1")
    else:
        df = pd.read_excel(uploaded)

    return _clean_network_df(df)




BUILTIN_CARREFOUR_NETWORK_FILE = BASE_DIR / "RED_CARREFOUR_COMPLETA_811.csv"

@st.cache_data(show_spinner=False)
def load_builtin_carrefour_network():
    """
    Red Carrefour oficial precargada para la app.
    Se carga con direcciones y queda lista para geocodificar dentro de la app.
    """
    if not BUILTIN_CARREFOUR_NETWORK_FILE.exists():
        return pd.DataFrame(columns=NETWORK_COLUMNS)
    df = pd.read_csv(BUILTIN_CARREFOUR_NETWORK_FILE, encoding="utf-8-sig")
    return _clean_network_df(df)

def normalize_network_address(address):
    text = str(address or "").strip()
    if not text:
        return ""
    if "argentina" not in norm(text):
        text = f"{text}, Argentina"
    return text


def network_geocode_counts(df):
    out = _clean_network_df(df)
    status = out["EstadoGeo"].fillna("").astype(str)
    return {
        "total": int(len(out)),
        "localized": int((status == "Localizado").sum()),
        "pending": int((status == "Pendiente").sum()),
        "not_found": int((status == "No encontrado").sum()),
        "without_address": int((status == "Sin dirección").sum()),
    }


def reset_failed_geocoding(df):
    out = _clean_network_df(df)
    mask = out["EstadoGeo"] == "No encontrado"
    out.loc[mask, "EstadoGeo"] = "Pendiente"
    return out


def network_address_key(address):
    """
    Clave conservadora para detectar la MISMA dirección repetida.
    No intenta unir locales por similitud de nombre ni por localidad parecida.
    """
    text = normalize_network_address(address)
    key = norm(text)
    # "Argentina" es agregado por la app y no aporta a la identidad.
    key = re.sub(r"\bargentina\b", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def dedupe_network_dataframe(df):
    """
    Elimina duplicados por dirección normalizada conservando la primera fila.
    Si una copia tiene coordenadas y la primera no, conserva esas coordenadas.

    Devuelve: dataframe_depurado, lista_de_duplicados.
    """
    out = _clean_network_df(df)
    if out.empty:
        return out, []

    kept_rows = []
    key_to_pos = {}
    duplicates = []

    for original_idx, row in out.iterrows():
        record = row.to_dict()
        address = str(record.get("Dirección") or "").strip()
        key = network_address_key(address) if address else ""

        # Sin dirección no se deduplica automáticamente.
        if not key:
            kept_rows.append(record)
            continue

        if key not in key_to_pos:
            key_to_pos[key] = len(kept_rows)
            kept_rows.append(record)
            continue

        kept_pos = key_to_pos[key]
        kept = kept_rows[kept_pos]

        # Si la copia tiene información útil que falta en la conservada, completarla.
        for col in ("Nombre", "Formato"):
            if not str(kept.get(col) or "").strip() and str(record.get(col) or "").strip():
                kept[col] = record.get(col)

        kept_lat = kept.get("Latitud")
        kept_lon = kept.get("Longitud")
        row_lat = record.get("Latitud")
        row_lon = record.get("Longitud")
        if (
            (pd.isna(kept_lat) or pd.isna(kept_lon))
            and pd.notna(row_lat)
            and pd.notna(row_lon)
        ):
            kept["Latitud"] = float(row_lat)
            kept["Longitud"] = float(row_lon)

        duplicates.append({
            "Fila": int(original_idx) + 1,
            "Nombre duplicado": str(record.get("Nombre") or ""),
            "Dirección": address,
            "Conservada como": str(kept.get("Nombre") or ""),
            "Motivo": "Misma dirección normalizada",
        })

    clean = pd.DataFrame(kept_rows, columns=NETWORK_COLUMNS)
    return _clean_network_df(clean), duplicates


def geocode_network_dataframe(
    df,
    max_requests=NETWORK_GEOCODE_BATCH_SIZE,
    progress_callback=None,
):
    """
    Procesa UN bloque.

    Importante:
    - una dirección que devuelve cero resultados queda como "No encontrado";
    - no vuelve a intentarse en los siguientes bloques;
    - sólo queda "Pendiente" una dirección que todavía no se consultó o cuyo
      proceso se interrumpió por un límite/caída temporal.
    """
    out, duplicates = dedupe_network_dataframe(df)
    success = 0
    failed = []
    stopped_reason = None

    pending_indices = out.index[
        out["EstadoGeo"].fillna("").astype(str) == "Pendiente"
    ].tolist()

    batch_indices = pending_indices[:max(int(max_requests), 1)]
    batch_total = len(batch_indices)
    processed = 0

    for position, idx in enumerate(batch_indices, start=1):
        address = str(out.at[idx, "Dirección"] or "").strip()

        if not address:
            out.at[idx, "EstadoGeo"] = "Sin dirección"
            failed.append({
                "Fila": int(idx) + 1,
                "Dirección": "",
                "Motivo": "Dirección vacía",
            })
            processed += 1
            continue

        try:
            geo = geocode_address(
                normalize_network_address(address),
                respect_rate_limit=True,
                max_retries=NOMINATIM_MAX_RETRIES,
            )
        except GeocodingRateLimitError as exc:
            stopped_reason = str(exc)
            # Esta dirección queda pendiente para poder retomarla luego.
            break
        except GeocodingTemporaryError as exc:
            stopped_reason = str(exc)
            break
        except requests.RequestException:
            stopped_reason = (
                "El servicio de geocodificación devolvió un error de red. "
                "El avance ya localizado se conserva."
            )
            break

        processed += 1

        if geo:
            out.at[idx, "Latitud"] = float(geo["lat"])
            out.at[idx, "Longitud"] = float(geo["lon"])
            out.at[idx, "EstadoGeo"] = "Localizado"
            success += 1
        else:
            # Esta es la mejora clave: no queda pendiente para siempre.
            out.at[idx, "EstadoGeo"] = "No encontrado"
            failed.append({
                "Fila": int(idx) + 1,
                "Dirección": address,
                "Motivo": "No se pudo localizar",
            })

        if progress_callback is not None:
            progress_callback(
                position,
                batch_total,
                success,
                len(failed),
            )

    counts = network_geocode_counts(out)

    return out, {
        "success": success,
        "failed": failed,
        "processed": processed,
        "remaining": counts["pending"],
        "not_found_total": counts["not_found"],
        "localized_total": counts["localized"],
        "duplicates": duplicates,
        "stopped_reason": stopped_reason,
        "batch_size": batch_total,
    }


def geocode_network_automatically(
    df,
    batch_size=NETWORK_GEOCODE_BATCH_SIZE,
    progress_callback=None,
    block_callback=None,
):
    """
    Sigue ejecutando bloques automáticamente hasta:
    - terminar todas las direcciones pendientes, o
    - encontrar un límite temporal/caída del geocodificador.

    El usuario inicia el proceso una sola vez.
    """
    out, duplicates = dedupe_network_dataframe(df)
    initial_counts = network_geocode_counts(out)
    initial_pending = initial_counts["pending"]

    total_success = 0
    all_failed = []
    blocks = 0
    stopped_reason = None

    while True:
        counts_before = network_geocode_counts(out)
        pending_before = counts_before["pending"]
        if pending_before <= 0:
            break

        already_processed = initial_pending - pending_before

        def _block_progress(done, total, ok, bad):
            if progress_callback is not None:
                progress_callback(
                    already_processed + done,
                    initial_pending,
                    total_success + ok,
                    len(all_failed) + bad,
                    blocks + 1,
                )

        out, summary = geocode_network_dataframe(
            out,
            max_requests=batch_size,
            progress_callback=_block_progress,
        )
        blocks += 1
        total_success += int(summary["success"])
        all_failed.extend(summary["failed"])

        counts_after = network_geocode_counts(out)

        if block_callback is not None:
            block_callback(blocks, counts_after, summary)

        if summary["stopped_reason"]:
            stopped_reason = summary["stopped_reason"]
            break

        # Seguridad para evitar un loop si por alguna razón no avanzó.
        if summary["processed"] <= 0:
            break

        # Mini pausa entre bloques. El rate limit real se respeta por solicitud.
        if counts_after["pending"] > 0:
            time.sleep(0.25)

    final_counts = network_geocode_counts(out)

    return out, {
        "success": total_success,
        "failed": all_failed,
        "blocks": blocks,
        "remaining": final_counts["pending"],
        "not_found_total": final_counts["not_found"],
        "localized_total": final_counts["localized"],
        "duplicates": duplicates,
        "stopped_reason": stopped_reason,
    }


def public_network_view(df):
    out = _clean_network_df(df)
    return out[NETWORK_PUBLIC_COLUMNS].copy()


def rebuild_network_from_public(editor_df, previous_df):
    """
    Convierte la tabla visible (Nombre, Dirección, Formato) a la estructura
    interna. Conserva coordenadas sólo cuando la sucursal/dirección no cambió.
    """
    previous = _clean_network_df(previous_df)
    rows = []

    for _, row in editor_df.iterrows():
        name = str(row.get("Nombre") or "").strip()
        address = str(row.get("Dirección") or "").strip()
        fmt = str(row.get("Formato") or "").strip()

        # Ignorar filas completamente vacías del editor dinámico.
        if not name and not address and not fmt:
            continue

        lat = None
        lon = None
        geo_status = "Pendiente" if address else "Sin dirección"

        if not previous.empty and address:
            matches = previous[
                previous["Dirección"].fillna("").astype(str).str.strip().str.casefold()
                == address.casefold()
            ]

            if name:
                same_name = matches[
                    matches["Nombre"].fillna("").astype(str).str.strip().str.casefold()
                    == name.casefold()
                ]
                if not same_name.empty:
                    matches = same_name

            if not matches.empty:
                old = matches.iloc[0]
                geo_status = str(old.get("EstadoGeo") or geo_status)
                if pd.notna(old["Latitud"]) and pd.notna(old["Longitud"]):
                    lat = float(old["Latitud"])
                    lon = float(old["Longitud"])
                    geo_status = "Localizado"

        rows.append({
            "Nombre": name,
            "Dirección": address,
            "Formato": fmt,
            "Latitud": lat,
            "Longitud": lon,
            "EstadoGeo": geo_status,
        })

    return _clean_network_df(
        pd.DataFrame(rows, columns=NETWORK_COLUMNS)
    )


def company_network_metrics(lat, lon, network_df):
    df, _ = dedupe_network_dataframe(network_df)
    valid = df.dropna(subset=["Latitud", "Longitud"]).copy()

    if valid.empty:
        return {
            "available": False,
            "count_1km": None,
            "count_3km": None,
            "count_5km": None,
            "nearest_m": None,
            "cannibalization_binary": None,
            "cannibalization_pressure": None,
            "stores": [],
            "stores_all": [],
        }

    stores = []
    for _, row in valid.iterrows():
        d = haversine_m(
            float(lat), float(lon),
            float(row["Latitud"]), float(row["Longitud"])
        )
        stores.append({
            "Nombre": str(row.get("Nombre") or "Local propio"),
            "Formato": str(row.get("Formato") or ""),
            "Dirección": str(row.get("Dirección") or ""),
            "lat": float(row["Latitud"]),
            "lon": float(row["Longitud"]),
            "distance_m": float(d),
        })

    stores = sorted(stores, key=lambda x: x["distance_m"])
    nearest_m = stores[0]["distance_m"] if stores else None
    cannibalization_binary = (
        "Sí"
        if nearest_m is not None and nearest_m < CANNIBALIZATION_DISTANCE_M
        else "No"
    )

    return {
        "available": True,
        "count_1km": sum(1 for x in stores if x["distance_m"] <= 1000),
        "count_3km": sum(1 for x in stores if x["distance_m"] <= 3000),
        "count_5km": sum(1 for x in stores if x["distance_m"] <= 5000),
        "nearest_m": nearest_m,
        "cannibalization_binary": cannibalization_binary,
        # Compatibilidad: 1 = Sí, 0 = No. Ya no se usa como presión gradual.
        "cannibalization_pressure": 1.0 if cannibalization_binary == "Sí" else 0.0,
        "stores": stores[:20],
        "stores_all": stores,
    }


def activity_range_for_format(format_name):
    fmt = str(format_name or "Market").strip()
    return ACTIVITY_OPTIMAL_RANGES.get(fmt, ACTIVITY_OPTIMAL_RANGES["Market"])


def activity_range_text(format_name):
    rng = activity_range_for_format(format_name)
    label = FORMAT_MODEL_TO_LABEL.get(format_name, format_name or "Market")
    return (
        f"Rango ideal {rng['ideal_low']:.0f}–{rng['ideal_high']:.0f}/100 "
        f"para {label}"
    )


def score_activity_by_format(value, format_name):
    """
    100 puntos dentro del rango ideal.
    Por debajo: baja gradual, sin confundir poca actividad con mala ubicación.
    Por encima: baja moderada por posible saturación/congestión.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None

    x = min(max(float(value), 0.0), 100.0)
    rng = activity_range_for_format(format_name)
    low = float(rng["ideal_low"])
    high = float(rng["ideal_high"])

    if low <= x <= high:
        return 100.0

    if x < low:
        raw = 30.0 + 70.0 * (x / low) if low > 0 else 100.0
        return float(min(max(raw, 0.0), 100.0))

    span = max(100.0 - high, 1.0)
    raw = 100.0 - 30.0 * ((x - high) / span)
    return float(min(max(raw, 70.0), 100.0))


def score_one_value(value, sense, threshold_1, threshold_2):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None

    x = float(value)
    a = float(threshold_1)
    b = float(threshold_2)

    if sense == "Menos es mejor":
        ideal = a
        max_tolerated = b
        if max_tolerated <= ideal:
            return 100.0 if x <= ideal else 0.0
        raw = 100.0 * (1.0 - (x - ideal) / (max_tolerated - ideal))
    else:
        minimum = a
        target = b
        if target <= minimum:
            return 100.0 if x >= target else 0.0
        raw = 100.0 * (x - minimum) / (target - minimum)

    return float(min(max(raw, 0.0), 100.0))


def excluded_factor_failed(value, sense, threshold_1, threshold_2, excluded):
    if not bool(excluded):
        return False
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False

    x = float(value)
    if sense == "Menos es mejor":
        return x > float(threshold_2)
    return x < float(threshold_1)


def factor_requirement_text(row, format_name=None):
    variable = str(row.get("Variable", ""))

    if variable == "Actividad comercial microlocal":
        if format_name:
            return activity_range_text(format_name)
        return "Rango ideal automático según tipo de local"
    sense = str(row.get("Sentido", "Más es mejor"))
    t1 = float(row.get("Umbral 1", 0) or 0)
    t2 = float(row.get("Umbral 2", 0) or 0)
    unit = str(row.get("Unidad", "") or "")

    if sense == "Menos es mejor":
        return (
            f"Ideal ≤ {format_observed_value(variable, t1)} {unit}".strip()
            + f" · límite {format_observed_value(variable, t2)} {unit}".strip()
        )
    return (
        f"Mínimo {format_observed_value(variable, t1)} {unit}".strip()
        + f" · objetivo {format_observed_value(variable, t2)} {unit}".strip()
    )


def format_observed_value(variable, value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "Sin dato"
    if variable in ("Competencia directa", "Canibalización propia"):
        return str(value)
    if variable in ("Población", "Densidad poblacional", "Movilidad / flujo comercial"):
        return f"{float(value):,.0f}".replace(",", ".")
    if variable in ("Cupo disponible", "Sinergia logística / red"):
        return f"{float(value):.0f}"
    return f"{float(value):.1f}"


def build_company_observations(rec, metrics, own_metrics, company_name=None):
    micro = micro_score(metrics)
    accessibility = 100.0 * (
        0.60 * micro["access_component"]
        + 0.40 * micro["transit_component"]
    )
    proximity = binary_proximity_signals(
        metrics,
        own_metrics=own_metrics,
        company_name=company_name,
    )

    return {
        "Población": rec.get("Población"),
        "Fuente población": rec.get("Fuente población", "Serie territorial histórica; año a verificar"),
        "Densidad poblacional": rec.get("Densidad poblacional"),
        "Movilidad / flujo comercial": rec.get("Densidad comercial"),
        "Cupo disponible": rec.get("Cupo disponible"),
        "Actividad comercial microlocal": metrics.get("activity_score"),
        # Señales binarias nuevas
        "Competencia directa": proximity["competition_direct"],
        "Distancia competencia directa m": proximity["nearest_competitor_m"],
        "Cobertura OSM verificada": bool(metrics.get('competition_source')=='OpenStreetMap'),
        "Competidores OSM": proximity['competitors'],
        "Canibalización propia": proximity["cannibalization"],
        "Distancia misma cadena m": proximity["nearest_same_chain_m"],
        "Sinergia logística / red": (
            own_metrics.get("count_5km")
            if own_metrics.get("available") else None
        ),
        "Accesibilidad": accessibility,
        "Afinidad socioeconómica": socioeconomic_score_for_record(rec),
        # Inputs específicos del motor V37
        "Población nivel V37": rec.get("Población nivel V37"),
        "Densidad nivel V37": rec.get("Densidad nivel V37"),
        "Movilidad nivel V37": rec.get("Movilidad nivel V37"),
        "Cupo nivel V37": rec.get("Cupo nivel V37"),
        "Competencia ponderada V37": rec.get("Competencia ponderada V37", rec.get("Competencia ponderada")),
        "Visibilidad V37 índice": metrics.get("road_access_score"),
        "Accesibilidad V37 índice": accessibility,
        "Flujo V37 índice": metrics.get("activity_score"),
        "NSE V37 nivel": V37_SOCIO_MAP.get(socioeconomic_level_for_record(rec), "Regular"),
    }



# =============================================================
# MOTOR V37 — score de oportunidad (interfaz V33)
# =============================================================
V37_SCALE = {
    "Excelente": 100.0,
    "Muy bueno": 90.0,
    "Bueno": 78.0,
    "Aceptable": 65.0,
    "Regular": 50.0,
    "Malo": 30.0,
}
V37_TERRITORIAL = {
    "Muy bueno": 90.0,
    "Bueno": 78.0,
    "Regular": 50.0,
    "Malo": 30.0,
}
V37_PRESSURE = {
    "Muy baja": 100.0,
    "Baja": 90.0,
    "Media": 78.0,
    "Alta": 50.0,
    "Muy alta": 30.0,
}
V37_BINARY_RISK = {
    "No": 100.0,
    "Sí": 30.0,
}
V37_COVERAGE = {
    "Sin presencia": 78.0,
    "Muy baja": 90.0,
    "Baja": 78.0,
    "Media": 65.0,
    "Alta": 50.0,
    "Muy alta": 30.0,
}
V37_SOCIO_MAP = {
    "Poco": "Malo",
    "Regular": "Regular",
    "Alto": "Bueno",
    "Muy alto": "Excelente",
}


def v37_output_level(score):
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return "Sin dato"
    value = float(score)
    if value < 50:
        return "Malo"
    if value < 65:
        return "Regular"
    if value < 75:
        return "Aceptable"
    if value < 85:
        return "Bueno"
    if value < 90:
        return "Muy bueno"
    return "Excelente"


def v37_recommendation(level):
    return {
        "Excelente": "PRIORIDAD ALTA",
        "Muy bueno": "PRIORIDAD ALTA",
        "Bueno": "AVANZAR",
        "Aceptable": "EVALUAR FAVORABLEMENTE",
        "Regular": "EVALUAR",
        "Malo": "BAJA PRIORIDAD",
    }.get(level, "SIN DATOS SUFICIENTES")


def v37_pressure_level(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    x = max(float(value), 0.0)
    if x <= 0.50:
        return "Muy baja"
    if x <= 1.50:
        return "Baja"
    if x <= 3.00:
        return "Media"
    if x <= 5.00:
        return "Alta"
    return "Muy alta"


def v37_structural_pressure_level(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    x = float(value)
    if x <= 4:
        return "Muy baja"
    if x <= 8:
        return "Baja"
    if x <= 12:
        return "Media"
    if x <= 18:
        return "Alta"
    return "Muy alta"


def v37_coverage_level(count):
    if count is None or (isinstance(count, float) and math.isnan(count)):
        return None
    x = max(float(count), 0.0)
    if x == 0:
        return "Sin presencia"
    if x <= 2:
        return "Muy baja"
    if x <= 5:
        return "Baja"
    if x <= 8:
        return "Media"
    if x <= 12:
        return "Alta"
    return "Muy alta"


def v37_index_input_level(value):
    """Convierte un índice OSM 0-100 a la escala cualitativa de entrada V37."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    x = min(max(float(value), 0.0), 100.0)
    if x >= 92:
        return "Excelente"
    if x >= 78:
        return "Muy bueno"
    if x >= 65:
        return "Bueno"
    if x >= 50:
        return "Aceptable"
    if x >= 35:
        return "Regular"
    return "Malo"


def v37_operation_level(property_data):
    if not property_data:
        return None
    value = str(property_data.get("operation_v37") or "Sin validar").strip()
    return value if value in V37_SCALE else None


def referencias_censales(tipo):
    """Solo compara unidades geográficas del mismo tipo y fuente censal 2022."""
    dataset=st.session_state.get('censo2022') or {}
    values=[]
    for (_,unidad),row in dataset.items():
        if unidad==tipo:
            population=row.get('Población')
            if population is not None: values.append(population)
    return {'Población':values}


def v37_weight_map(group_profile_df=None):
    weights = dict(PESOS_POTENCIAL)
    if group_profile_df is not None:
        try:
            for _, row in group_profile_df.iterrows():
                name=str(row.get('Factor',''))
                if name in weights:
                    weights[name]=max(float(row.get('Peso %',weights[name])),0.)
        except Exception:
            pass
    return weights


def score_company_location(rules_df, observations, format_name='Express',
                           group_profile_df=None, property_data=None, referencias=None):
    """Evaluación comercial de ubicación; red propia solo afecta conveniencia."""
    weights=v37_weight_map(group_profile_df)
    calculated=evaluar_potencial_y_expansion(observations, pesos=weights,referencias=referencias)
    values=calculated['factors']
    rows=[]
    for factor in EXECUTIVE_FACTORS:
        name=factor['Factor']; value=values.get(name)
        rows.append({'Factor':name,'Descripción':factor['Descripción'],
                    'Componentes':list(factor['Componentes']),
                    'Score actual':value,'Nivel encontrado':v37_output_level(value),
                    'Nivel buscado':'Automático','Score':value,
                    'Peso %':(25. if name=='Red propia' else weights[name]),
                    'Cobertura %':calculated['factor_coverage'].get(name,0.),
                    'Peso efectivo':(25. if name=='Red propia' else weights[name]) if value is not None else 0.})
    nearest=observations.get('Distancia misma cadena m')
    technical_rows=[
        {'Variable':'Población','Valor mostrado':f"{observations.get('Población') or '—'} · {observations.get('Fuente población','Fuente sin año documentado')}",'Score':None},
        {'Variable':'Población territorial','Valor mostrado':str(observations.get('Población nivel V37') or '—'),'Score':None},
        {'Variable':'Competidor más cercano','Valor mostrado':('—' if observations.get('Distancia competencia directa m') is None else f"{observations['Distancia competencia directa m']:.0f} m"),'Score':None},
        {'Variable':'Carrefour más cercano','Valor mostrado':('—' if nearest is None else f'{nearest:.0f} m'),'Score':None},
        {'Variable':'Riesgo de canibalización','Valor mostrado':'ELEVADO' if calculated['cannibalization_alert'] else 'Sin alerta crítica detectada','Score':None},
        {'Variable':'Alcance','Valor mostrado':'Comercial y territorial; no edilicio','Score':None},
    ]
    return {
        'sufficient_data':calculated['sufficient_data'],
        'missing_essential_factors':calculated['missing_essential_factors'],
        'score':calculated['expansion_score'],
        'commercial_score':calculated['commercial_score'],
        'expansion_score':calculated['expansion_score'],
        'network_score':calculated['network_score'],
        'coverage':calculated['coverage'],
        'confidence':'Alta' if calculated['coverage']>=95 else 'Media' if calculated['coverage']>=70 else 'Baja',
        'recommendation':calculated['recommendation'],
        'rows':rows,'technical_rows':technical_rows,'excluded_failures':[],
        'total_weight':100.,'available_weight':calculated['coverage'],
        'method':calculated['method'],
        'cannibalization_alert':calculated['cannibalization_alert'],
    }



def active_company_profile():
    company = st.session_state.get(
        "company_chain_selector",
        st.session_state.get("company_name", "Carrefour"),
    )
    format_label = st.session_state.get("company_format_selector")
    fmt = (
        FORMAT_LABEL_TO_MODEL.get(format_label)
        if format_label in FORMAT_LABEL_TO_MODEL
        else st.session_state.get("company_format", "Market")
    )
    return {
        "company": company,
        "profile_name": f"{company} — {FORMAT_MODEL_TO_LABEL.get(fmt, fmt)}",
        "format": fmt,
        "rules": st.session_state.get("score_rules_df", default_score_rules_df()),
        "groups": st.session_state.get("group_profile_df", default_group_profile_df()),

    }


def profile_json_payload(profile):
    rules = profile["rules"].copy()
    groups = profile["groups"].copy()
    payload = {
        "company": profile["company"],
        "profile_name": profile["profile_name"],
        "format": profile["format"],
        "groups": groups.to_dict(orient="records"),
        "rules": rules.to_dict(orient="records"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def apply_profile_payload(payload):
    st.session_state["company_name"] = payload.get("company", "Carrefour")
    st.session_state["company_format"] = payload.get("format", "Market")
    rules = payload.get("rules")
    if rules:
        incoming = pd.DataFrame(rules)
        base = default_score_rules_df()
        keep_cols = list(base.columns)
        for col in keep_cols:
            if col not in incoming.columns:
                incoming[col] = base[col]
        st.session_state["score_rules_df"] = incoming[keep_cols]

    groups = payload.get("groups")
    # No migrar pesos de cinco factores a un sistema de tres factores territoriales.
    if groups:
        _groups_names={str(x.get('Factor','')) for x in groups if isinstance(x,dict)}
        if SOCIOECONOMIC_FACTOR in _groups_names or not set(PESOS_POTENCIAL).issubset(_groups_names):
            groups=None
    if groups:
        incoming_groups = pd.DataFrame(groups)
        base_groups = default_group_profile_df()
        merged_groups = base_groups.copy()
        if "Factor" in incoming_groups.columns:
            incoming_groups = incoming_groups.set_index("Factor")
            for idx, base_row in merged_groups.iterrows():
                factor = base_row["Factor"]
                if factor not in incoming_groups.index:
                    continue
                incoming_row = incoming_groups.loc[factor]
                if isinstance(incoming_row, pd.DataFrame):
                    incoming_row = incoming_row.iloc[0]
                if factor == SOCIOECONOMIC_FACTOR:
                    if "Peso %" in incoming_row and pd.notna(incoming_row["Peso %"]):
                        merged_groups.at[idx, "Peso %"] = float(incoming_row["Peso %"])
                    merged_groups.at[idx, "Nivel buscado"] = "Automático por barrio/localidad"
                    continue

                if "Peso %" in incoming_row and pd.notna(incoming_row["Peso %"]):
                    merged_groups.at[idx, "Peso %"] = float(incoming_row["Peso %"])
                level = incoming_row.get("Nivel buscado", base_row["Nivel buscado"])
                if level in QUALITATIVE_LEVELS:
                    merged_groups.at[idx, "Nivel buscado"] = level
        st.session_state["group_profile_df"] = merged_groups
    else:
        # Compatibilidad con perfiles anteriores a v25.
        st.session_state["group_profile_df"] = default_group_profile_df()

    # Limpiar estados de widgets para que el perfil importado se vea de inmediato.
    for key in list(st.session_state.keys()):
        if (
            key.startswith("weight_")
            or key in {
                "company_chain_selector",
                "company_format_selector",
                "score_rules_advanced_editor",
                "group_profile_editor",
                "socioeconomic_weight_editor",
            }
        ):
            st.session_state.pop(key, None)


def company_score_explanation(score_result, own_metrics):
    score = score_result.get("score")
    if score is None:
        return "No hay suficientes datos para calcular un score confiable."

    rows = [r for r in score_result["rows"] if r["Score"] is not None]
    strongest = sorted(rows, key=lambda r: r["Score"], reverse=True)[:2]
    weakest = sorted(rows, key=lambda r: r["Score"])[:2]
    strong_txt = ", ".join(f"{r['Factor']} ({r['Nivel encontrado']})" for r in strongest)
    weak_txt = ", ".join(f"{r['Factor']} ({r['Nivel encontrado']})" for r in weakest)

    txt = (
        f"La conveniencia de apertura obtiene {score:.1f}/100 y el potencial comercial "
        f"{score_result.get('commercial_score',0.):.1f}/100, "
        f"con una cobertura de datos de {score_result['coverage']:.0f}%. "
        f"Las fortalezas principales son {strong_txt}. "
        f"Los factores más débiles son {weak_txt}."
    )
    if not own_metrics.get("available"):
        txt += (
            " La red propia no fue cargada: la cobertura logística queda sin validar, "
            "pero la canibalización binaria puede inferirse si OpenStreetMap identifica la misma cadena cerca del punto."
        )
    return txt


def preview_address_location(address):
    """Geocodifica y asocia la dirección al modelo, sin ejecutar todavía el análisis microlocal."""
    address = str(address or "").strip()
    if not address:
        return {"error": "Ingresá una dirección."}

    try:
        geo = geocode_address(address)
    except GeocodingRateLimitError:
        return {
            "error": (
                "El geocodificador de OpenStreetMap está temporalmente limitado. "
                "Esperá unos minutos y volvé a intentar."
            )
        }
    except GeocodingTemporaryError:
        return {
            "error": (
                "El geocodificador está temporalmente ocupado. "
                "Volvé a intentar en unos minutos."
            )
        }

    if not geo:
        return {"error": "No pude localizar la dirección."}

    rec, matched_component = resolve_model_location(geo)
    if not rec:
        return {
            "error": "La dirección fue localizada, pero no pude asociarla a una ubicación del modelo.",
            "geo": geo,
            "input_address": address,
        }

    return {
        "geo": geo,
        "record": rec,
        "matched_component": matched_component,
        "input_address": address,
    }


def fetch_address_analysis_from_preview(
    preview, seed, format_override=None, force_format_override=False,
):
    """Ejecuta OSM + simulación a partir de una ubicación previamente confirmada."""
    if not preview or preview.get("error"):
        return preview or {"error": "No hay una ubicación confirmada."}

    geo = preview["geo"]
    rec = aplicar_censo(preview["record"], st.session_state.get('censo2022'))
    lat = float(geo["lat"])
    lon = float(geo["lon"])

    osm = query_osm_microlocation(lat, lon)
    metrics = derive_micro_metrics(lat, lon, osm)
    metrics = apply_competition_sources(metrics, lat, lon)
    sim = analyze_address(
        rec,
        metrics,
        seed=seed,
        format_override=format_override,
        force_format_override=force_format_override,
        address_text=preview.get("input_address", ""),
        lat=lat, lon=lon,
        network_df=st.session_state.get("own_network_df"),
    )

    return {
        "geo": geo,
        "record": rec,
        "metrics": metrics,
        "sim": sim,
        "matched_component": preview.get("matched_component"),
        "input_address": preview.get("input_address", geo.get("display_name", "")),
    }


def fetch_address_analysis(address, seed, format_override=None):
    """Atajo usado por comparación: localiza y analiza en un único paso."""
    preview = preview_address_location(address)
    if preview.get("error"):
        return preview
    return fetch_address_analysis_from_preview(
        preview,
        seed=seed,
        format_override=format_override,
    )

# -----------------------------
# Interfaz
# -----------------------------

