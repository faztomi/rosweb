# ROS V37 · Portación web del evaluador local

Esta edición usa el **motor de evaluación territorial V37 de octubre 2026**, con sus funciones originales de geocodificación Nominatim, consulta Overpass, clasificación de comercios/transporte, puntajes y Censo 2022 por barrio/municipio. El sistema de búsqueda de direcciones mantiene además el componente de respaldo Georef Argentina → Nominatim → Photon utilizado en versiones locales posteriores.

**No se usa la aplicación ROS Web v2-v5, ni datos OSM externos generados en GitHub, ni GitHub Actions.** Se excluyen explícitamente los archivos históricos de facturación y de sucursales Carrefour; no los publiques en repositorios públicos sin autorización.

## Publicar en tu página existente

1. Descomprimí el ZIP.
2. En el repositorio `rosweb` de GitHub, rama `main`, subí el contenido de la carpeta extraída. Sobrescribí `app.py` y los otros archivos con los del ZIP. Es importante que `model_data.json`, `CENSO_2022_VERIFICADO.csv` y `CENSO_2022_MUNICIPIOS.csv` estén **junto a `app.py`**.
3. Eliminá del repositorio los archivos de las versiones web anteriores que no se usan (`engine.py`, `services.py`, `osm_snapshot.py`, `caba_censo.py`, `tiles.py`, `data/`, `.github/workflows/update-maps.yml`) **solo si no necesitás conservarlos para otro proyecto**. La nueva app ya no depende de ellos.
4. En Streamlit Community Cloud, verificá `Main file path: app.py` y que la versión de Python sea compatible con `requirements.txt`.
5. Recargá la web (Ctrl+F5). Evaluá una dirección de CABA y una de PBA, con y sin archivo de red.

## Funcionamiento

La red es opcional. Si subís CSV/Excel con `Nombre`, `Dirección`, `Latitud`, `Longitud`, se utiliza lo que tenga coordenadas verificadas para evaluar superposición con sucursales propias. Las direcciones de sucursales que no tengan coordenadas **no impiden** calcular el potencial comercial territorial. La app **no geocodifica masivamente** 100+ sucursales al evaluar una sola dirección, por los límites del servicio público de mapas.

El puntaje principal es **Potencial comercial V37**, calculado con el motor original sobre Demanda y mercado (40%), Entorno comercial (30%) y Espacio competitivo (30%). Cuando existe red georreferenciada, también aparece **Conveniencia de apertura**, que considera red propia. No incluye evaluación edilicia.

Censo incorporado: 48 barrios CABA y los municipios PBA presentes en `CENSO_2022_MUNICIPIOS.csv`, a escala de barrio o municipio, no radio censal. Fuera de la base histórica de 81 ubicaciones no se asigna un resultado.

La consulta a OpenStreetMap se realiza **en vivo mediante Overpass**, exactamente como en la app V37; para una web pública no es posible garantizar la disponibilidad de los servidores públicos, especialmente desde una IP compartida de Streamlit Cloud. Si fallan, se informa el error sin inventar un score. Se recomienda comprobar la conectividad de la web desplegada antes de abrirla al público.
