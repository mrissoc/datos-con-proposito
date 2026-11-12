"""Almacén sintético: entrega metadatos generados, sin archivos en disco.

Sirve para medir el pipeline a la escala de un repositorio real (cientos de
miles de archivos) y para ejercitar los reintentos de la migración simulando
los límites de solicitudes de un servicio en la nube.
"""

from __future__ import annotations

import hashlib
from typing import Dict, Iterable, Iterator

from ..sintetico.repositorio import FIRMA, ArchivoPlan
from . import ArchivoOrigen, ErrorTransitorio


class AlmacenSintetico:
    def __init__(self, archivos: Iterable[ArchivoPlan], limite_cada: int = 0):
        """`limite_cada`: una de cada tantas reubicaciones responde "reintente más tarde"."""
        self._archivos = archivos
        self._tipos: Dict[str, str] = {}
        self._limite_cada = limite_cada
        self._llamadas = 0
        self.reubicados: Dict[str, str] = {}

    def listar(self) -> Iterator[ArchivoOrigen]:
        for archivo in self._archivos:
            self._tipos[archivo.origen_id] = archivo.tipo_real
            yield ArchivoOrigen(
                origen_id=archivo.origen_id, ruta=archivo.ruta, nombre=archivo.nombre, tamano=archivo.tamano,
                modificado=archivo.modificado.strftime("%Y-%m-%dT%H:%M:%SZ"),
                hash=hashlib.sha256(archivo.contenido.encode("utf-8")).hexdigest())

    def leer_inicio(self, origen_id: str, cantidad: int = 32) -> bytes:
        return FIRMA.get(self._tipos[origen_id], b"")[:cantidad]

    def leer(self, origen_id: str) -> bytes:
        raise NotImplementedError("El almacén sintético no guarda el contenido de los archivos")

    def reubicar(self, origen_id: str, destino: str, metadatos: Dict[str, str]) -> str:
        self._llamadas += 1
        if self._limite_cada and self._llamadas % self._limite_cada == 0:
            raise ErrorTransitorio("Límite de solicitudes simulado", esperar=0)
        self.reubicados[origen_id] = destino
        return destino
