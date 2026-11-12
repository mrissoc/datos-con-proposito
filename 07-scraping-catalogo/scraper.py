"""Web scraping de un catálogo de tienda en línea hacia Excel.

Recorre todas las categorías y páginas de books.toscrape.com (un sitio creado
para practicar scraping), extrae título, categoría, precio, calificación y
disponibilidad de cada libro, y genera un Excel con el detalle y un resumen
por categoría.

Uso:
    python scraper.py                   # catálogo completo
    python scraper.py --max-categorias 3
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

URL_BASE = "https://books.toscrape.com/"
PAUSA = 0.2  # segundos entre solicitudes, para no cargar el servidor
MAX_REINTENTOS = 4
ESTRELLAS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}

CARPETA = Path(__file__).resolve().parent
RUTA_EXCEL = CARPETA / "catalogo_libros.xlsx"
RUTA_CSV = CARPETA / "catalogo_libros.csv"

log = logging.getLogger("scraper")


def obtener_pagina(sesion: requests.Session, url: str) -> BeautifulSoup:
    """Descarga y parsea una página, con reintentos y espera creciente."""
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            respuesta = sesion.get(url, timeout=30)
            respuesta.raise_for_status()
            respuesta.encoding = "utf-8"  # el servidor no declara la codificación
            time.sleep(PAUSA)
            return BeautifulSoup(respuesta.text, "html.parser")
        except requests.RequestException as error:
            if intento == MAX_REINTENTOS:
                raise
            espera = 2 ** intento
            log.warning("%s: %s. Reintento %d en %ds", url, error, intento, espera)
            time.sleep(espera)
    raise RuntimeError("inalcanzable")


def listar_categorias(sesion: requests.Session) -> list[tuple[str, str]]:
    """Devuelve (nombre, url) de cada categoría del menú lateral."""
    pagina = obtener_pagina(sesion, URL_BASE)
    enlaces = pagina.select("div.side_categories ul li ul li a")
    return [(a.get_text(strip=True), urljoin(URL_BASE, a["href"])) for a in enlaces]


def extraer_libro(articulo, categoria: str, url_pagina: str) -> dict:
    """Convierte la tarjeta HTML de un libro en un registro."""
    enlace = articulo.select_one("h3 a")
    precio_texto = articulo.select_one("p.price_color").get_text(strip=True)
    coincidencia = re.search(r"\d+(?:\.\d+)?", precio_texto)
    clases = articulo.select_one("p.star-rating").get("class", [])
    estrellas = next((ESTRELLAS[c] for c in clases if c in ESTRELLAS), None)
    disponibilidad = articulo.select_one("p.availability").get_text(strip=True)
    return {
        "titulo": enlace["title"],
        "categoria": categoria,
        "precio_gbp": float(coincidencia.group()) if coincidencia else None,
        "calificacion": estrellas,
        "en_stock": disponibilidad.lower().startswith("in stock"),
        "url": urljoin(url_pagina, enlace["href"]),
    }


def recorrer_categoria(sesion: requests.Session, nombre: str, url: str) -> list[dict]:
    """Extrae todos los libros de una categoría siguiendo la paginación."""
    libros = []
    while url:
        pagina = obtener_pagina(sesion, url)
        for articulo in pagina.select("article.product_pod"):
            libros.append(extraer_libro(articulo, nombre, url))
        siguiente = pagina.select_one("li.next a")
        url = urljoin(url, siguiente["href"]) if siguiente else None
    return libros


def guardar(df: pd.DataFrame) -> None:
    """Escribe el CSV y un Excel con detalle y resumen por categoría."""
    resumen = (
        df.groupby("categoria")
        .agg(libros=("titulo", "count"),
             precio_promedio_gbp=("precio_gbp", "mean"),
             precio_minimo_gbp=("precio_gbp", "min"),
             precio_maximo_gbp=("precio_gbp", "max"),
             calificacion_promedio=("calificacion", "mean"))
        .round(2)
        .sort_values("libros", ascending=False)
        .reset_index()
    )
    df.to_csv(RUTA_CSV, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(RUTA_EXCEL, engine="openpyxl") as libro:
        df.to_excel(libro, sheet_name="Libros", index=False)
        resumen.to_excel(libro, sheet_name="Resumen por categoría", index=False)
        for hoja in libro.sheets.values():
            hoja.freeze_panes = "A2"
            hoja.auto_filter.ref = hoja.dimensions
            for columna in hoja.columns:
                ancho = max(len(str(c.value)) if c.value is not None else 0 for c in columna)
                hoja.column_dimensions[columna[0].column_letter].width = min(ancho + 2, 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scraping de catálogo de libros a Excel")
    parser.add_argument("--max-categorias", type=int, default=None,
                        help="limita la cantidad de categorías (para pruebas)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

    libros = []
    with requests.Session() as sesion:
        sesion.headers["User-Agent"] = "scraper-portafolio/1.0"
        categorias = listar_categorias(sesion)[: args.max_categorias]
        for numero, (nombre, url) in enumerate(categorias, start=1):
            encontrados = recorrer_categoria(sesion, nombre, url)
            libros.extend(encontrados)
            log.info("[%d/%d] %s: %d libros", numero, len(categorias), nombre, len(encontrados))

    df = pd.DataFrame(libros).drop_duplicates(subset="url")
    sin_precio = int(df["precio_gbp"].isna().sum())
    if sin_precio:
        log.warning("%d libros sin precio legible", sin_precio)
    guardar(df)
    log.info("Listo: %d libros en %d categorías -> %s", len(df), df["categoria"].nunique(),
             RUTA_EXCEL.name)


if __name__ == "__main__":
    main()
