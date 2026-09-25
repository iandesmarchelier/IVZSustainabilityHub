# IVZ Sustainability Hub en un contenedor.
# La misma imagen sirve para Azure (Container Apps / App Service) y SAP BTP (Cloud Foundry / Kyma).
FROM python:3.12-slim

# HUB_ENV=production exige DATABASE_URL (nunca SQLite dentro del contenedor) y cookies Secure.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HUB_ENV=production

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY index.html admin.html bridge.js reports-ui.js carbon-integration-ui.js users-ui.js globe.js map-ui.js world.js ./

RUN useradd --create-home hub
USER hub

# Cloud Foundry (BTP) y App Service indican el puerto en PORT; el resto usa 8000.
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
