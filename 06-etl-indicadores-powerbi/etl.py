"""ETL de indicadores económicos de Chile.

Extrae series históricas desde la API pública de mindicador.cl, las valida y
transforma con Pandas, las carga en una base SQLite y exporta archivos listos
para Power BI y un reporte en Excel.

Uso:
    python etl.py                       # últimos 5 años, indicadores por defecto
    python etl.py --desde 2020 --hasta 2026
    python etl.py --indicadores uf dolar euro
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

API_URL = "https://mindicador.cl/api/{codigo}/{anio}"
INDICADORES_POR_DEFECTO = [
    "uf", "dolar", "euro", "utm", "ipc", "tpm", "libra_cobre", "tasa_desempleo",
]
MAX_REINTENTOS = 4
PAUSA_ENTRE_LLAMADAS = 0.3  # segundos; la API es gratuita, no hay que saturarla

CARPETA = Path(__file__).resolve().parent
RUTA_BD = CARPETA / "indicadores.db"
RUTA_EXCEL = CARPETA / "reporte_indicadores.xlsx"
CARPETA_POWERBI = CARPETA / "powerbi" / "datos"

log = logging.getLogger("etl")


# ---------------------------------------------------------------- Extracción

def descargar_serie(sesion: requests.Session, codigo: str, anio: int) -> dict:
    """Descarga un año de un indicador, con reintentos y espera creciente."""
    url = API_URL.format(codigo=codigo, anio=anio)
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            respuesta = sesion.get(url, timeout=30)
            if respuesta.status_code == 429 or respuesta.status_code >= 500:
                raise requests.HTTPError(f"HTTP {respuesta.status_code}")
            respuesta.raise_for_status()
            return respuesta.json()
        except (requests.RequestException, ValueError) as error:
            if intento == MAX_REINTENTOS:
                raise
            espera = 2 ** intento
            log.warning("%s %s: %s. Reintento %d en %ds", codigo, anio, error, intento, espera)
            time.sleep(espera)
    raise RuntimeError("inalcanzable")


def extraer(codigos: list[str], desde: int, hasta: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (catálogo de indicadores, valores en bruto)."""
    catalogo, filas = [], []
    with requests.Session() as sesion:
        sesion.headers["User-Agent"] = "etl-indicadores-portafolio/1.0"
        for codigo in codigos:
            for anio in range(desde, hasta + 1):
                datos = descargar_serie(sesion, codigo, anio)
                if anio == desde:
                    catalogo.append({
                        "codigo": datos["codigo"],
                        "nombre": datos["nombre"],
                        "unidad_medida": datos["unidad_medida"],
                    })
                if not datos.get("serie"):
                    log.warning("%s %d: la API no entrega datos para ese año", codigo, anio)
                for punto in datos.get("serie", []):
                    filas.append({"codigo": codigo, "fecha": punto["fecha"], "valor": punto["valor"]})
                time.sleep(PAUSA_ENTRE_LLAMADAS)
            log.info("Extraído %s (%d-%d)", codigo, desde, hasta)
    return pd.DataFrame(catalogo), pd.DataFrame(filas)


# ------------------------------------------------------------ Transformación

def transformar(bruto: pd.DataFrame) -> pd.DataFrame:
    """Tipa, valida y deja una fila por indicador y fecha."""
    df = bruto.copy()
    # La API entrega la medianoche de Chile expresada en UTC (03:00 o 04:00 Z),
    # por lo que la fecha UTC coincide con la fecha local.
    df["fecha"] = pd.to_datetime(df["fecha"], utc=True, errors="coerce").dt.date
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")

    invalidas = df["fecha"].isna() | df["valor"].isna()
    if invalidas.any():
        log.warning("Se descartan %d filas con fecha o valor inválido", int(invalidas.sum()))
    df = df[~invalidas]

    antes = len(df)
    df = df.drop_duplicates(subset=["codigo", "fecha"], keep="last")
    if len(df) < antes:
        log.warning("Se descartan %d filas duplicadas", antes - len(df))

    df["fecha"] = df["fecha"].astype(str)  # ISO 8601, el formato de fecha de SQLite
    return df.sort_values(["codigo", "fecha"]).reset_index(drop=True)


