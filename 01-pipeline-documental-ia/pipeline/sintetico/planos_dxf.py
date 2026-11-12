"""Genera planos DXF de ejemplo: una lámina A1 con un dibujo genérico y su cajetín.

Hay tres formatos de cajetín, porque en un archivo heredado conviven los de
distintas épocas y oficinas: horizontal en español (A), franja vertical al
costado de la lámina (B) y antiguo con rótulos en inglés y tabla de revisiones (C).
Esa variedad es la razón de leer los cajetines con visión por IA y no con una
plantilla fija de coordenadas.
"""

from __future__ import annotations

import io
import random
from typing import Sequence, Tuple

import ezdxf

from .repositorio import DatosCajetin

ANCHO, ALTO = 841.0, 594.0            # lámina A1, en milímetros
MARGEN = 10.0
DESCRIPCION_REVISION = ["EMITIDO PARA REVISION", "EMITIDO PARA APROBACION", "EMITIDO PARA CONSTRUCCION",
                        "ACTUALIZADO SEGUN OBRA"]


def crear_dxf(datos: DatosCajetin) -> bytes:
    """Devuelve el contenido de un archivo DXF con el plano."""
    # Sin esta opción el archivo lleva la hora y un identificador aleatorio, y dos
    # corridas con la misma semilla producirían huellas distintas.
    ezdxf.options.write_fixed_meta_data_for_testing = True
    documento = ezdxf.new("R2010")
    documento.header["$INSUNITS"] = 4                       # milímetros
    espacio = documento.modelspace()
    rng = random.Random(datos.semilla)

    _rectangulo(espacio, 0, 0, ANCHO, ALTO)
    _rectangulo(espacio, MARGEN + 10, MARGEN, ANCHO - MARGEN, ALTO - MARGEN)
    if datos.estilo == "B":
        _cajetin_vertical(espacio, datos)
        zona = (MARGEN + 20, MARGEN + 10, ANCHO - MARGEN - 80, ALTO - MARGEN - 10)
    elif datos.estilo == "C":
        _cajetin_antiguo(espacio, datos)
        zona = (MARGEN + 20, MARGEN + 70, ANCHO - MARGEN - 10, ALTO - MARGEN - 10)
    else:
        _cajetin_horizontal(espacio, datos)
        zona = (MARGEN + 20, MARGEN + 75, ANCHO - MARGEN - 10, ALTO - MARGEN - 10)
    _dibujo_generico(espacio, rng, zona)

    salida = io.StringIO()
    documento.write(salida)
    return salida.getvalue().encode("utf-8")


# ------------------------------------------------------------------ Primitivas

def _rectangulo(espacio, x1: float, y1: float, x2: float, y2: float) -> None:
    espacio.add_lwpolyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], close=True)


def _linea(espacio, x1: float, y1: float, x2: float, y2: float) -> None:
    espacio.add_line((x1, y1), (x2, y2))


def _texto(espacio, texto: str, x: float, y: float, alto: float = 3.0) -> None:
    espacio.add_text(texto, dxfattribs={"height": alto, "insert": (x, y)})


def _campo(espacio, rotulo: str, valor: str, x: float, y: float, alto_valor: float = 3.5) -> None:
    """Una celda de cajetín: rótulo pequeño arriba y valor debajo."""
    _texto(espacio, rotulo, x, y + alto_valor + 2.5, 1.8)
    _texto(espacio, valor, x, y, alto_valor)


def _fecha(datos: DatosCajetin) -> str:
    return datos.fecha.strftime("%d-%m-%Y")


# ------------------------------------------------------------------- Cajetines

