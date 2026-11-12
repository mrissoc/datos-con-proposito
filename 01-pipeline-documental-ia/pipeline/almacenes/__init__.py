"""Almacenes de archivos.

El pipeline no sabe dónde están los archivos: trabaja contra esta interfaz.
Hay tres implementaciones:

- `AlmacenLocal`: una carpeta del disco. Es la que usa la demostración.
- `AlmacenSharePoint`: una biblioteca de SharePoint, vía Microsoft Graph.
- `AlmacenSintetico`: metadatos generados en memoria, para medir el pipeline a
  la escala de cientos de miles de archivos sin crearlos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, Optional

try:
    from typing import Protocol
except ImportError:                      # Python 3.7
    from typing_extensions import Protocol


@dataclass(frozen=True)
class ArchivoOrigen:
    origen_id: str                       # identificador estable dentro del almacén
    ruta: str                            # carpeta relativa, separada por '/'
    nombre: str
    tamano: int
    modificado: Optional[str]            # fecha en ISO 8601
    hash: Optional[str]                  # huella del contenido, si el almacén la entrega


class ErrorTransitorio(Exception):
    """El almacén pide reintentar: límite de solicitudes o falla temporal."""

    def __init__(self, mensaje: str, esperar: Optional[float] = None):
        super().__init__(mensaje)
        self.esperar = esperar           # segundos sugeridos por el servicio, si los indicó


class Almacen(Protocol):
    def listar(self) -> Iterator[ArchivoOrigen]:
        """Recorre todos los archivos del origen."""

    def leer_inicio(self, origen_id: str, cantidad: int = 32) -> bytes:
        """Primeros bytes de un archivo, para reconocer su tipo real."""

    def leer(self, origen_id: str) -> bytes:
        """Contenido completo de un archivo."""

    def reubicar(self, origen_id: str, destino: str, metadatos: Dict[str, str]) -> str:
        """Deja el archivo en su ubicación de la taxonomía, con sus metadatos.

        Debe poder repetirse sin efectos adversos y lanzar `ErrorTransitorio`
        cuando conviene reintentar. Devuelve el identificador del archivo en destino.
        """
