"""Base de staging: una fila por archivo, que cada etapa va completando.

Las etapas no se pasan datos en memoria: leen y escriben aquí. Por eso cada una
se puede ejecutar por separado, repetir sin duplicar y retomar si se interrumpe.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Union

ESQUEMA = """
CREATE TABLE IF NOT EXISTS archivo (
    id                INTEGER PRIMARY KEY,
    -- Inventario
    origen_id         TEXT    NOT NULL UNIQUE,   -- identificador en el almacén de origen
    ruta              TEXT    NOT NULL,          -- carpeta relativa, separada por '/'
    nombre            TEXT    NOT NULL,
    extension         TEXT    NOT NULL,          -- como viene, en minúsculas ('' si no tiene)
    tamano            INTEGER NOT NULL,
    modificado        TEXT,
    hash              TEXT,
    en_respaldo       INTEGER NOT NULL DEFAULT 0,  -- la carpeta parece una copia o respaldo
    -- Perfilado
    extension_real    TEXT,                      -- normalizada o deducida de la firma del archivo
    tipo_por_firma    INTEGER NOT NULL DEFAULT 0,
    categoria         TEXT,                      -- cad, pdf, ofimatica, imagen, comprimido, temporal, sistema, otro
    -- Clasificación
    clase             TEXT,                      -- plano | documento
    patron            TEXT,                      -- convención de nombre que calzó
    proyecto          TEXT,
    disciplina        TEXT,
    tipo_doc          TEXT,
    numero            TEXT,
    hoja              TEXT,
    revision          TEXT,
    revision_orden    INTEGER,                   -- para comparar revisiones (A < B < 0 < 1)
    titulo            TEXT,
    fuente            TEXT,                      -- nombre | ia | nombre+ia
    requiere_revision INTEGER NOT NULL DEFAULT 0,
    nota_revision     TEXT,
    -- Deduplicación y versiones
    duplicado_de      INTEGER REFERENCES archivo (id),
    vigente           INTEGER,                   -- 1 si es la última revisión de su plano
    -- Migración
    destino           TEXT,
    estado_migracion  TEXT,                      -- pendiente | migrado | error | conflicto
    intentos          INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    migrado_en        TEXT
);

CREATE INDEX IF NOT EXISTS ix_archivo_hash      ON archivo (hash);
CREATE INDEX IF NOT EXISTS ix_archivo_plano     ON archivo (clase, proyecto, disciplina, numero, hoja);
CREATE INDEX IF NOT EXISTS ix_archivo_migracion ON archivo (estado_migracion);
CREATE INDEX IF NOT EXISTS ix_archivo_copia     ON archivo (duplicado_de);
CREATE INDEX IF NOT EXISTS ix_archivo_destino   ON archivo (destino);

-- Respuesta de la lectura por IA de cada plano, para auditar y no pagar dos veces.
CREATE TABLE IF NOT EXISTS lectura_ia (
    archivo_id     INTEGER PRIMARY KEY REFERENCES archivo (id),
    estado         TEXT    NOT NULL,             -- ok | rechazada | error
    modelo         TEXT,
    pasadas        INTEGER NOT NULL DEFAULT 1,
    respuesta      TEXT,                         -- JSON devuelto por el modelo
    tokens_entrada INTEGER NOT NULL DEFAULT 0,
    tokens_salida  INTEGER NOT NULL DEFAULT 0,
    detalle        TEXT,
    leido_en       TEXT
);

CREATE TABLE IF NOT EXISTS ejecucion (
    id       INTEGER PRIMARY KEY,
    etapa    TEXT NOT NULL,
    inicio   TEXT NOT NULL,
    segundos REAL NOT NULL,
    detalle  TEXT
);
"""


def ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def conectar(ruta: Union[Path, str]) -> sqlite3.Connection:
    """Abre (y crea si hace falta) la base de staging."""
    if str(ruta) != ":memory:":
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(str(ruta))
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    conexion.executescript(ESQUEMA)
    return conexion


@contextmanager
def etapa(conexion: sqlite3.Connection, nombre: str) -> Iterator[dict]:
    """Mide una etapa y deja registro de su resultado en la tabla `ejecucion`."""
    inicio, reloj = ahora(), time.perf_counter()
    resultado: dict = {}
    yield resultado
    segundos = time.perf_counter() - reloj
    detalle = ", ".join(f"{clave}={valor}" for clave, valor in resultado.items())
    with conexion:
        conexion.execute("INSERT INTO ejecucion (etapa, inicio, segundos, detalle) VALUES (?, ?, ?, ?)",
                         (nombre, inicio, round(segundos, 3), detalle))
    resultado["segundos"] = round(segundos, 2)
