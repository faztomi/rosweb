# ROS Web — ampliación segura del evaluador territorial

## Qué mantiene
- No cambia `engine.py`: conserva los 81 territorios y exactamente las cuatro puntuaciones y pesos del Excel `ROS integrado con facturación, simulador y ranking.xlsm`.
- El score territorial aparece aunque Georef u OpenStreetMap no respondan.
- Se puede usar **sin cargar ninguna red de sucursales**.

## Qué agrega
1. Consulta geográfica por calle + altura con validación de provincia/municipio, sin aceptar otra localidad para dar distancias.
2. Supermercados y autoservicios registrados en OSM dentro de un radio aproximado de 1 km, más paradas y ejes viales. Metros de distancia en línea recta. Depende de la disponibilidad del servicio OSM/Overpass.
3. Proximidad a sucursales de la red cargada, alerta de posible canibalización y distancia logística indicativa. Para medir la red se necesitan coordenadas (`Latitud`, `Longitud`) o una marca identificable en OSM. **No estima ventas canibalizadas ni tiempos de logística.**
4. Simulador de 10.000 escenarios lognormales con datos de ventas y objetivos introducidos por el usuario: P10, P50, P90 y probabilidad de superar el objetivo. No se publica la base interna de ventas Carrefour ni se infiere facturación a partir del ROS.

## ACTUALIZAR TU WEB ACTUAL (recomendado)

No reemplaces el `engine.py` que actualmente te funciona. Abrí `ROS_WEB_ACTUALIZACION_SIN_TOCAR_ENGINE.zip` y subí a la raíz de tu repositorio `rosweb` **solo estos cuatro archivos**:

- `app.py` (reemplaza al anterior)
- `geo_extra.py` (nuevo)
- `analysis_extra.py` (nuevo)
- `revenue.py` (nuevo)

Mantené `engine.py`, `georef.py`, `networks.py` y `requirements.txt` como están. Confirmá con **Commit changes**; Streamlit actualizará el mismo enlace.

Si la aplicación ya no tiene los archivos previos, usá el ZIP completo; dentro están todos los módulos del programa.

## Verificaciones incluidas
- Se comprobaron por código los 81 ROS contra el índice original, incluyendo Lomas de Zamora = 90,6/100.
- 15 pruebas automatizadas sin conexión, incluidas pantallas simuladas con red y sin red, OSM disponible y OSM caído, distancia de sucursales y simulador repetible.
- **Limitación pendiente**: no se ha probado desde este entorno una consulta Overpass real originada desde tu Streamlit Cloud; los servidores públicos pueden limitar las solicitudes. La funcionalidad territorial funciona sin esa dependencia. Una lista de supermercados **no se mostrará si la consulta no está respaldada por datos reales**.

## Qué significa cada indicador

- ROS territorial: barrio/municipio y datos de la base histórica; **no** puntuación por puerta.
- Comercios cercanos: OSM público; puede estar incompleto y las distancias son rectas, no recorridos.
- Canibalización: alertas de cercanía a tiendas propias, no cuantificación de ventas perdidas.
- Logística: proximidad a red y mención de conectividad cartográfica, no estimación de transporte ni costos.
- Facturación: escenarios según inputs cargados, no una proyección del local derivada automáticamente del ROS.
