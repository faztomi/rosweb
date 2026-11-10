# ROS Web — Evaluador comercial

Aplicación web de evaluación de ubicaciones comerciales para **cualquier red de supermercados u otro negocio**. El visitante solo carga su red (CSV/XLSX), escribe la dirección y selecciona **Calcular score**. No hay sucursales de una cadena precargadas.

## Publicar en el repositorio existente `rosweb`

1. En GitHub, abrí el repositorio `rosweb` (rama `main`).
2. Subí **el contenido descomprimido** de este ZIP en la raíz del repositorio; reemplazá los archivos existentes. **Es indispensable subir también la carpeta `data`, con `data/censo_pba_2022.geojson.gz`.** No subas el ZIP sin extraer.
3. Verificá que en la raíz estén `app.py`, `engine.py`, `services.py`, `caba_censo.py`, `tiles.py` y `requirements.txt`.
4. Confirmá en **Commit changes**. Streamlit Community Cloud redeploya automáticamente la página ya creada; conservás la URL original.

Si usás GitHub Codespaces en lugar de la interfaz web, colocá los mismos archivos en la carpeta `/workspaces/rosweb`, verificá que `data` contenga el archivo censal, y hacé commit y push a `main`. No crees los archivos dentro de `.devcontainer`.

## Fuentes

- **Provincia de Buenos Aires:** radios censales 2022 + población por radio (Censo INDEC 2022) integrados en `data/censo_pba_2022.geojson.gz`.
- **CABA:** radios censales y población 2022 consultados automáticamente mediante el servicio público [ArcGIS de comparación Censo 2010/2022](https://services1.arcgis.com/gKFHG0iKXPnKkg1l/arcgis/rest/services/Censo_2010_2022_Comparaciones_Divisiones_Territoriales/FeatureServer/3). Datos de INDEC procesados y publicados cartográficamente por terceros. La consulta depende de disponibilidad externa; **no es una base censal CABA empaquetada para funcionar sin conexión**.
- **Comercios y transporte:** OpenStreetMap a través de Overpass. Una consulta fallida **nunca se convierte en puntaje cero**.
- **Georreferenciación:** Georef Argentina, Photon y Nominatim como servicios alternativos, sujetos a sus condiciones de uso.

## Qué mide (índice heurístico, no predicción)

- Demanda residencial: 35 %
- Actividad comercial registrada: 25 %
- Accesibilidad y transporte registrados: 15 %
- Equilibrio de competencia: 15 %
- Servicios complementarios: 10 %

**No incluye** frente, superficie, estado edilicio, ventas proyectadas, precio ni alquiler. Los parámetros **no están calibrados contra ventas observadas**. No se publica un score cuando faltan indicadores esenciales o hay cobertura insuficiente.

## Formato de red

Acepta CSV y Excel con columnas como `Dirección`, `Nombre`, `Latitud`, `Longitud`, `Cadena`. Solo es obligatorio contar con direcciones o con latitud y longitud por sucursal. Las coordenadas aumentan la confiabilidad y rapidez del cálculo. Se pueden usar nombres equivalentes de columnas.

## Validación y limitaciones

Se incluyen pruebas de lógica de cálculo y respuestas simuladas de la API CABA; **no se probó conectividad real desde Streamlit Cloud en esta entrega**. Antes de compartir la web públicamente, probar una dirección de PBA y una de CABA, y confirmar que la red se localice y que las cinco dimensiones tengan respaldo suficiente. El índice no equivale a una probabilidad de éxito comercial.
