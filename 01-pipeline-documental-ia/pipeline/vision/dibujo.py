"""Convierte un plano DXF en imágenes para la lectura por IA.

Se generan dos vistas: el recorte de la esquina inferior derecha, donde suele
estar el cajetín, y la lámina completa, que sirve de respaldo cuando el cajetín
está en otra parte (por ejemplo, en una franja vertical).

Los archivos DWG no se pueden leer directamente: en un proyecto real se
convierten antes a DXF (por ejemplo con ODA File Converter) o se usa el PDF de
trazado, y a partir de ahí el proceso es el mismo.
"""

from __future__ import annotations

import io
from typing import Tuple

from PIL import Image

# La API acepta imágenes de hasta 2576 px por lado sin reducirlas; más píxeles
# solo suman costo. Para leer texto pequeño conviene acercarse a ese máximo.
LADO_MAXIMO = 2576


def dxf_a_png(contenido: bytes, ancho_px: int = 4200) -> bytes:
    """Dibuja el espacio modelo del DXF en negro sobre blanco y lo devuelve como PNG."""
    import matplotlib
    matplotlib.use("Agg")
    from ezdxf import bbox, recover
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from matplotlib.figure import Figure

    documento, _ = recover.read(io.BytesIO(contenido))
    espacio = documento.modelspace()
    limites = bbox.extents(espacio)
    ancho, alto = (limites.size.x, limites.size.y) if limites.has_data else (841.0, 594.0)
    puntos_por_pulgada = 200
    figura = Figure(figsize=(ancho_px / puntos_por_pulgada, ancho_px * alto / ancho / puntos_por_pulgada))
    ejes = figura.add_axes([0, 0, 1, 1])
    configuracion = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK)
    Frontend(RenderContext(documento), MatplotlibBackend(ejes), config=configuracion).draw_layout(espacio, finalize=True)
    # El dibujo ajusta la figura a su contenido: el tamaño final se fija al guardar.
    figura.set_size_inches(ancho_px / puntos_por_pulgada, ancho_px * alto / ancho / puntos_por_pulgada)
    salida = io.BytesIO()
    figura.savefig(salida, format="png", dpi=puntos_por_pulgada)
    return salida.getvalue()


def recortar_cajetin(png: bytes, fraccion_ancho: float = 0.36, fraccion_alto: float = 0.24) -> bytes:
    """Recorta la esquina inferior derecha de la lámina."""
    imagen = Image.open(io.BytesIO(png))
    ancho, alto = imagen.size
    caja = (int(ancho * (1 - fraccion_ancho)), int(alto * (1 - fraccion_alto)), ancho, alto)
    return _guardar(_ajustar(imagen.crop(caja)))


def lamina_completa(png: bytes) -> bytes:
    """La lámina entera, reducida al tamaño máximo útil."""
    return _guardar(_ajustar(Image.open(io.BytesIO(png))))


def dimensiones(png: bytes) -> Tuple[int, int]:
    return Image.open(io.BytesIO(png)).size


def _ajustar(imagen: Image.Image) -> Image.Image:
    mayor = max(imagen.size)
    if mayor > LADO_MAXIMO:
        escala = LADO_MAXIMO / mayor
        imagen = imagen.resize((round(imagen.width * escala), round(imagen.height * escala)), Image.LANCZOS)
    return imagen.convert("L")           # escala de grises: basta para leer texto y pesa menos


def _guardar(imagen: Image.Image) -> bytes:
    salida = io.BytesIO()
    imagen.save(salida, format="PNG", optimize=True)
    return salida.getvalue()
