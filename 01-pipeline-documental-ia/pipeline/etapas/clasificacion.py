"""Etapa 3: clasificación por el nombre del archivo, con expresiones regulares.

Cada época del repositorio nombró los planos a su manera. Aquí hay un patrón
por convención; el primero que calza entrega proyecto, disciplina, número,
hoja y revisión. Lo que el nombre no dice (por ejemplo el título, o todo, en un
"Plano1.dwg") queda marcado para la lectura del cajetín por IA.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Dict, List, Optional, Tuple

from ..config import Taxonomia

REV = r"(?P<revision>[A-Z]|\d{1,2})"
TITULO = r"(?:\s+(?P<titulo>.+))?"

PATRONES: List[Tuple[str, str, "re.Pattern[str]"]] = [(nombre, clase, re.compile(expresion, re.IGNORECASE)) for nombre, clase, expresion in [
    # PRY-021-ELE-DU-0045-H01-RevB Diagrama unilineal general
    ("moderna", "plano",
     rf"^(?P<proyecto>PRY-\d{{3}})-(?P<disciplina>[A-Z]{{3}})-(?P<tipo>[A-Z]{{2}})-(?P<numero>\d{{4}})-H(?P<hoja>\d{{2}})-Rev{REV}{TITULO}$"),
    # PRY-019-MC-003 Memoria de cálculo estructural RevB
    ("documento", "documento",
     rf"^(?P<proyecto>PRY-\d{{3}})-(?P<tipo>[A-Z]{{2,3}})-(?P<numero>\d{{3}})(?:\s+(?P<titulo>.+?))?\s+Rev{REV}$"),
    # PRY014_EL_0503_1_A Detalles de montaje de tableros
    ("guiones", "plano",
     rf"^PRY(?P<proyecto>\d{{3}})_(?P<disciplina>[A-Z]{{2,4}})_(?P<numero>\d{{3,4}})_(?P<hoja>\d{{1,2}})_{REV}{TITULO}$"),
    # CTX-0501-ELE-R1-L2 Diagrama unilineal general
    ("contratista", "plano",
     rf"^(?P<contratista>[A-Z]{{3}})-(?P<numero>\d{{4}})-(?P<disciplina>[A-Z]{{3}})-R(?P<revision>\d{{1,2}})(?:-L(?P<hoja>\d{{1,2}}))?{TITULO}$"),
    # E-0505-B   o   E-0505-2-B (con número de hoja)
    ("letra", "plano",
     r"^(?P<disciplina>[A-Z])-(?P<numero>\d{3,4})(?:-(?P<hoja>\d{1,2}))?-(?P<revision>[A-Z])$"),
]]

# Marcas que dejan las copias y que no forman parte del nombre original.
PREFIJO_COPIA = re.compile(r"^(?:copia de\s+)+", re.IGNORECASE)
SUFIJO_COPIA = re.compile(r"(?:\s*\(\d+\)|\s*\[\d+\]|\s+-\s+copia|_final)+$", re.IGNORECASE)
PROYECTO_EN_RUTA = re.compile(r"PRY[-_ ]?(\d{3})", re.IGNORECASE)

CATEGORIAS_DE_PLANO = ("cad", "pdf", "imagen")
CATEGORIAS_DE_DOCUMENTO = ("pdf", "ofimatica")


def nombre_limpio(nombre: str, extension: str) -> str:
    """Nombre sin extensión y sin las marcas de copia."""
    base = nombre[: -(len(extension) + 1)] if extension and nombre.lower().endswith("." + extension) else nombre
    return SUFIJO_COPIA.sub("", PREFIJO_COPIA.sub("", base.strip())).strip()


def orden_revision(revision: Optional[str]) -> Optional[int]:
    """Las letras son emisiones previas y los números, emisiones aprobadas: A < B < 0 < 1."""
    if not revision:
        return None
    return 100 + int(revision) if revision.isdigit() else ord(revision.upper()) - 64


def clasificar_nombre(nombre: str, extension: str, ruta: str, categoria: str, taxonomia: Taxonomia) -> Dict[str, object]:
    """Devuelve los campos deducidos del nombre y de la carpeta de un archivo."""
    base = nombre_limpio(nombre, extension)
    campos: Dict[str, object] = {"clase": None, "patron": None, "proyecto": None, "disciplina": None, "tipo_doc": None,
                                 "numero": None, "hoja": None, "revision": None, "titulo": None}
    for patron, clase, expresion in PATRONES:
        permitidas = CATEGORIAS_DE_PLANO if clase == "plano" else CATEGORIAS_DE_DOCUMENTO
        coincidencia = expresion.match(base) if categoria in permitidas else None
        if not coincidencia:
            continue
        grupos = coincidencia.groupdict()
        if clase == "documento" and grupos["tipo"].upper() not in taxonomia.tipos_documento:
            continue
        campos.update(clase=clase, patron=patron)
        campos["numero"] = grupos["numero"].zfill(4 if clase == "plano" else 3)
        campos["revision"] = grupos["revision"].upper()
        campos["titulo"] = (grupos.get("titulo") or "").strip() or None
        if clase == "plano":
            campos["hoja"] = (grupos.get("hoja") or "1").zfill(2)
            campos["disciplina"] = taxonomia.disciplina_de(grupos["disciplina"])
            tipo = (grupos.get("tipo") or "").upper()
            campos["tipo_doc"] = tipo if tipo in taxonomia.tipos_plano else taxonomia.tipo_por_titulo(campos["titulo"])
        else:
            campos["tipo_doc"] = grupos["tipo"].upper()
        proyecto = grupos.get("proyecto")
        if proyecto:
            campos["proyecto"] = proyecto.upper() if proyecto.upper().startswith("PRY-") else f"PRY-{proyecto}"
        break

    if campos["proyecto"] is None:
        en_ruta = PROYECTO_EN_RUTA.search(ruta)
        if en_ruta:
            campos["proyecto"] = f"PRY-{en_ruta.group(1)}"
    if campos["proyecto"] not in taxonomia.proyectos:
        campos["proyecto"] = None
    if campos["clase"] is None and categoria == "cad":
        campos["clase"] = "plano"                       # un archivo CAD es un plano aunque su nombre no diga nada
    return campos


def faltantes(campos: Dict[str, object]) -> List[str]:
    """Campos obligatorios que aún no se conocen."""
    if campos["clase"] == "plano":
        obligatorios = ["proyecto", "disciplina", "numero", "revision"]
    elif campos["clase"] == "documento":
        obligatorios = ["proyecto", "tipo_doc", "numero", "revision"]
    else:
        return ["clase"]
    return [campo for campo in obligatorios if not campos.get(campo)]


def clasificar(conexion: sqlite3.Connection, taxonomia: Taxonomia) -> dict:
    """Clasifica por nombre todos los archivos que todavía no tienen fuente de metadatos."""
    filas = conexion.execute(
        "SELECT id, nombre, extension, ruta, categoria FROM archivo "
        "WHERE fuente IS NULL AND categoria IN ('cad', 'pdf', 'ofimatica', 'imagen')").fetchall()
    cambios, completos, por_patron = [], 0, {}
    for fila in filas:
        campos = clasificar_nombre(fila["nombre"], fila["extension"], fila["ruta"], fila["categoria"], taxonomia)
        falta = faltantes(campos)
        completos += not falta
        if campos["patron"]:
            por_patron[campos["patron"]] = por_patron.get(campos["patron"], 0) + 1
        cambios.append((campos["clase"], campos["patron"], campos["proyecto"], campos["disciplina"], campos["tipo_doc"],
                        campos["numero"], campos["hoja"], campos["revision"], orden_revision(campos["revision"]),
                        campos["titulo"], int(bool(falta)), ("Falta: " + ", ".join(falta)) if falta else None, fila["id"]))
    with conexion:
        conexion.executemany(
            "UPDATE archivo SET clase = ?, patron = ?, proyecto = ?, disciplina = ?, tipo_doc = ?, numero = ?, hoja = ?, "
            "revision = ?, revision_orden = ?, titulo = ?, fuente = 'nombre', requiere_revision = ?, nota_revision = ? "
            "WHERE id = ?", cambios)
    return {"clasificados": len(cambios), "completos": completos, **{f"patron_{k}": v for k, v in sorted(por_patron.items())}}
