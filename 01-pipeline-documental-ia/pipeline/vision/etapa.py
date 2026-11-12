"""Etapa de visión: completa con IA los planos que el nombre del archivo no alcanzó a describir.

Solo se leen los archivos canónicos (nunca las copias) a los que les falta algún
campo, y cada lectura queda guardada: una segunda ejecución no vuelve a pagar
por lo ya leído.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Dict, List, Optional, Tuple

import anthropic

from ..almacenes import Almacen
from ..bd import ahora
from ..config import Taxonomia
from ..etapas.clasificacion import orden_revision
from ..etapas.deduplicacion import marcar_vigentes
from .dibujo import dxf_a_png
from .extractor import ErrorDeConfiguracion, Lectura, LecturaRechazada, LectorCajetines

log = logging.getLogger(__name__)

PENDIENTES = """
SELECT id, origen_id, patron, proyecto, disciplina, tipo_doc, numero, hoja, revision, titulo
FROM archivo
WHERE clase = 'plano' AND duplicado_de IS NULL AND extension_real = 'dxf'
  AND (proyecto IS NULL OR disciplina IS NULL OR tipo_doc IS NULL OR numero IS NULL OR revision IS NULL OR titulo IS NULL)
  AND id NOT IN (SELECT archivo_id FROM lectura_ia WHERE estado IN ('ok', 'rechazada'))
ORDER BY (numero IS NULL) DESC, id
LIMIT ?
"""

# Campo de la base -> campo de la lectura. En los primeros, una diferencia entre
# el nombre del archivo y el cajetín es una contradicción que debe ver una persona.
IDENTIDAD = [("proyecto", "proyecto"), ("disciplina", "disciplina"), ("numero", "numero"), ("hoja", "hoja"),
             ("revision", "revision")]
DESCRIPTIVOS = [("tipo_doc", "tipo_plano"), ("titulo", "titulo")]
OBLIGATORIOS = ["proyecto", "disciplina", "numero", "revision"]


def combinar(fila: sqlite3.Row, lectura: Lectura) -> Tuple[Dict[str, Optional[str]], List[str]]:
    """Une lo que decía el nombre con lo leído en el cajetín.

    El nombre manda donde ya había un dato; el cajetín completa lo que faltaba.
    Devuelve los valores finales y la lista de contradicciones.
    """
    valores: Dict[str, Optional[str]] = {}
    contradicciones: List[str] = []
    for columna, campo in IDENTIDAD + DESCRIPTIVOS:
        actual, leido = fila[columna], getattr(lectura, campo)
        valores[columna] = actual if actual is not None else leido
        # Sin número de hoja en el nombre se asume la hoja 1: eso no es un dato que contradecir.
        if (columna, campo) in IDENTIDAD and actual is not None and leido is not None and actual != leido \
                and not (columna == "hoja" and fila["patron"] is None):
            contradicciones.append(f"{columna}: el nombre dice {actual} y el cajetín dice {leido}")
    if fila["patron"] is None and lectura.hoja:
        valores["hoja"] = lectura.hoja
    valores["hoja"] = valores["hoja"] or "01"
    return valores, contradicciones


def leer_cajetines(conexion: sqlite3.Connection, almacen: Almacen, taxonomia: Taxonomia, maximo: int = 20,
                   modelo: Optional[str] = None, esfuerzo: str = "low",
                   lector: Optional[LectorCajetines] = None) -> dict:
    """Lee hasta `maximo` cajetines pendientes y actualiza la base."""
    filas = conexion.execute(PENDIENTES, (maximo,)).fetchall()
    if not filas:
        return {"leidos": 0, "pendientes": 0}
    lector = lector or LectorCajetines(taxonomia, modelo=modelo, esfuerzo=esfuerzo)
    cuenta = {"leidos": 0, "completos": 0, "con_contradiccion": 0, "rechazados": 0, "errores": 0,
              "tokens_entrada": 0, "tokens_salida": 0}
    seguidos = 0
    for fila in filas:
        try:
            lectura = lector.leer_plano(dxf_a_png(almacen.leer(fila["origen_id"])))
        except ErrorDeConfiguracion:
            raise
        except LecturaRechazada as rechazo:
            _registrar(conexion, fila["id"], "rechazada", detalle=str(rechazo))
            cuenta["rechazados"] += 1
            continue
        except (anthropic.APIError, ValueError, OSError) as error:
            # Límite de solicitudes, falla del servicio o un archivo que no se pudo dibujar.
            log.warning("No se pudo leer %s: %s", fila["origen_id"], error)
            _registrar(conexion, fila["id"], "error", detalle=f"{type(error).__name__}: {error}")
            cuenta["errores"] += 1
            seguidos += 1
            if seguidos >= 3:
                log.error("Tres errores seguidos: se detiene la etapa. Lo ya leído queda guardado.")
                break
            continue
        seguidos = 0
        valores, contradicciones = combinar(fila, lectura)
        faltan = [campo for campo in OBLIGATORIOS if not valores[campo]]
        notas = contradicciones + ([f"Falta: {', '.join(faltan)}"] if faltan else [])
        if lectura.observaciones:
            notas.append(f"IA: {lectura.observaciones}")
        with conexion:
            conexion.execute(
                "UPDATE archivo SET proyecto = ?, disciplina = ?, tipo_doc = ?, numero = ?, hoja = ?, revision = ?, "
                "revision_orden = ?, titulo = ?, fuente = ?, requiere_revision = ?, nota_revision = ? WHERE id = ?",
                (valores["proyecto"], valores["disciplina"], valores["tipo_doc"], valores["numero"], valores["hoja"],
                 valores["revision"], orden_revision(valores["revision"]), valores["titulo"],
                 "nombre+ia" if fila["patron"] else "ia", int(bool(contradicciones or faltan)),
                 "; ".join(notas) or None, fila["id"]))
        _registrar(conexion, fila["id"], "ok", lectura)
        cuenta["leidos"] += 1
        cuenta["completos"] += not (contradicciones or faltan)
        cuenta["con_contradiccion"] += bool(contradicciones)
        cuenta["tokens_entrada"] += lectura.tokens_entrada
        cuenta["tokens_salida"] += lectura.tokens_salida
    marcar_vigentes(conexion)
    cuenta["pendientes"] = conexion.execute(PENDIENTES, (-1,)).fetchall().__len__()
    return cuenta


def _registrar(conexion: sqlite3.Connection, archivo_id: int, estado: str, lectura: Optional[Lectura] = None,
               detalle: Optional[str] = None) -> None:
    with conexion:
        conexion.execute(
            "INSERT OR REPLACE INTO lectura_ia (archivo_id, estado, modelo, pasadas, respuesta, tokens_entrada, "
            "tokens_salida, detalle, leido_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (archivo_id, estado, lectura.modelo if lectura else None, lectura.pasadas if lectura else 1,
             json.dumps(lectura.respuestas, ensure_ascii=False) if lectura else None,
             lectura.tokens_entrada if lectura else 0, lectura.tokens_salida if lectura else 0, detalle, ahora()))
