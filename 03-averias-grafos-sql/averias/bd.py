"""Acceso a la base de datos.

Toda la lógica vive en SQL: cada operación es un archivo de `sql/consultas/`.
Este módulo solo abre la conexión y ejecuta esas consultas por nombre, con
parámetros con nombre (`:parametro`). La aplicación web usa los mismos archivos
cuando corre sin servidor, por lo que hay una única versión de cada consulta.
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
RUTA_ESQUEMA = RAIZ / "sql" / "01_esquema.sql"
RUTA_CONSULTAS = RAIZ / "sql" / "consultas"


def conectar(ruta: Path | str, entre_hilos: bool = False) -> sqlite3.Connection:
    """Abre la base con filas accesibles por nombre y llaves foráneas activas.

    `entre_hilos` permite usar la conexión desde un hilo distinto al que la creó.
    La API lo activa porque abre una conexión por solicitud y el servidor puede
    atender las distintas etapas de una misma solicitud en hilos diferentes.
    """
    conexion = sqlite3.connect(str(ruta), check_same_thread=not entre_hilos)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    return conexion


@lru_cache(maxsize=None)
def sql(nombre: str) -> str:
    """Devuelve el texto de una consulta con nombre."""
    ruta = RUTA_CONSULTAS / f"{nombre}.sql"
    if not ruta.is_file():
        raise KeyError(f"No existe la consulta '{nombre}'")
    return ruta.read_text(encoding="utf-8")


def consultar(conexion: sqlite3.Connection, nombre: str, **parametros: Any) -> list[dict]:
    """Ejecuta una consulta de lectura y devuelve las filas como diccionarios."""
    return [dict(fila) for fila in conexion.execute(sql(nombre), parametros)]


def ejecutar(conexion: sqlite3.Connection, nombre: str, **parametros: Any) -> sqlite3.Cursor:
    """Ejecuta una consulta de escritura. La transacción la controla quien llama."""
    return conexion.execute(sql(nombre), parametros)
