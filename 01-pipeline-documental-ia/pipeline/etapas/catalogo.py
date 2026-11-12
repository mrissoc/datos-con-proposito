"""Etapa 6: catálogo para la biblioteca web y reporte de resultados."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..bd import ahora
from ..config import RAIZ, Taxonomia

RUTA_RED = RAIZ.parent / "datos-ejemplo" / "red.json"

FILAS_CATALOGO = """
SELECT a.id, a.origen_id, a.clase, a.proyecto, a.disciplina, a.tipo_doc, a.numero, a.hoja, a.revision,
       a.revision_orden, a.titulo, a.fuente, a.vigente, a.extension_real, a.tamano, a.modificado, a.destino,
       (SELECT COUNT(*) FROM archivo c WHERE c.duplicado_de = a.id) AS copias
FROM archivo a
WHERE a.destino IS NOT NULL AND a.estado_migracion IN ('pendiente', 'migrado')
ORDER BY a.clase DESC, a.proyecto, a.disciplina, a.numero, a.hoja, a.revision_orden DESC, a.extension_real
"""


def exportar(conexion: sqlite3.Connection, taxonomia: Taxonomia, salida: Path,
             vista_previa: Optional[Callable[[sqlite3.Row], Optional[str]]] = None) -> dict:
    """Escribe el catálogo en JSON: una entrada por plano (por hoja) o documento, con sus revisiones.

    `vista_previa` recibe la fila del archivo vigente y devuelve la ruta de su imagen, si la genera.
    """
    entradas: Dict[tuple, dict] = {}
    for fila in conexion.execute(FILAS_CATALOGO):
        clave = (fila["clase"], fila["proyecto"], fila["disciplina"], fila["tipo_doc"] if fila["clase"] == "documento" else None,
                 fila["numero"], fila["hoja"])
        entrada = entradas.get(clave)
        if entrada is None:
            proyecto = taxonomia.proyectos[fila["proyecto"]]
            entrada = entradas[clave] = {
                "id": f"{fila['clase'][0]}{fila['id']}", "clase": fila["clase"], "proyecto": fila["proyecto"],
                "instalacion": proyecto["instalacion"], "anio": proyecto["anio"], "disciplina": fila["disciplina"],
                "tipo": fila["tipo_doc"], "numero": fila["numero"], "hoja": fila["hoja"], "titulo": None,
                "revision": fila["revision"], "fecha": (fila["modificado"] or "")[:10], "fuente": fila["fuente"],
                "formatos": [], "copias": 0, "vista": None, "revisiones": [],
            }
        # Las filas llegan con la revisión más reciente primero.
        entrada["titulo"] = entrada["titulo"] or fila["titulo"]
        entrada["tipo"] = entrada["tipo"] or fila["tipo_doc"]
        entrada["copias"] += fila["copias"]
        revisiones: List[dict] = entrada["revisiones"]
        if not revisiones or revisiones[-1]["revision"] != fila["revision"]:
            revisiones.append({"revision": fila["revision"], "fecha": (fila["modificado"] or "")[:10],
                               "vigente": bool(fila["vigente"]), "archivos": []})
        revisiones[-1]["archivos"].append({"formato": fila["extension_real"], "destino": fila["destino"],
                                           "tamano": fila["tamano"]})
        if fila["vigente"]:
            if fila["extension_real"] not in entrada["formatos"]:
                entrada["formatos"].append(fila["extension_real"])
            if vista_previa and entrada["vista"] is None:
                entrada["vista"] = vista_previa(fila)

    red = json.loads(RUTA_RED.read_text(encoding="utf-8"))
    catalogo = {
        "generado": ahora(),
        "taxonomia": {
            "disciplinas": [{"codigo": c, "nombre": n} for c, n in taxonomia.disciplinas.items()],
            "tipos_plano": [{"codigo": c, "nombre": n} for c, n in taxonomia.tipos_plano.items()],
            "tipos_documento": [{"codigo": c, "nombre": n} for c, n in taxonomia.tipos_documento.items()],
            "proyectos": [{"codigo": p["codigo"], "nombre": p["nombre"], "anio": p["anio"], "instalacion": p["instalacion"]}
                          for p in taxonomia.proyectos.values()],
        },
        "instalaciones": [{"codigo": n["codigo"], "nombre": n["nombre"], "tipo": n["tipo"]}
                          for n in red["nodos"] if n["tipo"] != "sector"],
        "resumen": resumen(conexion),
        "items": list(entradas.values()),
    }
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(catalogo, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"entradas": len(entradas), "planos": sum(e["clase"] == "plano" for e in entradas.values()),
            "documentos": sum(e["clase"] == "documento" for e in entradas.values()),
            "con_vista": sum(e["vista"] is not None for e in entradas.values())}


def resumen(conexion: sqlite3.Connection) -> dict:
    """Cifras generales del estado del repositorio."""
    uno = lambda consulta: conexion.execute(consulta).fetchone()[0]          # noqa: E731
    return {
        "archivos": uno("SELECT COUNT(*) FROM archivo"),
        "extensiones": uno("SELECT COUNT(DISTINCT extension) FROM archivo"),
        "copias": uno("SELECT COUNT(*) FROM archivo WHERE duplicado_de IS NOT NULL"),
        "descartables": uno("SELECT COUNT(*) FROM archivo WHERE categoria IN ('temporal', 'sistema')"),
        "catalogados": uno("SELECT COUNT(*) FROM archivo WHERE estado_migracion IN ('pendiente', 'migrado')"),
        "por_revisar": uno("SELECT COUNT(*) FROM archivo WHERE requiere_revision = 1 AND duplicado_de IS NULL"),
        "leidos_por_ia": uno("SELECT COUNT(*) FROM lectura_ia WHERE estado = 'ok'"),
    }


def _tabla(encabezados: List[str], filas: List[tuple]) -> str:
    lineas = ["| " + " | ".join(encabezados) + " |", "|" + "|".join(["---"] * len(encabezados)) + "|"]
    lineas += ["| " + " | ".join(str(valor) for valor in fila) + " |" for fila in filas]
    return "\n".join(lineas)


def _miles(numero: int) -> str:
    return f"{numero:,}".replace(",", ".")


def _tamano(cantidad_bytes: int) -> str:
    for unidad, divisor in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if cantidad_bytes >= divisor:
            return f"{cantidad_bytes / divisor:.1f} {unidad}".replace(".", ",")
    return f"{cantidad_bytes} bytes"


def reporte(conexion: sqlite3.Connection) -> str:
    """Reporte en Markdown: qué había, qué se reconoció y qué quedó pendiente."""
    filas = lambda consulta: conexion.execute(consulta).fetchall()           # noqa: E731
    r = resumen(conexion)
    total = max(r["archivos"], 1)
    bytes_copias = conexion.execute(
        "SELECT COALESCE(SUM(tamano), 0) FROM archivo WHERE duplicado_de IS NOT NULL").fetchone()[0]
    por_firma = conexion.execute("SELECT COUNT(*) FROM archivo WHERE tipo_por_firma = 1").fetchone()[0]
    partes = [
        "# Reporte del repositorio", "",
        _tabla(["Indicador", "Valor"], [
            ("Archivos inventariados", _miles(r["archivos"])),
            ("Extensiones distintas", _miles(r["extensiones"])),
            ("Tipo reconocido por la firma del archivo", _miles(por_firma)),
            ("Copias exactas", f"{_miles(r['copias'])} ({100 * r['copias'] / total:.1f} %)"),
            ("Espacio en copias exactas", _tamano(bytes_copias)),
            ("Temporales y archivos de sistema", _miles(r["descartables"])),
            ("Archivos con destino en la biblioteca", _miles(r["catalogados"])),
            ("Archivos que requieren revisión", _miles(r["por_revisar"])),
            ("Planos leídos por IA", _miles(r["leidos_por_ia"])),
        ]), "",
        "## Categorías", "",
        _tabla(["Categoría", "Archivos", "Extensiones"], [
            (f["categoria"], _miles(f["n"]), f["extensiones"]) for f in filas(
                "SELECT categoria, COUNT(*) AS n, COUNT(DISTINCT extension) AS extensiones FROM archivo "
                "GROUP BY categoria ORDER BY n DESC")]), "",
        "## Extensiones más frecuentes", "",
        _tabla(["Extensión", "Archivos"], [
            (f["extension"] or "(sin extensión)", _miles(f["n"])) for f in filas(
                "SELECT extension, COUNT(*) AS n FROM archivo GROUP BY extension ORDER BY n DESC, extension LIMIT 12")]), "",
        "## Clasificación por nombre", "",
        _tabla(["Convención", "Archivos", "Con todos los campos"], [
            (f["patron"] or "(nombre no reconocido)", _miles(f["n"]), _miles(f["completos"])) for f in filas(
                "SELECT patron, COUNT(*) AS n, SUM(requiere_revision = 0) AS completos FROM archivo "
                "WHERE categoria IN ('cad', 'pdf', 'ofimatica', 'imagen') GROUP BY patron ORDER BY n DESC")]), "",
        "## Migración", "",
        _tabla(["Estado", "Archivos"], [
            (f["estado"], _miles(f["n"])) for f in filas(
                "SELECT COALESCE(estado_migracion, 'sin destino') AS estado, COUNT(*) AS n FROM archivo "
                "WHERE duplicado_de IS NULL AND categoria NOT IN ('temporal', 'sistema') GROUP BY estado ORDER BY n DESC")]), "",
        "## Tiempos por etapa", "",
        _tabla(["Etapa", "Segundos", "Detalle"], [
            (f["etapa"], f"{f['segundos']:.2f}", f["detalle"] or "") for f in filas(
                "SELECT etapa, segundos, detalle FROM ejecucion WHERE id IN (SELECT MAX(id) FROM ejecucion GROUP BY etapa) "
                "ORDER BY id")]), "",
    ]
    return "\n".join(partes)
