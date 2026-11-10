# ROS WEB v4 — Base OSM propia (sin Overpass por evaluación)

**Para el propietario de la página**. El visitante solo realiza dos pasos: subir su red en Excel/CSV y escribir una dirección. No necesita configurar APIs, Censo ni mapas.

## Qué cambia

1. **NO llama a Overpass al calcular el score**: utiliza una base OSM local creada por GitHub Actions desde el extracto completo de Argentina de Geofabrik (© OpenStreetMap contributors, ODbL). Una vez creada, la base se guarda comprimida en el repositorio, dividida en partes verificadas mediante SHA-256. Cada consulta de la web usa esa copia.
2. La red del visitante sirve para distinguir tiendas propias de competidores, pero **no bloquea todo el análisis** si algunas sucursales no tienen coordenadas. La proximidad a sucursales sin coordenadas no puede estimarse.
3. El Censo 2022 de PBA está incorporado. Hay un proceso adicional que intenta guardar **CABA 2022** localmente; si ese servicio externo falla, la app conserva su modo de consulta externa para CABA.
4. **Nunca se publica un score incompleto**. Si la base aún no fue construida o falta información suficiente en la zona, se informa de forma breve al visitante sin asignar cero.
5. El modelo es **heurístico y no validado contra ventas reales**. Los datos OSM reflejan registros aportados por colaboradores, no un censo de todos los comercios ni aforos peatonales.

## Instalación en tu repositorio `rosweb` (misma dirección `rosweb.streamlit.app`)

1. Descargá y extraé el ZIP. En tu repositorio de GitHub `rosweb`, rama `main`, elegí **Add file > Upload files** y subí el contenido de la carpeta (no el ZIP). Confirmá los cambios. Verificá que `app.py`, `engine.py`, `osm_snapshot.py`, `requirements.txt`, la carpeta `data` y `.github/workflows/update-maps.yml` estén en sus rutas correctas. Si GitHub no sube directorios ocultos, agregá el archivo de workflow desde **Add file > Create new file**, usando como nombre `.github/workflows/update-maps.yml`, y copiá su contenido.
2. Entrá a **GitHub > rosweb > Actions**. Buscá **Preparar datos cartograficos ROS**. La primera subida puede iniciarlo automáticamente; si no, entrá y pulsá **Run workflow > main > Run workflow**.
3. Esperá a que el proceso de GitHub concluya en **verde**. Abrí los pasos de la ejecución: debe confirmar elementos para CABA, Lomas y La Plata. Si falla en rojo, **no des la aplicación por verificada**. Descargá o copiá el mensaje del paso fallido para corregirlo.
4. Tras la ejecución correcta, en `rosweb/data` deberían aparecer `osm_snapshot_manifest.json`, varios `osm_part_000.bin` etc. y, si respondió la fuente del Censo CABA, `censo_caba_2022.geojson.gz`.
5. Abrí `https://rosweb.streamlit.app/?diagnostico=1` para comprobar «Base OSM: disponible». Si sigue sin figurar, reiniciá la app desde **Manage app > Reboot app** y verificá que el workflow realmente haya escrito en `main`.
6. Probá una dirección de CABA y una de Provincia, y corroborá el desglose de cinco dimensiones. **No anuncies la app para uso comercial hasta completar estas pruebas reales en Streamlit Cloud.**

**Si el paso 3 falla al hacer `git push`**: en GitHub abrí `Settings > Actions > General > Workflow permissions` y verificá los permisos de escritura del repositorio. El workflow ya solicita `contents: write`. Volvé a ejecutarlo. Más detalles: https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow

La carga del extracto argentino completo sucede en GitHub (proceso del administrador), **nunca cuando un visitante hace clic**. Se actualiza automáticamente el primer día de cada mes; la programación depende de que GitHub Actions esté habilitado.

## Archivos principales

- `app.py`: interfaz pública.
- `engine.py`: cálculo territorial.
- `services.py`: geocodificación de la dirección y lectura de redes.
- `osm_snapshot.py`: lector de base OSM verificada, sin internet.
- `scripts/build_osm_snapshot.py`: construye la base desde Geofabrik en GitHub Actions.
- `.github/workflows/update-maps.yml`: automatiza la descarga, comprobaciones y publicación.
- `data/censo_pba_2022.geojson.gz`: Censo 2022 PBA, incorporado.
- `caba_censo.py` y `scripts/build_caba_snapshot.py`: población 2022 CABA.

## Cobertura y límites

- Datos OSM y población: PBA y CABA. Otras provincias no tienen garantía de cobertura con este paquete.
- Cinco componentes: demanda residencial (35%), actividad comercial registrada en OSM (25%), accesibilidad y transporte registrados (15%), demanda frente a competencia (15%), servicios complementarios (10%).
- **No incluye características físicas del inmueble**.
- Se admite CSV o Excel con columna `Dirección` o coordenadas `Latitud`/`Longitud`; `Cadena` es opcional. Si tenés coordenadas de la red, mejoran el análisis de las tiendas propias cercanas.
- Geocodificar una dirección sigue requiriendo un proveedor público (Georef, Photon o Nominatim), con sus propias limitaciones. También se aceptan coordenadas del punto copiadas de un mapa en el mismo cuadro de dirección.
- Las áreas empleadas por este motor son radiales y no isócronas caminando. El score **no predice ventas reales ni es una probabilidad de éxito**.

## Pruebas

`python -m pytest -q test_app.py test_caba.py test_snapshot.py test_web.py`

Se prueban cálculos, lectura de CSV, importación de red parcialmente geocodificada, reconstrucción de una base SQLite dividida en partes y detección de una copia corrupta con **datos sintéticos de prueba**. Eso no sustituye ejecutar GitHub Actions con el **extracto OSM real** y probar la web ya desplegada.
