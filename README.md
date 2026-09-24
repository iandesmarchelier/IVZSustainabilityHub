# IVZ Sustainability Hub · demo con backend Python

Primera implementación local sobre el HTML V1.4 entregado por el usuario. Datos ficticios, una cuenta por empresa. No se ha publicado en Vercel.

## Ejecutar con Python 3.12 o posterior

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m backend.demo
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Abrir http://127.0.0.1:8000. El usuario y la contraseña aleatoria de prueba están en `data/acceso-demo.txt`. No publicar ese archivo. Para crear cuentas adicionales: `python -m backend.manage usuario "Empresa"` (solicita la contraseña sin mostrarla).

## Qué incluye

- Acceso mediante cookie HttpOnly con caducidad, contraseña scrypt y separación de datos por cuenta.
- Importador original Excel/CSV y sus siete plantillas; validación adicional del dataset al guardarlo en Python.
- Mapa ESG (Panorama): globo 3D con cada ubicación operativa; para el indicador cuantitativo elegido, la altura del pin es la magnitud y el color el estado frente al objetivo, la variación interanual o el valor relativo. Si el objetivo es de un nodo superior, se aplica a la ubicación el mismo cambio relativo (métricas absolutas) o el mismo valor meta (porcentajes, promedios e intensidades). Las ubicaciones guardan `lat` y `lon` (alta de nodo, importación de datos maestros con columnas latitud/longitud, modal «Coordenadas de ubicaciones» o «Ubicar en el globo»); sin ellas se estiman por la ciudad del nombre o el país. El globo (`globe.js`, `world.js` con contornos Natural Earth de dominio público) es un canvas propio, sin servicios de mapas externos, compartido con IVZ Carbon.
- Estado persistente en SQLite local o PostgreSQL mediante DATABASE_URL. Guardado automático cada 2,5 segundos y botón manual. Avisos de errores y conflictos entre pestañas.
- Motor Python para indicadores anuales del reporte, selección de alcance y método de Scope 2.
- Reporte ESG general con indicadores, comparación con el año anterior y valores faltantes explícitos.
- Resumen ejecutivo Gemini opcional. Sin clave, se genera una plantilla marcada explícitamente «sin IA».
- Historial de reportes por empresa, edición, aprobación por el mismo usuario y auditoría de operaciones del servidor.
- PDF/impresión mediante el exportador original del navegador.
- Configuración por cliente (`backend/features.py`): en Administración, «Configurar» enciende o apaga para cada cuenta las secciones del menú (salvo Inicio) y los conectores de Integraciones. Lo apagado desaparece del menú y del recorrido guiado y el servidor lo bloquea (reportes e integración con IVZ Carbon devuelven 403); los datos no se borran. Desde el mismo panel el administrador conecta IVZ Carbon con el token del cliente, sincroniza si ya hay sitios mapeados y desconecta (`/api/admin/accounts/{id}/settings`, `/api/admin/accounts/{id}/carbon`).

## IA

Configurar GEMINI_API_KEY y GEMINI_MODEL en el entorno del proceso. `.env.example` es documentación: no se carga automáticamente. El modelo debe estar habilitado y tener cuota en Google AI Studio. La clave nunca llega al frontend.

```powershell
# Configurar la clave mediante el gestor de secretos o las variables del servidor.
$env:GEMINI_MODEL = "gemini-3.8-flash"
```

Se envían indicadores calculados, nombre de empresa y alcance; no el archivo original. Los errores del proveedor no se sustituyen silenciosamente por un resultado simulado. La llamada real requiere credenciales y todavía no se ha probado.

## Vercel

Mantener `index.html`, `bridge.js`, `reports-ui.js`, `carbon-integration-ui.js`, `map-ui.js`, `globe.js`, `world.js`, `backend/`, `api/`, `requirements.txt` y `vercel.json` juntos. Configurar un proyecto sin comando de build de frontend y DATABASE_URL para una base PostgreSQL externa. COOKIE_SECURE=1; GEMINI_API_KEY y GEMINI_MODEL opcionales. Crear cuentas con `backend.manage` usando esa misma DATABASE_URL desde un entorno administrativo.

SQLite se rechaza en Vercel para evitar pérdida de datos. El despliegue y PostgreSQL requieren verificación con las credenciales reales; no se han probado. Vercel limita cada solicitud a 4,5 MB, así que los datos no viajan en un solo bloque: las mediciones y los valores reales son filas en `state_rows` (con su orden en `seq`) y el resto del estado queda en `states`. La pantalla los carga por páginas (`/api/state/catalogue`, `/api/state/rows`) y guarda solo lo que cambió (`/api/state/changes`); un cambio grande viaja en partes (`/api/state/upload`) y se aplica entero o nada. El servidor valida siempre el estado completo.

Cada cuenta en el formato anterior (todo en `states`) se convierte sola la primera vez que se lee, y el bloque original queda en `state_backups`. Para volver a una versión anterior a este formato, correr antes `python -m backend.unsplit` con la misma DATABASE_URL: rearma el bloque único con los datos al día.

Cierre de años: desde el menú de la cuenta, el cliente o un administrador cierra un año (`POST /api/closures`); sus mediciones y valores reales ya no se pueden agregar, editar ni borrar (423), y la sincronización con IVZ Carbon lo saltea. Solo un administrador que entra como el cliente puede reabrirlo, con un motivo que queda en los eventos (`POST /api/closures/{year}/reopen`).

## SAP BTP

La aplicación ASGI es `backend.app:app`. Un despliegue Cloud Foundry puede arrancarla con `uvicorn backend.app:app --host 0.0.0.0 --port $PORT`. Adaptar base de datos, secretos e identidad corporativa antes de migrar; Python no hace automática esa migración.

## Validación

```powershell
node tests/extract-seed.cjs
python -m unittest discover -s tests -v
node --check bridge.js
```

Las pruebas usan SQLite temporal, dos empresas y el dataset extraído del frontend: autenticación, separación de datos/reportes, conflicto de revisión, rechazo de datos inválidos, aprobación, Scope 2 y mezcla mensual/anual por ubicación.

## Límites de esta primera implementación

- Conserva el importador y los gráficos JavaScript; no es aún un motor de importación de archivos en el servidor ni un catálogo ESG normalizado en tablas. Los archivos originales no se archivan.
- El reporte Python inicial incluye cuatro áreas y resumen, sin los gráficos ni todas las subsecciones del generador original. «Nueva versión» genera otro reporte completo; no implementa todavía regeneración individual con IA.
- Los indicadores de promedio conservan la media simple del prototipo: faltan denominadores para ponderación ambiental/social correcta.
- Los cambios del documento actual se guardan con el estado. «Guardar / aprobar» actualiza la copia del historial; no hay versionado inmutable de cada edición.
- Los borradores generados conservan los indicadores y revisión de datos como evidencia. Aprobación significa decisión del usuario, no certificación del reporte.
- Integraciones externas desactivadas. Marcos y porcentajes de cobertura del catálogo original son demostrativos.
- Antes de venta: normalizar datos, endurecer validación de unidades/duplicados, recuperar contraseñas, respaldos, pruebas de carga, limitar consumo de IA, tareas durables, cuotas por empresa y revisión de seguridad.

Documentación: https://ai.google.dev/gemini-api/docs/get-started · https://vercel.com/docs/frameworks/backend/fastapi
