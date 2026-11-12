"""Fixtures: el simulador de SAP corriendo en un puerto libre y una base de averías."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest
import uvicorn

from averias.construir_bd import construir
from integracion_sap.cliente import ClienteSap
from integracion_sap.simulador import RUTA_SERVICIO, crear_simulador


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
        puerto = self.servidor.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{puerto}"
        return self

    def __exit__(self, *_):
        self.servidor.should_exit = True
        self.hilo.join(timeout=5)


@pytest.fixture
def simulador():
    """Simulador con páginas de 10 filas, para forzar la paginación del servidor."""
    with ServidorDePrueba(crear_simulador(pagina_maxima=10)) as servidor:
        yield servidor


@pytest.fixture
def sap(simulador):
    return ClienteSap(simulador.url + RUTA_SERVICIO, "demo", "demo", espera=lambda segundos: None)


@pytest.fixture
def conexion(tmp_path):
    """Base de averías con tres averías abiertas y un historial breve."""
    ruta = construir(tmp_path / "averias.db", averias=6)
    conexion = sqlite3.connect(str(ruta))
    conexion.row_factory = sqlite3.Row
    yield conexion
    conexion.close()
