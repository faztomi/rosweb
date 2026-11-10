# ROS Web — versión revisada de geolocalización, Censo 2022 y datos OSM

**Objetivo del usuario:** cargar una dirección y obtener el índice comercial con sus cinco dimensiones. El archivo de sucursales es opcional. No se necesitan claves ni configuraciones dentro de la página.

## Qué cambió

1. **Direcciones:** se valida calle, altura y municipio, no solo un nombre parecido. Se descartará `Loria 355, Glew` si el usuario pidió `Ioria 355, Lomas de Zamora`. Las coincidencias solo por calle no se puntúan automáticamente.
2. **Censo PBA:** se incluye una base real de radios 2022 en `data/censo_pba_2022.geojson.gz` (23.882 radios con población), y se verifica antes de generar un score.
3. **Censo CABA:** el código puede leer una base local de radios de CABA generada por el workflow, y como respaldo la consulta a la capa 2022 en línea. Si ninguna está disponible no inventa habitantes.
4. **OpenStreetMap:** el principal cambio. Se utiliza un extracto público de Buenos Aires (BBBike) para crear una base espacial SQLite local. Se actualiza en GitHub Actions y se intenta crear automáticamente al usar la app si no existiera. Esto evita que cada búsqueda dependa del servicio público Overpass. Fuera del extracto de Buenos Aires se mantiene la consulta directa V37 como alternativa.
5. **Red de sucursales:** es opcional. Cuando se sube, se usan solo las coordenadas válidas y no se impide el análisis por filas pendientes.

**Nota metodológica:** OSM es una fuente colaborativa, no un censo exhaustivo de comercios. Los puntajes son heurísticos y no equivalen a una probabilidad de ventas. No se evalúa el interior, frente ni estado físico del inmueble.

## Publicar en el repositorio existente rosweb

1. Descargar el ZIP, elegir **Extraer todo** y abrir la carpeta descomprimida.
2. Abrir el repositorio `rosweb` en GitHub (rama `main`).
3. Subir **el contenido** de la carpeta extraída a la raíz del repositorio, no el ZIP solo. Reemplazar los archivos con el mismo nombre. Verificar que `data/censo_pba_2022.geojson.gz` figure en GitHub.
4. Subir también la carpeta oculta `.github/workflows` (fundamental para la actualización automática de mapas). En Windows, si no se ve, activar **Ver → Mostrar → Elementos ocultos**.
5. GitHub Actions debería ejecutar `Actualizar mapa OSM de Buenos Aires` al recibir el archivo `scripts/build_osm_snapshot.py`. Si no se inicia, abrir **Actions → Actualizar mapa OSM de Buenos Aires → Run workflow**. Si se ejecuta con éxito, publica `data/osm_buenosaires.sqlite.gz` o un manifiesto con varias partes `data/osm_part_*.bin`.
6. Streamlit actualiza la web desde `main` automáticamente. No crear un repositorio ni una URL nuevos.
7. Probar con y sin sucursales, por ejemplo con la dirección `Mariano S. Ioria 355, Lomas de Zamora, Buenos Aires, Argentina` (si Georef confirma municipio y altura), y luego otra dirección de CABA.

**Sin pasos de GitHub Actions:** si el mapa aún no está guardado, la aplicación intentará descargar automáticamente el extracto BuenosAires (aprox. 37 MB) al calcular el primer score. Esa primera consulta puede tardar, o fallar si el alojamiento bloquea descargas. La publicación del mapa por GitHub Actions es el camino recomendado para que los visitantes no esperen.

## Cobertura geográfica

- **Base OSM local:** CABA y área metropolitana dentro del rectángulo oficial del extracto BBBike BuenosAires, lon -58.85 a -58.12, lat -34.85 a -34.40. Las áreas de borde están excluidas si el radio consultado excede el extracto.
- **Censo PBA por radio:** Provincia de Buenos Aires, donde el archivo tenga polígonos con población.
- **Censo CABA por radio:** requiere que la copia generada por la acción o la consulta externa responda.
- **Otras regiones:** la app conserva la alternativa Overpass V37, que depende de un servicio externo y no tiene garantía de disponibilidad pública.

## Verificación técnica (solo para el administrador)

- `https://rosweb.streamlit.app/?diagnostico=1` muestra si hay Censo PBA y base OSM disponibles, y la fecha de los datos. La interfaz normal no muestra configuraciones.
- En GitHub, el workflow publica el mapa únicamente si descarga, procesa y comprueba el extracto, y si los puntos de prueba de CABA/Lomas devuelven elementos.
- Pruebas de código: `ROS_ALLOW_BOOTSTRAP=0 python -m pytest -q` (con las librerías del `requirements.txt` y pytest instalado).

## Lo que todavía hay que verificar en producción

Este ZIP se comprobó localmente con el Censo real de PBA y simulación de un archivo geográfico OSM completo, pero **no se pudo efectuar la descarga real del extracto OSM en este entorno sin acceso a Internet**. La prueba definitiva es la acción de GitHub en verde seguida de una evaluación dentro de Streamlit Cloud. No consideres producción habilitada si la acción falla o los diagnósticos no muestran fuentes disponibles.