def _cajetin_horizontal(espacio, d: DatosCajetin) -> None:
    """Formato A: bloque de 200 x 60 mm en la esquina inferior derecha."""
    x1, y1 = ANCHO - MARGEN - 200, MARGEN
    x2, y2 = ANCHO - MARGEN, MARGEN + 60
    _rectangulo(espacio, x1, y1, x2, y2)
    for y in (y1 + 14, y1 + 30, y1 + 46):
        _linea(espacio, x1, y, x2, y)
    _texto(espacio, "EMPRESA SANITARIA DE EJEMPLO", x1 + 4, y1 + 50, 4.5)
    _campo(espacio, "PROYECTO", f"{d.proyecto}  {d.nombre_proyecto.upper()}", x1 + 4, y1 + 33)
    _linea(espacio, x1 + 120, y1 + 14, x1 + 120, y1 + 30)
    _campo(espacio, "TITULO", d.titulo.upper(), x1 + 4, y1 + 17, 3.2)
    _campo(espacio, "CONTENIDO", d.subtitulo.upper(), x1 + 123, y1 + 17, 3.0)
    columnas = [(0, "N° PLANO", d.codigo_impreso), (78, "HOJA", f"{d.hoja} DE {d.hojas}"), (108, "REV.", d.revision),
                (128, "FECHA", _fecha(d)), (165, "ESCALA", d.escala)]
    for desplazamiento, rotulo, valor in columnas:
        if desplazamiento:
            _linea(espacio, x1 + desplazamiento, y1, x1 + desplazamiento, y1 + 14)
        _campo(espacio, rotulo, valor, x1 + desplazamiento + 3, y1 + 2.5, 4.0 if rotulo == "N° PLANO" else 3.5)


def _cajetin_vertical(espacio, d: DatosCajetin) -> None:
    """Formato B: franja de 65 mm de ancho a todo el alto del borde derecho."""
    x1, x2 = ANCHO - MARGEN - 65, ANCHO - MARGEN
    y1, y2 = MARGEN, ALTO - MARGEN
    _linea(espacio, x1, y1, x1, y2)
    _texto(espacio, f"{d.codigo_impreso.split('-')[0]} INGENIERIA", x1 + 4, y2 - 14, 4.5)
    _texto(espacio, "CONSULTORES", x1 + 4, y2 - 21, 2.5)
    filas = [("MANDANTE", "EMPRESA SANITARIA DE EJEMPLO"), ("OBRA", d.nombre_proyecto.upper()),
             ("PLANO", d.titulo.upper()), ("SECTOR", d.subtitulo.upper()), ("CODIGO", d.codigo_impreso),
             ("LAMINA", f"{d.hoja}/{d.hojas}"), ("REVISION", d.revision), ("FECHA", _fecha(d)), ("ESC.", d.escala)]
    y = y2 - 34
    for rotulo, valor in filas:
        _linea(espacio, x1, y + 12, x2, y + 12)
        _texto(espacio, rotulo, x1 + 3, y + 7.5, 1.8)
        for numero, renglon in enumerate(_partir(valor, 24)):
            _texto(espacio, renglon, x1 + 3, y + 2 - 5 * numero, 3.0 if rotulo != "CODIGO" else 3.6)
        y -= 14 + 5 * (len(_partir(valor, 24)) - 1)
    _linea(espacio, x1, y + 12, x2, y + 12)


