"""Etapa 5: migración a la taxonomía documental.

Primero se planifica: cada archivo canónico y bien identificado recibe su
ubicación y su nombre normalizado. Después se ejecuta, archivo por archivo, con
un estado propio para cada uno. Si el proceso se interrumpe o el servicio de
destino limita las solicitudes, basta volver a ejecutarlo: retoma lo pendiente.
"""

from __future__ import annotations

import random
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional, Tuple

from ..almacenes import Almacen, ErrorTransitorio
from ..bd import ahora
from ..config import Reglas, Taxonomia

CANDIDATOS = """
SELECT id, clase, categoria, extension_real, proyecto, disciplina, tipo_doc, numero, hoja, revision
FROM archivo
WHERE duplicado_de IS NULL AND clase IS NOT NULL AND requiere_revision = 0
  AND COALESCE(estado_migracion, 'pendiente') <> 'migrado'
"""


def ruta_destino(fila: sqlite3.Row, taxonomia: Taxonomia) -> str:
    """Ubicación y nombre normalizado de un archivo dentro de la biblioteca."""
    proyecto, extension = fila["proyecto"], fila["extension_real"]
    if fila["clase"] == "plano":
        disciplina = fila["disciplina"]
        tipo = fila["tipo_doc"]
        carpeta_tipo = f"{tipo} {taxonomia.tipos_plano[tipo]}" if tipo else "Sin tipo"
        nombre = f"{proyecto}-{disciplina}-{fila['numero']}-H{fila['hoja']}-Rev{fila['revision']}.{extension}"
        return f"Planos/{disciplina} {taxonomia.disciplinas[disciplina]}/{carpeta_tipo}/{proyecto}/{nombre}"
    tipo = fila["tipo_doc"]
    nombre = f"{proyecto}-{tipo}-{fila['numero']}-Rev{fila['revision']}.{extension}"
    return f"Documentos/{tipo} {taxonomia.tipos_documento[tipo]}/{proyecto}/{nombre}"


def planificar(conexion: sqlite3.Connection, taxonomia: Taxonomia, reglas: Reglas) -> dict:
    """Asigna destino a lo que se puede migrar y aparta los conflictos."""
    with conexion:
        # Los conflictos de una planificación anterior se vuelven a evaluar.
        conexion.execute("UPDATE archivo SET requiere_revision = 0, nota_revision = NULL, estado_migracion = NULL "
                         "WHERE estado_migracion = 'conflicto'")
    planes = [(ruta_destino(fila, taxonomia), fila["id"])
              for fila in conexion.execute(CANDIDATOS)
              if reglas.alcance.get(fila["categoria"]) == "migrar"]
    with conexion:
        conexion.execute("UPDATE archivo SET destino = NULL, estado_migracion = NULL "
                         "WHERE COALESCE(estado_migracion, '') <> 'migrado'")
        conexion.executemany("UPDATE archivo SET destino = ?, estado_migracion = 'pendiente' WHERE id = ?", planes)
        # Dos contenidos distintos que reclaman el mismo plano y la misma revisión:
        # alguien editó sin emitir una revisión nueva. Decide una persona.
        conexion.execute(
            "UPDATE archivo SET estado_migracion = 'conflicto', requiere_revision = 1, "
            "nota_revision = 'Mismo plano y revisión con contenido distinto' "
            "WHERE estado_migracion = 'pendiente' AND destino IN ("
            "    SELECT destino FROM archivo WHERE destino IS NOT NULL GROUP BY destino HAVING COUNT(*) > 1)")
    return dict(conexion.execute(
        "SELECT estado_migracion, COUNT(*) FROM archivo WHERE estado_migracion IS NOT NULL GROUP BY estado_migracion"))


def _metadatos(fila: dict) -> Dict[str, str]:
    campos = {"Proyecto": fila["proyecto"], "Disciplina": fila["disciplina"], "Tipo": fila["tipo_doc"],
              "Numero": fila["numero"], "Hoja": fila["hoja"], "Revision": fila["revision"], "Titulo": fila["titulo"],
              "Vigente": "Sí" if fila["vigente"] else "No", "FuenteMetadatos": fila["fuente"],
              "RutaOriginal": fila["origen_id"], "Huella": fila["hash"]}
    return {clave: valor for clave, valor in campos.items() if valor is not None}


def _migrar_uno(almacen: Almacen, fila: dict, max_intentos: int,
                espera: Callable[[float], None]) -> Tuple[int, bool, int, Optional[str]]:
    """Reubica un archivo, reintentando ante errores transitorios con espera creciente."""
    error = None
    for intento in range(1, max_intentos + 1):
        try:
            almacen.reubicar(fila["origen_id"], fila["destino"], _metadatos(fila))
            return fila["id"], True, intento, None
        except ErrorTransitorio as transitorio:
            error = str(transitorio)
            if intento < max_intentos:
                sugerida = transitorio.esperar
                espera(sugerida if sugerida is not None else min(2 ** intento, 60) + random.uniform(0, 1))
        except Exception as definitivo:                  # un archivo con problemas no detiene al resto
            return fila["id"], False, intento, f"{type(definitivo).__name__}: {definitivo}"
    return fila["id"], False, max_intentos, error


def migrar(conexion: sqlite3.Connection, almacen: Almacen, hilos: int = 4, max_intentos: int = 5,
           espera: Callable[[float], None] = time.sleep, limite: Optional[int] = None) -> dict:
    """Ejecuta lo planificado. Los archivos con error quedan para la próxima ejecución."""
    filas = conexion.execute(
        "SELECT id, origen_id, destino, proyecto, disciplina, tipo_doc, numero, hoja, revision, titulo, vigente, "
        "fuente, hash FROM archivo WHERE estado_migracion IN ('pendiente', 'error') ORDER BY id"
        + (f" LIMIT {int(limite)}" if limite else "")).fetchall()
    filas = [dict(fila) for fila in filas]               # copias simples, seguras de leer desde otros hilos
    migrados = errores = reintentos = 0
    with ThreadPoolExecutor(max_workers=hilos) as ejecutor:
        resultados = ejecutor.map(lambda fila: _migrar_uno(almacen, fila, max_intentos, espera), filas)
        # La base se escribe solo desde este hilo, a medida que llegan los resultados,
        # y se confirma cada 200 archivos: una interrupción pierde a lo más ese tramo.
        for cantidad, (archivo_id, exito, intentos, error) in enumerate(resultados, start=1):
            reintentos += intentos - 1
            conexion.execute(
                "UPDATE archivo SET estado_migracion = ?, intentos = intentos + ?, error = ?, migrado_en = ? "
                "WHERE id = ?",
                ("migrado" if exito else "error", intentos, error, ahora() if exito else None, archivo_id))
            if cantidad % 200 == 0:
                conexion.commit()
            migrados += exito
            errores += not exito
        conexion.commit()
    return {"migrados": migrados, "errores": errores, "reintentos": reintentos}
