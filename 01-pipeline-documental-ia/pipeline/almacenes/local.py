"""Almacén sobre carpetas del disco."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, Optional, Union

from . import ArchivoOrigen

BLOQUE = 1024 * 1024


def huella(ruta: Path) -> str:
    """SHA-256 del contenido, leyendo por bloques para no cargar el archivo en memoria."""
    sha = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(BLOQUE), b""):
            sha.update(bloque)
    return sha.hexdigest()


class AlmacenLocal:
    """Lee desde `origen` y deja los archivos migrados en `destino`.

    La migración copia en vez de mover: el origen queda intacto hasta que alguien
    verifique el resultado. Los metadatos se guardan junto a cada archivo en un
    `.json`, que cumple el papel de las columnas de una biblioteca documental.
    """

    def __init__(self, origen: Union[Path, str], destino: Optional[Union[Path, str]] = None):
        self.origen = Path(origen)
        self.destino = Path(destino) if destino else None

    def listar(self) -> Iterator[ArchivoOrigen]:
        for carpeta, subcarpetas, nombres in os.walk(self.origen):
            subcarpetas.sort()
            relativa = Path(carpeta).relative_to(self.origen).as_posix()
            relativa = "" if relativa == "." else relativa
            for nombre in sorted(nombres):
                ruta = Path(carpeta) / nombre
                estado = ruta.stat()
                modificado = datetime.fromtimestamp(estado.st_mtime, tz=timezone.utc)
                yield ArchivoOrigen(
                    origen_id=f"{relativa}/{nombre}" if relativa else nombre,
                    ruta=relativa,
                    nombre=nombre,
                    tamano=estado.st_size,
                    modificado=modificado.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    hash=huella(ruta),
                )

    def leer_inicio(self, origen_id: str, cantidad: int = 32) -> bytes:
        with (self.origen / origen_id).open("rb") as archivo:
            return archivo.read(cantidad)

    def leer(self, origen_id: str) -> bytes:
        return (self.origen / origen_id).read_bytes()

    def reubicar(self, origen_id: str, destino: str, metadatos: Dict[str, str]) -> str:
        if self.destino is None:
            raise ValueError("El almacén no tiene carpeta de destino")
        fuente = self.origen / origen_id
        final = self.destino / destino
        final.parent.mkdir(parents=True, exist_ok=True)
        # Se copia a un nombre temporal y se renombra al final: una interrupción
        # nunca deja un archivo a medias con el nombre definitivo.
        temporal = final.with_name(final.name + ".parcial")
        shutil.copy2(fuente, temporal)
        if temporal.stat().st_size != fuente.stat().st_size:
            temporal.unlink()
            raise OSError(f"La copia de {origen_id} quedó incompleta")
        os.replace(temporal, final)
        final.with_name(final.name + ".json").write_text(
            json.dumps(metadatos, ensure_ascii=False, indent=2), encoding="utf-8")
        return destino
