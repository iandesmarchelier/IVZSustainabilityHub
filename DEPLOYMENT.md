# Publicación del sistema

El usuario indicó que la aplicación se utiliza en https://ivz-sustainability-hub.vercel.app.
Cada cambio solicitado debe publicarse en el proyecto Vercel conectado al repositorio
https://github.com/iandesmarchelier/IVZSustainabilityHub, después de verificarlo.
La validación local no equivale a entrega: comprobar la URL pública tras la publicación.

No incluir data/, bases SQLite, contraseñas, .env ni claves en Git.
Mantener los datos y las cuentas existentes del entorno publicado.
La base publicada usa DATABASE_URL; SQLite sólo corresponde al entorno local.

## Contenedor (Azure, SAP BTP u otro hosting con Docker)

El `Dockerfile` arma la misma aplicación como imagen Docker. Vercel lo ignora, así que ambos despliegues conviven.

- Variables: `DATABASE_URL` (obligatoria), `ADMIN_PASSWORD_HASH`, `GEMINI_API_KEY` y `GEMINI_MODEL` si se usan, y `CARBON_API_BASE` con la dirección de IVZ Carbon si deja de estar en https://ivzcarbon.vercel.app.
- La imagen trae `HUB_ENV=production`: exige PostgreSQL (nunca SQLite dentro del contenedor, que se perdería al reiniciar) y marca las cookies como Secure, igual que en Vercel.
- Puerto: 8000, o el que indique la plataforma en `PORT` (Cloud Foundry, App Service).
- Prueba local con Docker Desktop: crear `.env` con `HUB_DB_PASSWORD` y correr `docker compose up --build` (http://localhost:8000).
- `.github/workflows/contenedor.yml` arma la imagen en cada push, corre todas las pruebas dentro de ella y comprueba `/health`.
