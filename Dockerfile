# Imagen local de demostración. La autenticación de la web está pendiente.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/taxiroute

RUN useradd --create-home --uid 10001 taxiroute \
    && mkdir -p /home/taxiroute/.taxiroute /app/configuracion \
    && chown -R 10001:10001 /home/taxiroute /app

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY --chown=10001:10001 taxiroute/ ./taxiroute/
COPY --chown=10001:10001 configuracion/tarifas.json ./configuracion/tarifas.json

USER 10001:10001
EXPOSE 8765
# El puerto publicado por Compose se limita a 127.0.0.1 del ordenador.
# Flask escucha en la red del contenedor para que ese puerto sea accesible.
CMD ["flask", "--app", "taxiroute.web:crear_app", "run", "--host=0.0.0.0", "--port=8765", "--no-reload"]
