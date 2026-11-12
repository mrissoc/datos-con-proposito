"""Base de ejemplo: dotación con su organigrama, cursos obligatorios y avance de cada persona.

En un caso real estas tablas se cargan desde los sistemas de origen (el organigrama
desde el sistema de recursos humanos, el avance desde la plataforma de cursos).
Aquí se generan con datos ficticios.
"""

from __future__ import annotations

import random
import sqlite3
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Union

ESQUEMA = """
CREATE TABLE IF NOT EXISTS empleado (
    id       INTEGER PRIMARY KEY,
    nombre   TEXT NOT NULL,
    correo   TEXT NOT NULL UNIQUE,
    cargo    TEXT NOT NULL,
    gerencia TEXT NOT NULL,
    pais     TEXT NOT NULL,
    jefe_id  INTEGER REFERENCES empleado (id)
);
CREATE TABLE IF NOT EXISTS curso (
    id     INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    horas  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS asignacion (
    empleado_id   INTEGER NOT NULL REFERENCES empleado (id),
    curso_id      INTEGER NOT NULL REFERENCES curso (id),
    fecha_limite  TEXT NOT NULL,
    completado_en TEXT,
    PRIMARY KEY (empleado_id, curso_id)
);
-- Registro de envíos: evita mandar dos veces el mismo recordatorio y permite retomar.
CREATE TABLE IF NOT EXISTS envio (
    campania    TEXT NOT NULL,
    empleado_id INTEGER NOT NULL REFERENCES empleado (id),
    estado      TEXT NOT NULL,              -- enviado | error
    detalle     TEXT,
    registrado  TEXT NOT NULL,
    PRIMARY KEY (campania, empleado_id)
);
-- Una foto diaria de los indicadores: el origen solo muestra el estado de hoy,
-- y la evolución en el tiempo se construye guardando cada día.
CREATE TABLE IF NOT EXISTS kpi_diario (
    fecha       TEXT NOT NULL,
    pais        TEXT NOT NULL,
    gerencia    TEXT NOT NULL,
    asignados   INTEGER NOT NULL,
    completados INTEGER NOT NULL,
    vencidos    INTEGER NOT NULL,
    PRIMARY KEY (fecha, pais, gerencia)
);
"""

NOMBRES = ["Camila", "Diego", "Valentina", "Sebastián", "Fernanda", "Ignacio", "Daniela", "Tomás", "Javiera", "Felipe",
           "Carolina", "Rodrigo", "Paula", "Andrés", "Constanza", "Cristóbal", "Francisca", "Gonzalo", "Marcela", "Pablo"]
APELLIDOS = ["Rojas", "Soto", "Contreras", "Silva", "Morales", "Fuentes", "Araya", "Espinoza", "Castillo", "Tapia",
             "Reyes", "Vargas", "Sepúlveda", "Navarro", "Cárdenas", "Pizarro", "Molina", "Bravo", "Riquelme", "Vera"]
GERENCIAS = ["Operaciones", "Finanzas", "Personas", "Comercial", "Tecnología"]
CURSOS = [("Código de ética y conducta", 2), ("Prevención de delitos", 1.5), ("Protección de datos personales", 1),
          ("Seguridad de la información", 1.5), ("Libre competencia", 1), ("Prevención del acoso laboral", 2),
          ("Conflictos de interés", 1), ("Seguridad y salud en el trabajo", 3)]
DOTACION = {"Chile": 70, "México": 28, "Argentina": 22}


def _correo(nombre: str, usados: set) -> str:
    base = "".join(c for c in unicodedata.normalize("NFD", nombre.lower()) if unicodedata.category(c) != "Mn")
    base = base.replace(" ", ".")
    correo, numero = f"{base}@ejemplo.com", 1
    while correo in usados:
        numero += 1
        correo = f"{base}{numero}@ejemplo.com"
    usados.add(correo)
    return correo


def conectar(ruta: Union[Path, str]) -> sqlite3.Connection:
    conexion = sqlite3.connect(str(ruta))
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    conexion.executescript(ESQUEMA)
    return conexion


def crear_ejemplo(ruta: Union[Path, str], hoy: date, semilla: int = 5) -> sqlite3.Connection:
    """Crea la base con tres países: en cada uno un gerente general, gerentes de área, jefaturas y equipos."""
    ruta = Path(ruta)
    if ruta.exists():
        ruta.unlink()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conexion = conectar(ruta)
    rng = random.Random(semilla)
    usados: set = set()

    def persona(cargo: str, gerencia: str, pais: str, jefe) -> int:
        nombre = f"{rng.choice(NOMBRES)} {rng.choice(APELLIDOS)}"
        return conexion.execute(
            "INSERT INTO empleado (nombre, correo, cargo, gerencia, pais, jefe_id) VALUES (?, ?, ?, ?, ?, ?)",
            (nombre, _correo(nombre, usados), cargo, gerencia, pais, jefe)).lastrowid

    with conexion:
        conexion.executemany("INSERT INTO curso (nombre, horas) VALUES (?, ?)", CURSOS)
        for pais, dotacion in DOTACION.items():
            general = persona("Gerente general", "Gerencia general", pais, None)
            restantes = dotacion - 1
            for gerencia in GERENCIAS:
                gerente = persona(f"Gerente de {gerencia}", gerencia, pais, general)
                equipo = max(2, (restantes // len(GERENCIAS)) - 1)
                jefe = gerente
                for indice in range(equipo):
                    if indice % 5 == 0:
                        jefe = persona(f"Jefe de área de {gerencia}", gerencia, pais, gerente)
                    else:
                        persona("Analista", gerencia, pais, jefe)
        empleados = [fila["id"] for fila in conexion.execute("SELECT id FROM empleado")]
        for empleado in empleados:
            for curso in range(1, len(CURSOS) + 1):
                if rng.random() < 0.2:
                    continue                                    # no todos los cursos aplican a todos
                limite = hoy + timedelta(days=rng.randint(-45, 60))
                # Un curso completado tiene fecha de término en el pasado, nunca después de hoy.
                termino = min(limite - timedelta(days=rng.randint(1, 40)), hoy - timedelta(days=rng.randint(0, 20)))
                completado = termino.isoformat() if rng.random() < 0.62 else None
                conexion.execute(
                    "INSERT INTO asignacion (empleado_id, curso_id, fecha_limite, completado_en) VALUES (?, ?, ?, ?)",
                    (empleado, curso, limite.isoformat(), completado))
    return conexion


def avanzar_dia(conexion: sqlite3.Connection, hoy: date, semilla: int) -> int:
    """Simula un día de avance: algunas personas completan cursos pendientes."""
    rng = random.Random(semilla)
    pendientes = conexion.execute(
        "SELECT empleado_id, curso_id FROM asignacion WHERE completado_en IS NULL").fetchall()
    completan = [fila for fila in pendientes if rng.random() < 0.035]
    with conexion:
        conexion.executemany(
            "UPDATE asignacion SET completado_en = ? WHERE empleado_id = ? AND curso_id = ?",
            [(hoy.isoformat(), fila["empleado_id"], fila["curso_id"]) for fila in completan])
    return len(completan)