def _cajetin_antiguo(espacio, d: DatosCajetin) -> None:
    """Formato C: bloque de 170 x 45 mm con rótulos en inglés y tabla de revisiones encima."""
    x1, y1 = ANCHO - MARGEN - 170, MARGEN
    x2, y2 = ANCHO - MARGEN, MARGEN + 45
    _rectangulo(espacio, x1, y1, x2, y2)
    _linea(espacio, x1, y1 + 15, x2, y1 + 15)
    _linea(espacio, x1, y1 + 32, x2, y1 + 32)
    _texto(espacio, d.nombre_proyecto.upper(), x1 + 4, y1 + 36, 3.8)
    _campo(espacio, "TITLE", d.titulo.upper(), x1 + 4, y1 + 18, 3.2)
    _texto(espacio, d.subtitulo.upper(), x1 + 100, y1 + 18, 2.5)
    columnas = [(0, "DWG No.", d.codigo_impreso), (52, "SHT", f"{d.hoja} OF {d.hojas}"), (82, "REV", d.revision),
                (100, "DATE", d.fecha.strftime("%d/%m/%y")), (134, "SCALE", d.escala)]
    for desplazamiento, rotulo, valor in columnas:
        if desplazamiento:
            _linea(espacio, x1 + desplazamiento, y1, x1 + desplazamiento, y1 + 15)
        _campo(espacio, rotulo, valor, x1 + desplazamiento + 3, y1 + 3, 4.0 if rotulo == "DWG No." else 3.5)
    # Tabla de revisiones: la revisión vigente es la última fila.
    base = y2 + 4
    _texto(espacio, "REV", x1 + 3, base + 6 * len(d.revisiones) + 1, 2.0)
    _texto(espacio, "DESCRIPTION", x1 + 20, base + 6 * len(d.revisiones) + 1, 2.0)
    for numero, revision in enumerate(reversed(d.revisiones)):
        y = base + 6 * numero
        _rectangulo(espacio, x1, y - 1.5, x2, y + 4.5)
        _texto(espacio, revision, x1 + 3, y, 2.5)
        indice = len(d.revisiones) - 1 - numero
        _texto(espacio, DESCRIPCION_REVISION[min(indice, len(DESCRIPCION_REVISION) - 1)], x1 + 20, y, 2.5)


def _partir(texto: str, ancho: int) -> Sequence[str]:
    """Divide un texto en renglones de a lo más `ancho` caracteres, sin cortar palabras."""
    renglones, actual = [], ""
    for palabra in texto.split():
        if actual and len(actual) + 1 + len(palabra) > ancho:
            renglones.append(actual)
            actual = palabra
        else:
            actual = f"{actual} {palabra}".strip()
    return renglones + [actual] if actual else renglones or [""]


# ---------------------------------------------------------------------- Dibujo

def _dibujo_generico(espacio, rng: random.Random, zona: Tuple[float, float, float, float]) -> None:
    """Rellena la lámina con recintos, equipos y tuberías, para que parezca un plano."""
    x1, y1, x2, y2 = zona
    ancho, alto = x2 - x1, y2 - y1
    columnas, filas = rng.randint(2, 4), rng.randint(2, 3)
    for columna in range(columnas):
        for fila in range(filas):
            if rng.random() < 0.25:
                continue
            ax = x1 + ancho * columna / columnas + 12
            ay = y1 + alto * fila / filas + 12
            bx = x1 + ancho * (columna + 1) / columnas - 12
            by = y1 + alto * (fila + 1) / filas - 12
            _rectangulo(espacio, ax, ay, bx, by)
            _rectangulo(espacio, ax + 3, ay + 3, bx - 3, by - 3)
            for equipo in range(rng.randint(1, 4)):
                cx, cy = rng.uniform(ax + 25, bx - 25), rng.uniform(ay + 25, by - 25)
                radio = rng.uniform(6, 14)
                espacio.add_circle((cx, cy), radio)
                espacio.add_circle((cx, cy), radio * 0.45)
                _texto(espacio, f"{rng.choice('BVTM')}-{rng.randint(1, 40):02d}", cx - 6, cy - radio - 6, 2.5)
                _linea(espacio, cx + radio, cy, bx - 3, cy)
            cota = by + 4
            _linea(espacio, ax, cota, bx, cota)
            _texto(espacio, f"{(bx - ax) * rng.choice([50, 100]) / 1000:.2f} m", (ax + bx) / 2 - 8, cota + 1.5, 2.2)
    puntos = [(x1 + 6, rng.uniform(y1 + 20, y2 - 20))]
    while puntos[-1][0] < x2 - 40:
        ultimo = puntos[-1]
        puntos.append((ultimo[0] + rng.uniform(30, 90), ultimo[1]))
        puntos.append((puntos[-1][0], min(max(ultimo[1] + rng.uniform(-70, 70), y1 + 8), y2 - 8)))
    espacio.add_lwpolyline(puntos)
    _texto(espacio, "N", x1 + 8, y2 - 14, 5)
    espacio.add_circle((x1 + 10, y2 - 12), 7)