# --------------------------------------------------------------------- Carga

ESQUEMA = """
CREATE TABLE IF NOT EXISTS dim_indicador (
    codigo        TEXT PRIMARY KEY,
    nombre        TEXT NOT NULL,
    unidad_medida TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_valor (
    codigo TEXT NOT NULL REFERENCES dim_indicador (codigo),
    fecha  TEXT NOT NULL,
    valor  REAL NOT NULL,
    PRIMARY KEY (codigo, fecha)
);
CREATE TABLE IF NOT EXISTS etl_ejecucion (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecutado_en  TEXT NOT NULL,
    filas_leidas  INTEGER NOT NULL,
    filas_validas INTEGER NOT NULL
);
DROP VIEW IF EXISTS vw_mensual;
CREATE VIEW vw_mensual AS
WITH mensual AS (
    SELECT codigo,
           substr(fecha, 1, 7) || '-01' AS mes,
           AVG(valor)                   AS promedio,
           MIN(valor)                   AS minimo,
           MAX(valor)                   AS maximo,
           COUNT(*)                     AS observaciones
    FROM fact_valor
    GROUP BY codigo, substr(fecha, 1, 7)
),
con_rezagos AS (
    -- El rezago se busca por fecha y no por posición, para que un mes
    -- sin datos no desplace la comparación.
    SELECT m.*,
           (SELECT p.promedio FROM mensual p
             WHERE p.codigo = m.codigo AND p.mes = date(m.mes, '-1 month'))  AS promedio_mes_anterior,
           (SELECT p.promedio FROM mensual p
             WHERE p.codigo = m.codigo AND p.mes = date(m.mes, '-12 month')) AS promedio_anio_anterior
    FROM mensual m
)
-- dif_*: diferencia absoluta. Para tasas (unidad "Porcentaje") son puntos porcentuales.
-- var_*_pct: variación relativa. No se calcula para tasas, donde no tiene sentido.
SELECT r.codigo, r.mes, r.promedio, r.minimo, r.maximo, r.observaciones,
       ROUND(r.promedio - r.promedio_mes_anterior, 4)  AS dif_mensual,
       ROUND(r.promedio - r.promedio_anio_anterior, 4) AS dif_12m,
       CASE WHEN d.unidad_medida <> 'Porcentaje' THEN
            ROUND(100.0 * (r.promedio - r.promedio_mes_anterior) / r.promedio_mes_anterior, 2)
       END AS var_mensual_pct,
       CASE WHEN d.unidad_medida <> 'Porcentaje' THEN
            ROUND(100.0 * (r.promedio - r.promedio_anio_anterior) / r.promedio_anio_anterior, 2)
       END AS var_12m_pct
FROM con_rezagos r
JOIN dim_indicador d ON d.codigo = r.codigo;
"""


def cargar(conexion: sqlite3.Connection, catalogo: pd.DataFrame, valores: pd.DataFrame,
           filas_leidas: int) -> None:
    """Carga idempotente: volver a ejecutar actualiza, no duplica."""
    conexion.executescript(ESQUEMA)
    with conexion:
        conexion.executemany(
            "INSERT INTO dim_indicador (codigo, nombre, unidad_medida) VALUES (?, ?, ?) "
            "ON CONFLICT (codigo) DO UPDATE SET nombre = excluded.nombre, "
            "unidad_medida = excluded.unidad_medida",
            catalogo[["codigo", "nombre", "unidad_medida"]].itertuples(index=False, name=None),
        )
        conexion.executemany(
            "INSERT INTO fact_valor (codigo, fecha, valor) VALUES (?, ?, ?) "
            "ON CONFLICT (codigo, fecha) DO UPDATE SET valor = excluded.valor",
            valores[["codigo", "fecha", "valor"]].itertuples(index=False, name=None),
        )
        conexion.execute(
            "INSERT INTO etl_ejecucion (ejecutado_en, filas_leidas, filas_validas) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), filas_leidas, len(valores)),
        )


