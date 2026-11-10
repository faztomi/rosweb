# ROS Simple WEB — listo para publicar

**Página web de la aplicación ROS Simple** construida sobre Streamlit.
No se necesita el archivo `.bat` ni instalar Python en las computadoras o celulares de los visitantes.

## Publicación en Streamlit Community Cloud

1. Crear una cuenta gratuita en https://github.com y entrar a https://github.com/new.
2. Crear un repositorio público, por ejemplo `ros-simple-web`. **No publicar allí redes privadas, claves ni datos internos.**
3. Descomprimir este ZIP y **subir los archivos y carpetas de su interior al repositorio** (no subir el ZIP como un solo archivo). En la raíz deben quedar `app.py`, `requirements.txt`, `engine.py`, `services.py`, `tiles.py`, `data/` y `.streamlit/`.
4. Entrar con GitHub en https://share.streamlit.io y pulsar **Create app**.
5. Elegir el repositorio, la rama `main` y el archivo principal `app.py`.
6. En la configuración avanzada elegir Python 3.12 si ofrece la selección. No se requieren claves API.
7. Publicar la aplicación. Streamlit entregará un enlace público `https://...streamlit.app`.
8. Abrir el enlace desde un teléfono y una computadora para verificar su funcionamiento.

## Qué hace cada visitante

1. Usa la red pública parcial DÍA de ejemplo o sube su propia red (CSV / XLSX, hasta 15 MB).
2. Escribe una dirección (o coordenadas) y hace clic en **Calcular score**.
3. Ve el score y las dimensiones: demanda residencial 35 %, actividad 25 %, accesibilidad 15 %, competencia 15 %, servicios 10 %.

## Privacidad y funcionamiento

- Las redes subidas se guardan solamente en el estado de la **sesión** del visitante. No se sobrescribe la red de otros usuarios. Al terminar o reiniciar la sesión se pierde el archivo y hay que volver a subirlo.
- Los servicios externos de geocodificación reciben la dirección que el usuario solicita localizar.
- El ejemplo DÍA es una **red pública parcial y no verificada**. No equivale a todas las sucursales actuales.
- La base incluida del Censo 2022 cubre **Provincia de Buenos Aires**, no CABA ni el resto de Argentina. En zonas sin población censal el resultado debe figurar parcial.
- Los comercios, el transporte y la competencia proceden de datos geográficos abiertos; los servicios públicos pueden saturarse. El score es heurístico y exploratorio, NO predicción de ventas ni probabilidad de éxito.
- En hosting gratuito la app puede dormir por inactividad y tener límites de recursos. Para uso profesional intensivo, usar alojamiento pago y/o un proveedor de mapas con mayor disponibilidad.
- No se incluye microubicación del inmueble.

## Recomendaciones de operación

- No enviar redes confidenciales a un alojamiento público sin revisar las reglas de manejo de datos de la empresa.
- No publicar llaves privadas ni credenciales en GitHub.
- Para dejar una red privada guardada entre visitas se requiere iniciar sesión y una base de datos segura; deliberadamente esta versión no hace eso.

## Pruebas

```bash
python -m pip install -r requirements.txt
python -m pytest -q test_app.py test_web.py
streamlit run app.py
```

Documentación oficial: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
