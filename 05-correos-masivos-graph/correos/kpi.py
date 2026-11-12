"""Seguimiento de KPIs en el tiempo.

La plataforma de cursos solo muestra el avance de hoy. Para ver la evolución se
guarda una foto diaria de los indicadores; Power BI lee el historial acumulado.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import date
from pathlib import Path

FOTO = """
INSERT OR REPLACE INTO kpi_diario (fecha, pais, gerencia, asignados, completados, vencidos)
SELECT :fecha, e.pais, e.gerencia,
       COUNT(*),
       SUM(a.completado_en IS NOT NULL AND a.completado_en <= :fecha),
       SUM((a.completado_en IS NULL OR a.completado_en > :fecha) AND a.fecha_limite < :fecha)
FROM asignacion a
JOIN empleado e ON e.id = a.empleado_id
GROUP BY e.pais, e.gerencia
"""

# Avance por día y variación respecto del día anterior, con una función de ventana.
EVOLUCION = """
SELECT fecha, pais,
       SUM(asignados)   AS asignados,
       SUM(completados) AS completados,
       SUM(vencidos)    AS vencidos,
       ROUND(100.0 * SUM(completados) / SUM(asignados), 1) AS avance_pct,
       ROUND(100.0 * SUM(completados) / SUM(asignados)
             - LAG(100.0 * SUM(completados) / SUM(asignados)) OVER (PARTITION BY pais ORDER BY fecha), 2) AS variacion_pp
FROM kpi_diario
GROUP BY fecha, pais
ORDER BY pais, fecha
"""


def tomar_foto(conexion: sqlite3.Connection, fecha: date) -> int:
    """Guarda los indicadores del día. Repetirla el mismo día reemplaza la foto, no la duplica."""
    with conexion:
        return conexion.execute(FOTO, {"fecha": fecha.isoformat()}).rowcount


def evolucion(conexion: sqlite3.Connection) -> list:
    return [dict(fila) for fila in conexion.execute(EVOLUCION)]


def exportar(conexion: sqlite3.Connection, carpeta: Path) -> None:
    """Deja el historial en CSV, listo para el modelo de Power BI."""
    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre, consulta in (("kpi_diario", "SELECT * FROM kpi_diario ORDER BY fecha, pais, gerencia"),
                             ("kpi_evolucion", EVOLUCION)):
        cursor = conexion.execute(consulta)
        with (carpeta / f"{nombre}.csv").open("w", newline="", encoding="utf-8-sig") as archivo:
            escritor = csv.writer(archivo)
            escritor.writerow([columna[0] for columna in cursor.description])
            escritor.writerows(cursor)