# ---------------------------------------------------------------- Exportación

CONSULTA_RESUMEN = """
WITH ultimo AS (
    -- La UF se publica con semanas de anticipación: se excluyen fechas futuras.
    SELECT codigo, MAX(fecha) AS fecha FROM fact_valor
    WHERE fecha <= date('now', 'localtime')
    GROUP BY codigo
)
SELECT d.nombre                         AS indicador,
       d.unidad_medida                  AS unidad,
       u.fecha                          AS ultima_fecha,
       f.valor                          AS ultimo_valor,
       m.dif_12m                        AS dif_12_meses,
       m.var_12m_pct                    AS var_12_meses_pct
FROM ultimo u
JOIN fact_valor f    ON f.codigo = u.codigo AND f.fecha = u.fecha
JOIN dim_indicador d ON d.codigo = u.codigo
LEFT JOIN vw_mensual m
       ON m.codigo = u.codigo AND m.mes = substr(u.fecha, 1, 7) || '-01'
ORDER BY d.nombre;
"""


def exportar(conexion: sqlite3.Connection) -> None:
    """Genera los CSV para Power BI y el reporte en Excel."""
    dim = pd.read_sql("SELECT * FROM dim_indicador ORDER BY codigo", conexion)
    diario = pd.read_sql("SELECT * FROM fact_valor ORDER BY codigo, fecha", conexion)
    mensual = pd.read_sql("SELECT * FROM vw_mensual ORDER BY codigo, mes", conexion)
    resumen = pd.read_sql(CONSULTA_RESUMEN, conexion)

    CARPETA_POWERBI.mkdir(parents=True, exist_ok=True)
    dim.to_csv(CARPETA_POWERBI / "dim_indicador.csv", index=False, encoding="utf-8-sig")
    diario.to_csv(CARPETA_POWERBI / "fact_valor_diario.csv", index=False, encoding="utf-8-sig")
    mensual.to_csv(CARPETA_POWERBI / "fact_valor_mensual.csv", index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(RUTA_EXCEL, engine="openpyxl") as libro:
        resumen.to_excel(libro, sheet_name="Resumen", index=False)
        mensual.merge(dim[["codigo", "nombre"]], on="codigo").to_excel(
            libro, sheet_name="Mensual", index=False)
        for hoja in libro.sheets.values():
            hoja.freeze_panes = "A2"
            hoja.auto_filter.ref = hoja.dimensions
            for columna in hoja.columns:
                ancho = max(len(str(celda.value)) if celda.value is not None else 0 for celda in columna)
                hoja.column_dimensions[columna[0].column_letter].width = min(ancho + 2, 45)
    log.info("Exportado: %s y CSV en %s", RUTA_EXCEL.name, CARPETA_POWERBI)


# ----------------------------------------------------------------- Principal

def main() -> None:
    anio_actual = date.today().year
    parser = argparse.ArgumentParser(description="ETL de indicadores económicos de Chile")
    parser.add_argument("--desde", type=int, default=anio_actual - 4, help="año inicial")
    parser.add_argument("--hasta", type=int, default=anio_actual, help="año final")
    parser.add_argument("--indicadores", nargs="+", default=INDICADORES_POR_DEFECTO)
    args = parser.parse_args()
    if args.desde > args.hasta:
        parser.error("--desde no puede ser mayor que --hasta")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

    catalogo, bruto = extraer(args.indicadores, args.desde, args.hasta)
    if bruto.empty:
        raise SystemExit("La API no devolvió datos para los parámetros indicados.")
    valores = transformar(bruto)

    conexion = sqlite3.connect(RUTA_BD)
    try:
        cargar(conexion, catalogo, valores, filas_leidas=len(bruto))
        exportar(conexion)
        total = conexion.execute("SELECT COUNT(*) FROM fact_valor").fetchone()[0]
    finally:
        conexion.close()
    log.info("Listo: %d filas leídas, %d válidas, %d en la base", len(bruto), len(valores), total)


if __name__ == "__main__":
    main()
