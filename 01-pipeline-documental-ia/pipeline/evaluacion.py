"""Mide cuánto acertó el pipeline, comparando la base de staging con la verdad del repositorio sintético."""

from __future__ import annotations

import sqlite3
from typing import Dict, List

from .config import sin_acentos

CAMPOS_PLANO = [("proyecto", "proyecto"), ("disciplina", "disciplina"), ("tipo_doc", "tipo"), ("numero", "numero"),
                ("hoja", "hoja"), ("revision", "revision"), ("titulo", "titulo")]


def _igual(campo: str, obtenido, esperado) -> bool:
    if campo == "titulo":
        return obtenido is not None and sin_acentos(str(obtenido)).strip() == sin_acentos(str(esperado)).strip()
    return obtenido == esperado


def _exactitud(filas: List[sqlite3.Row], verdad: Dict[str, dict]) -> List[tuple]:
    """Por campo: cuántos se informaron, cuántos de esos son correctos y qué cobertura representan."""
    resultado = []
    for columna, clave in CAMPOS_PLANO:
        informados = [f for f in filas if f[columna] is not None]
        correctos = sum(_igual(columna, f[columna], verdad[f["origen_id"]][clave]) for f in informados)
        total = len(filas) or 1
        resultado.append((columna, len(informados), correctos,
                          f"{100 * correctos / max(len(informados), 1):.1f} %", f"{100 * correctos / total:.1f} %"))
    return resultado


def evaluar(conexion: sqlite3.Connection, verdad: Dict[str, dict]) -> str:
    """Devuelve un reporte en Markdown con la exactitud de cada etapa."""
    filas = [f for f in conexion.execute("SELECT * FROM archivo") if f["origen_id"] in verdad]
    por_id = {f["id"]: f for f in filas}
    planos = [f for f in filas if verdad[f["origen_id"]]["clase"] == "plano"]

    # Copias: la huella no admite error, pero se comprueba contra el contenido real.
    marcadas = [f for f in filas if f["duplicado_de"] is not None]
    bien = sum(verdad[f["origen_id"]]["contenido"] == verdad[por_id[f["duplicado_de"]]["origen_id"]]["contenido"]
               for f in marcadas)
    contenidos: Dict[str, int] = {}
    for f in filas:
        contenidos[verdad[f["origen_id"]]["contenido"]] = contenidos.get(verdad[f["origen_id"]]["contenido"], 0) + 1
    copias_reales = sum(cantidad - 1 for cantidad in contenidos.values())

    basura = [f for f in filas if verdad[f["origen_id"]]["clase"] == "basura"]
    basura_detectada = sum(f["categoria"] in ("temporal", "sistema") for f in basura)
    disfrazados = [f for f in filas if verdad[f["origen_id"]].get("tipo_real") in ("dwg", "pdf", "doc")]
    recuperados = sum(f["tipo_por_firma"] == 1 for f in disfrazados)

    por_nombre = [f for f in planos if f["fuente"] == "nombre"]
    por_ia = [f for f in planos if f["fuente"] in ("ia", "nombre+ia")]
    # Ediciones sin nueva revisión: solo se pueden detectar cuando el plano ya está identificado.
    editados = [f for f in filas if verdad[f["origen_id"]].get("conflicto")]
    identificados = [f for f in editados if f["proyecto"] and f["disciplina"] and f["numero"] and f["revision"]]
    apartados = sum(f["estado_migracion"] == "conflicto" for f in identificados)

    def tabla(encabezados, cuerpo):
        lineas = ["| " + " | ".join(encabezados) + " |", "|" + "|".join(["---"] * len(encabezados)) + "|"]
        return "\n".join(lineas + ["| " + " | ".join(str(v) for v in fila) + " |" for fila in cuerpo])

    partes = [
        "# Evaluación contra la verdad conocida", "",
        tabla(["Comprobación", "Resultado"], [
            ("Copias exactas marcadas", f"{len(marcadas)} de {copias_reales} reales"),
            ("Copias marcadas que realmente lo son", f"{bien} de {len(marcadas)}"),
            ("Temporales y archivos de sistema detectados", f"{basura_detectada} de {len(basura)}"),
            ("Archivos con extensión engañosa reconocidos por su firma", f"{recuperados} de {len(disfrazados)}"),
            ("Ediciones sin nueva revisión apartadas", f"{apartados} de {len(identificados)} identificables "
                                                       f"({len(editados) - len(identificados)} esperan la lectura por IA)"),
        ]), "",
        f"## Planos: campos obtenidos del nombre del archivo ({len(por_nombre)} archivos)", "",
        tabla(["Campo", "Informados", "Correctos", "Precisión", "Cobertura"], _exactitud(por_nombre, verdad)), "",
    ]
    if por_ia:
        partes += [
            f"## Planos: campos tras la lectura del cajetín por IA ({len(por_ia)} archivos)", "",
            tabla(["Campo", "Informados", "Correctos", "Precisión", "Cobertura"], _exactitud(por_ia, verdad)), "",
        ]
    return "\n".join(partes)
