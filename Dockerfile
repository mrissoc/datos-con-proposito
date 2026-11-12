# Imagen única para todos los proyectos del portafolio.
#
#   docker build -t datos-con-proposito .
#   docker run --rm datos-con-proposito                 # ejecuta las 129 pruebas
#   docker compose up                                   # levanta las aplicaciones web
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Las dependencias van primero: si solo cambia el código, Docker reutiliza esta capa.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Las aplicaciones no necesitan privilegios de administrador.
RUN useradd --create-home --uid 1000 portafolio && chown -R portafolio /app
USER portafolio

CMD ["pytest"]
