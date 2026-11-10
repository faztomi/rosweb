# ROS Web — versión 5 (dirección primero, red opcional)

Evaluador de atractivo comercial con cinco dimensiones. No evalúa fachada, metros cuadrados ni características físicas del inmueble. **El score es un índice exploratorio, no una probabilidad de ventas ni un modelo estadísticamente validado.**

## Uso público

1. Escribí una dirección con calle, altura, localidad y provincia.
2. Opcional: subí un Excel o CSV de sucursales de tu cadena.
3. Presioná **Calcular score**.

La red **NO es obligatoria**. Puede tener direcciones sin coordenadas: eso no impide evaluar la ubicación. Si contiene coordenadas comprobadas, estas ayudan a distinguir sucursales propias cercanas. Un CSV con nombres de cadenas también ayuda a no tratar la propia marca como competidora en OSM.

## Publicar la actualización en rosweb.streamlit.app

- Descomprimí todo este ZIP.
- En GitHub, en tu repositorio `rosweb`, reemplazá el contenido por estos archivos (no subas el ZIP cerrado).
- Confirmá con **Commit changes** en `main`.
- Asegurate de que se vea `.github/workflows/update-maps.yml` y la carpeta `scripts/`.
- En GitHub, abrí **Actions → Preparar datos cartograficos ROS → Run workflow** si no arrancó automáticamente.
- **Esperá a que ese proceso termine en verde antes de evaluar la web.** Su tarea es descargar extractos actuales de OpenStreetMap de Provincia de Buenos Aires y CABA, convertirlos en una base de datos local y publicar los archivos `data/osm_part_*.bin` y `data/osm_snapshot_manifest.json` en el repositorio. No hay que subirlos manualmente.
- Volvé a cargar `https://rosweb.streamlit.app/` una vez publicados los mapas.

### Verificar sin mostrar mensajes técnicos al público

Abrí `https://rosweb.streamlit.app/?diagnostico=1`. En *Estado técnico de las fuentes* debe figurar **Base OSM: disponible** y **Censo PBA: disponible**. Para direcciones en CABA, la app utiliza el Censo 2022 de su fuente geográfica, preferentemente una copia local si el proceso de GitHub consiguió publicarla.

Cuando falta la base OSM, la app intenta una consulta acotada a Overpass como contingencia, pero **Overpass público no garantiza respuesta**. La base OSM local es la solución principal.

### Si el proceso de mapas de GitHub falla

Abrí el trabajo fallido en *Actions* y revisá el primer paso rojo. Si el error es `Permission denied` al hacer push, revisá en GitHub `Settings → Actions → General → Workflow permissions` y autorizá **Read and write permissions** para el repositorio. Si el fallo es por una descarga externa, los datos NO se publican como si estuvieran completos.

## Datos y fuentes

- Geocodificación: Georef Argentina (V2/V0), Photon y Nominatim como alternativa para consultas puntuales; también admite coordenadas obtenidas de un mapa.
- Censo 2022 PBA: `data/censo_pba_2022.geojson.gz` incluido, población ponderada por superficie de radio; la densidad NO es una medición directa de tránsito peatonal.
- Censo CABA: radios 2022 en servicio ArcGIS, con publicación automática opcional del extracto local.
- Actividad, transporte, supermercados, servicios: OpenStreetMap, inicialmente datos regionales de `download.openstreetmap.fr`, licencia ODbL, © OpenStreetMap contributors. Los registros OSM pueden estar incompletos.

No poner contraseñas, datos comerciales sensibles ni información privada en un repositorio público.

## Pruebas incluidas

```bash
python test_app.py
python test_integration.py
python test_presentation_smoke.py
```

Son pruebas locales con datos simulados en los apartados de servicios. **No prueban la conexión real de Streamlit Cloud**. Para certificar la publicación hay que ejecutar el proceso de mapas y evaluar al menos una dirección real con y sin red.


## Estado de datos: comprobación obligatoria de publicación

El ZIP incluye `data/censo_pba_2022.geojson.gz`, 23.882 radios y 17.408.906 habitantes en viviendas particulares.
Si no se sube esta carpeta a la **raíz** del repositorio, la app no podrá calcular demanda en PBA.

GitHub Actions **Preparar datos cartográficos ROS** debe terminar verde y publicar
`data/osm_snapshot_manifest.json` **y** todas las partes `data/osm_part_*.bin`.
Estas partes contienen los datos de OpenStreetMap; NO están en el ZIP porque se generan
a partir de extractos oficiales después de subir el código.

Para comprobar los datos tras publicar, abrí `https://rosweb.streamlit.app/?diagnostico=1`
y buscá: Censo PBA: disponible; Base OSM: disponible. Este panel es técnico y
no se presenta en la interfaz normal. Si cualquiera marca no cargado, falta
publicar datos; no se arregla reintentando la dirección.

### Pruebas hechas antes de entregar
- `python scripts/check_census_pba.py` usa población REAL de dos puntos PBA.
- `python -m pytest -q` prueba geocodificación simulada y scoring completo.
- La descarga y procesamiento OSM en GitHub NO se comprueban desde este contenedor;
  solo se pueden verificar en el flujo de acciones del repositorio.
