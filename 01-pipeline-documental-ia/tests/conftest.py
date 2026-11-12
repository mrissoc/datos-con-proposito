"""Fixtures: taxonomía, un repositorio sintético pequeño y una API de Anthropic simulada."""

from __future__ import annotations

import json
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pipeline.config import Reglas, Taxonomia
from pipeline.sintetico.repositorio import Generador, materializar


@pytest.fixture(scope="session")
def taxonomia():
    return Taxonomia.cargar()


@pytest.fixture(scope="session")
def reglas():
    return Reglas.cargar()


@pytest.fixture(scope="session")
def repositorio(tmp_path_factory, taxonomia):
    """Repositorio sintético chico, escrito una sola vez para toda la sesión de pruebas."""
    carpeta = tmp_path_factory.mktemp("repo")
    archivos = list(Generador(taxonomia, semilla=3).archivos(planos=60, documentos=30, raros=45))
    verdad = materializar(archivos, carpeta / "origen")
    return {"carpeta": carpeta / "origen", "verdad": verdad, "archivos": archivos}


class ServidorDePrueba:
    """Corre una aplicación ASGI en un hilo, en un puerto elegido por el sistema."""

    def __init__(self, app):
        self.app = app
        self.servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        self.hilo = threading.Thread(target=self.servidor.run, daemon=True)

    def __enter__(self):
        self.hilo.start()
        limite = time.time() + 10
        while not self.servidor.started:
            if time.time() > limite:
                raise RuntimeError("El servidor de prueba no partió")
            time.sleep(0.01)
        self.url = f"http://127.0.0.1:{self.servidor.servers[0].sockets[0].getsockname()[1]}"
        return self

    def __exit__(self, *_):
        self.servidor.should_exit = True
        self.hilo.join(timeout=5)


def crear_api_simulada() -> FastAPI:
    """Imita el endpoint de mensajes de la API de Anthropic.

    Las pruebas dejan en `app.state.respuestas` lo que debe contestar cada llamada
    y leen en `app.state.solicitudes` lo que el SDK envió realmente.
    """
    app = FastAPI()
    app.state.respuestas = []
    app.state.solicitudes = []

    @app.post("/v1/messages")
    async def mensajes(request: Request):
        cuerpo = await request.json()
        app.state.solicitudes.append({"cuerpo": cuerpo, "cabeceras": dict(request.headers)})
        siguiente = app.state.respuestas.pop(0)
        if "estado" in siguiente:
            return JSONResponse({"type": "error", "error": {"type": siguiente["tipo"], "message": siguiente["mensaje"]}},
                                status_code=siguiente["estado"])
        contenido = [{"type": "thinking", "thinking": "", "signature": "firma-de-prueba"}]
        if "datos" in siguiente:
            contenido.append({"type": "text", "text": json.dumps(siguiente["datos"], ensure_ascii=False)})
        return {"id": "msg_prueba", "type": "message", "role": "assistant", "model": cuerpo["model"],
                "content": contenido, "stop_reason": siguiente.get("stop_reason", "end_turn"), "stop_sequence": None,
                "usage": {"input_tokens": 1800, "output_tokens": 150, "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0}}

    return app


@pytest.fixture
def api_simulada():
    with ServidorDePrueba(crear_api_simulada()) as servidor:
        yield servidor
