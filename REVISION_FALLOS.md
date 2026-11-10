# Auditoría ROS Web vs. V37 local — 10/11/2026

## 1. Geolocalización
- V37 local: Georef Argentina -> Nominatim -> Photon; consulta por dirección, sin
  penalizar por datos de una red que no se geolocalicen.
- ROS Web V5: también consulta estos servicios. La captura de Ioria 365 muestra
  coincidencia exacta por Georef. **El problema NO es localizar esa dirección.**

## 2. Censo
- Hay 23.882 radios de PBA y 17.408.906 personas (en viviendas particulares)
  en `data/censo_pba_2022.geojson.gz`. Probado directamente con Shapely:
  Lomas de Zamora y La Plata devuelven población local válida.
- La foto anterior del repositorio no muestra la carpeta `data`. El mensaje
  `Censo: no disponible` puede deberse a la ausencia del archivo en GitHub;
  sin acceso al repositorio en vivo no se puede confirmar la causa exacta.
- Ahora GitHub Actions falla con diagnóstico claro si se intenta construir
  los mapas sin la base PBA. No se inventa población.

## 3. OpenStreetMap
- V37 consultaba Overpass directamente. En una PC local el IP y red son distintos.
- ROS Web V5 prioriza `data/osm_snapshot_manifest.json` + `osm_part_*.bin`;
  en el ZIP de V5 solo está el script, NO la base descargada. Sin `Actions`
  completado, `Mapa comercial` resulta no disponible si el fallback falla.
- V6 prioriza OSM local; si falta, consulta Overpass con dos fuentes para la
  consulta completa y una tercera fuente con solicitudes divididas. Ninguna
  consulta fallida se convierte en 0 comercios ni en un score falso.

## 4. Qué falta verificar fuera del contenedor
- Que las acciones de GitHub generen y publiquen los datos OSM reales.
- Que Streamlit despliegue los archivos `data/` y lea `censo_pba_2022.geojson.gz`.
- Que la dirección Ioria 365 tenga las cinco dimensiones con cobertura suficiente.

**No se afirma que haya una prueba en vivo satisfactoria.**
