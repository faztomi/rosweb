# ROS Web — base territorial original (sin dependencia de OSM)

## Alcance real

Esta versión está construida a partir de `ROS integrado con facturación, simulador y ranking.xlsm`.
Reproduce el **ROS territorial** que ya existía en ese archivo. Contiene 48 barrios de CABA y 33 municipios de PBA; las cifras corresponden a las fuentes y fechas de actualización del Excel original. **No es** un score microlocal ni una predicción de ventas.

No usa OpenStreetMap para calcular el score. OpenStreetMap/Overpass no es una fuente confiable de disponibilidad continua en su servicio público gratuito para una aplicación web multiusuario. La calle y la altura pueden verificarse opcionalmente con Georef Argentina, sin impedir el cálculo territorial cuando el usuario indicó el municipio o barrio.

## Publicar

1. Crear una rama de respaldo del repositorio antes de reemplazar la aplicación actual.
2. Subir los archivos y la carpeta `data/` tal como están, manteniendo `app.py` y `requirements.txt` en la raíz.
3. Publicar en Streamlit Community Cloud apuntando a `app.py`.
4. Probar `Ioria 355, Lomas de Zamora`, `Combate de los Pozos 100, Balvanera, CABA`, primero sin red y luego con red CSV.
5. No agregar al repositorio público archivos de facturación, ventas ni ninguna otra información empresarial confidencial.

## Trazabilidad

`data/ros_territorios.json` se generó directamente del `.xlsm` aportado. Incluye nombre de archivo y huella SHA-256 del original. Para PBA toma el campo "Población 2022" del Excel; para CABA usa su columna "Población oficial", que no debe confundirse automáticamente con población por radio de Censo 2022.

## Diferencia con ROS 2.0

El ROS 2.0 tenía cinco dimensiones y dependía de datos OSM actualizados para algunas de ellas. Esta edición deliberadamente usa las cuatro variables de la base ROS original, con los mismos pesos, porque es el único resultado reproducible con la información que está disponible en el libro de Excel. No se muestran falsos puntajes microlocales.
