"""Etapa 4: copias exactas y revisiones vigentes.

Dos archivos con la misma huella de contenido son el mismo archivo, se llamen
como se llamen. De cada grupo se conserva uno (el canónico) y los demás quedan
apuntando a él. Luego, entre los canónicos de un mismo plano, se marca cuál es
la revisión vigente.
"""

from __future__ import annotations

import sqlite3

# El canónico es, en orden: el que no está en una carpeta de respaldo, el que
# tiene un nombre reconocido, el de ruta más corta y el más antiguo.
CANONICOS = """
CREATE TEMP TABLE canonico AS
SELECT id,
       FIRST_VALUE(id) OVER (
           PARTITION BY hash
           ORDER BY en_respaldo, (patron IS NULL), length(ruta), modificado, id
       ) AS canonico_id
FROM archivo
WHERE hash IS NOT NULL;
"""

VIGENTES = """
CREATE TEMP TABLE revision_maxima AS
SELECT id,
       CASE WHEN revision_orden = MAX(revision_orden) OVER (
                PARTITION BY clase, proyecto, disciplina, CASE WHEN clase = 'documento' THEN tipo_doc END, numero, hoja
            ) THEN 1 ELSE 0 END AS vigente
FROM archivo
WHERE duplicado_de IS NULL AND clase IS NOT NULL AND numero IS NOT NULL AND revision_orden IS NOT NULL;
"""


def deduplicar(conexion: sqlite3.Connection) -> dict:
    """Marca las copias exactas. Se puede repetir: recalcula todo desde las huellas."""
    with conexion:
        conexion.execute("DROP TABLE IF EXISTS temp.canonico")
        conexion.execute(CANONICOS)
        conexion.execute("CREATE INDEX temp.ix_canonico ON canonico (id)")
        conexion.execute(
            "UPDATE archivo SET duplicado_de = "
            "(SELECT c.canonico_id FROM temp.canonico c WHERE c.id = archivo.id AND c.canonico_id <> archivo.id)")
        conexion.execute("DROP TABLE temp.canonico")
    copias, espacio = conexion.execute(
        "SELECT COUNT(*), COALESCE(SUM(tamano), 0) FROM archivo WHERE duplicado_de IS NOT NULL").fetchone()
    return {"copias": copias, "bytes_en_copias": espacio, **marcar_vigentes(conexion)}


def marcar_vigentes(conexion: sqlite3.Connection) -> dict:
    """Marca la última revisión de cada plano o documento. Se repite tras la lectura por IA."""
    with conexion:
        conexion.execute("DROP TABLE IF EXISTS temp.revision_maxima")
        conexion.execute(VIGENTES)
        conexion.execute("CREATE INDEX temp.ix_revision_maxima ON revision_maxima (id)")
        conexion.execute("UPDATE archivo SET vigente = (SELECT r.vigente FROM temp.revision_maxima r WHERE r.id = archivo.id)")
        conexion.execute("DROP TABLE temp.revision_maxima")
    vigentes, superadas = conexion.execute(
        "SELECT COALESCE(SUM(vigente = 1), 0), COALESCE(SUM(vigente = 0), 0) FROM archivo").fetchone()
    return {"vigentes": vigentes, "superadas": superadas}
