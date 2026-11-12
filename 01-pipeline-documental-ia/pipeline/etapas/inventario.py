"""Etapas 1 y 2: inventario del origen y perfilado de tipos de archivo."""

from __future__ import annotations

import re
import sqlite3

from ..almacenes import Almacen
from ..config import Reglas

LOTE = 5000
CARPETA_DE_RESPALDO = re.compile(r"respaldo|backup|copia|\bold\b|antiguo|obsoleto|escritorio", re.IGNORECASE)

INSERTAR = """
INSERT INTO archivo (origen_id, ruta, nombre, extension, tamano, modificado, hash, en_respaldo)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (origen_id) DO UPDATE SET
    ruta = excluded.ruta, nombre = excluded.nombre, extension = excluded.extension,
    tamano = excluded.tamano, modificado = excluded.modificado, hash = excluded.hash,
    en_respaldo = excluded.en_respaldo
"""


def extension_de(nombre: str) -> str:
    """Lo que sigue al último punto, en minúsculas. No siempre es una extensión real."""
    base, punto, extension = nombre.rpartition(".")
    return extension.lower() if punto and base else ""


def inventariar(conexion: sqlite3.Connection, almacen: Almacen) -> int:
    """Registra cada archivo del origen. Repetirla actualiza, no duplica."""
    total, lote = 0, []
    for archivo in almacen.listar():
        lote.append((archivo.origen_id, archivo.ruta, archivo.nombre, extension_de(archivo.nombre), archivo.tamano,
                     archivo.modificado, archivo.hash, int(bool(CARPETA_DE_RESPALDO.search(archivo.ruta)))))
        if len(lote) >= LOTE:
            with conexion:
                conexion.executemany(INSERTAR, lote)
            total += len(lote)
            lote = []
    with conexion:
        conexion.executemany(INSERTAR, lote)
    return total + len(lote)


def perfilar(conexion: sqlite3.Connection, almacen: Almacen, reglas: Reglas) -> dict:
    """Asigna a cada archivo su tipo real y su categoría.

    Las extensiones conocidas se aceptan tal cual. Las desconocidas o ausentes
    (fechas escritas con puntos, errores de tipeo, archivos sin extensión) se
    resuelven leyendo los primeros bytes del archivo.
    """
    pendientes = conexion.execute(
        "SELECT id, origen_id, nombre, extension FROM archivo WHERE categoria IS NULL").fetchall()
    cambios, por_firma = [], 0
    for fila in pendientes:
        extension = reglas.normalizar(fila["extension"])
        firma = 0
        if fila["nombre"].startswith("~$"):
            categoria = "temporal"                    # archivo de bloqueo de Office
        elif extension in reglas.categoria_de:
            categoria = reglas.categoria(extension)
        else:
            tipo = reglas.tipo_por_firma(almacen.leer_inicio(fila["origen_id"], 32))
            if tipo:
                extension, firma, por_firma = tipo, 1, por_firma + 1
            categoria = reglas.categoria(extension)
        cambios.append((extension, firma, categoria, fila["id"]))
    with conexion:
        conexion.executemany(
            "UPDATE archivo SET extension_real = ?, tipo_por_firma = ?, categoria = ? WHERE id = ?", cambios)
    return {"perfilados": len(cambios), "reconocidos_por_firma": por_firma}
