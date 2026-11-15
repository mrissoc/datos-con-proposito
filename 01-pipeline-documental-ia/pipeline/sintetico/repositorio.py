"""Convierte los planos y documentos ficticios en un repositorio heredado y desordenado.

Reproduce a propósito los problemas habituales: cada proyecto nombra los archivos
a su manera, hay copias con otro nombre en carpetas de respaldo, versiones
editadas sin cambiar la revisión, archivos temporales de CAD, extensiones mal
escritas y archivos sin extensión.
"""

from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

from ..config import Taxonomia
from .mundo import Documento, Plano, crear_documentos, crear_planos

# Primeros bytes de cada tipo real de archivo.
FIRMA = {
    "dwg": b"AC1027\x00\x00\x00\x00\x00\x00",
    "dxf": b"  0\nSECTION\n  2\nHEADER\n",
    "pdf": b"%PDF-1.7\n",
    "docx": b"PK\x03\x04\x14\x00\x06\x00",
    "xlsx": b"PK\x03\x04\x14\x00\x06\x00",
    "zip": b"PK\x03\x04\x0a\x00\x00\x00",
    "doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    "xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    "jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF",
    "tif": b"II*\x00\x08\x00\x00\x00",
    "otro": b"",
}
TAMANO_MEDIANO = {"dwg": 380_000, "dxf": 900_000, "pdf": 260_000, "docx": 140_000, "xlsx": 90_000, "doc": 180_000,
                  "xls": 110_000, "jpg": 1_400_000, "tif": 4_000_000, "zip": 12_000_000, "otro": 6_000}

LETRA = {"CIV": "C", "EST": "S", "HID": "H", "MEC": "M", "ELE": "E", "INS": "I", "ARQ": "A", "TOP": "T"}
ALIAS = {"CIV": "CI", "EST": "ES", "HID": "HI", "MEC": "ME", "ELE": "EL", "INS": "IN", "ARQ": "AR", "TOP": "TO"}
ESTILO_CAJETIN = {"moderna": "A", "guiones": "C", "letra": "C", "contratista": "B"}

CARPETAS_COPIA = ["Respaldo/disco antiguo", "Respaldo/2019-03 servidor", "Copia de planos (2)", "Varios/para revisar",
                  "Usuarios/usuario1/Escritorio", "Backup proyectos/old"]
NOMBRES_CAOTICOS = ["Plano{n}", "Dibujo{n}", "plano final {n}", "copia de planta {n}", "para imprimir {n}",
                    "Layout{n}", "SIN NOMBRE {n}", "nuevo {n}", "plano corregido OK {n}", "lamina {n}"]
# El proyecto PRY-009 se guardó en una carpeta que no lleva su código.
CARPETA_SIN_CODIGO = {"PRY-009": "Proyectos/2011/Ampliacion planta norte"}


@dataclass(frozen=True)
class DatosCajetin:
    """Lo que va escrito en el cajetín de un plano."""
    proyecto: str
    nombre_proyecto: str
    titulo: str
    subtitulo: str
    codigo_impreso: str
    hoja: int
    hojas: int
    revision: str
    revisiones: Tuple[str, ...]
    fecha: date
    escala: str
    estilo: str                         # A: horizontal, B: franja vertical, C: antiguo en inglés
    semilla: int


@dataclass(frozen=True)
class ArchivoPlan:
    ruta: str
    nombre: str
    contenido: str                      # identifica el contenido: las copias exactas lo comparten
    tipo_real: str
    tamano: int                         # tamaño verosímil, usado en las pruebas de escala
    modificado: datetime
    verdad: dict
    cajetin: Optional[DatosCajetin] = None   # presente cuando el archivo es un DXF con cajetín

    @property
    def origen_id(self) -> str:
        return f"{self.ruta}/{self.nombre}" if self.ruta else self.nombre


def extensiones_raras() -> List[Tuple[str, str]]:
    """Extensiones poco comunes y el tipo real del archivo: cientos de extensiones distintas."""
    raras = [
        ("dwg1", "dwg"), ("dwg_old", "dwg"), ("dw_", "dwg"), ("bak2", "dwg"), ("dwt", "dwg"), ("dws", "dwg"),
        ("pdf1", "pdf"), ("pd_", "pdf"), ("xlsx_", "xlsx"), ("doc_viejo", "doc"), ("msg", "doc"), ("mpp", "doc"),
        ("vsd", "doc"), ("kmz", "zip"), ("plt", "otro"), ("ctb", "otro"), ("stb", "otro"), ("pc3", "otro"),
        ("shx", "otro"), ("lin", "otro"), ("pat", "otro"), ("lsp", "otro"), ("mnu", "otro"), ("dwf", "otro"),
        ("dwfx", "zip"), ("rvt", "doc"), ("ifc", "otro"), ("nwd", "otro"), ("skp", "otro"), ("3dm", "otro"),
        ("stp", "otro"), ("igs", "otro"), ("sat", "otro"), ("stl", "otro"), ("shp", "otro"), ("dbf", "otro"),
        ("prj", "otro"), ("kml", "otro"), ("gpx", "otro"), ("xml", "otro"), ("json", "otro"), ("html", "otro"),
        ("cfg", "otro"), ("exe", "otro"), ("dll", "otro"), ("bat", "otro"), ("mp4", "otro"), ("avi", "otro"),
        ("wav", "otro"), ("mp3", "otro"), ("psd", "otro"), ("cdr", "otro"), ("ai", "pdf"), ("eps", "otro"),
        ("heic", "otro"), ("raw", "otro"), ("tga", "otro"), ("pub", "doc"), ("one", "otro"), ("pst", "otro"),
        ("iso", "otro"), ("gho", "otro"), ("bkf", "otro"), ("odg", "zip"), ("wmf", "otro"), ("emf", "otro"),
    ]
    # Fechas y versiones escritas con puntos: lo que queda tras el último punto no es una extensión.
    raras += [(str(anio), "pdf" if anio % 2 else "doc") for anio in range(1998, 2026)]
    raras += [(sufijo, "dwg") for sufijo in ["2 final", "1 ok", "3 definitivo", "0 revisado", "1 corregido",
                                             "2 para aprobar", "4 as built", "1 obra", "2 emitido", "5 final final"]]
    # Archivos de trazado numerados, uno por lámina.
    raras += [(f"{numero:03d}", "otro") for numero in range(1, 221)]
    return raras


class Generador:
    """Genera los archivos de forma determinista: la misma semilla produce el mismo repositorio."""

    def __init__(self, taxonomia: Taxonomia, semilla: int = 7):
        self.taxonomia = taxonomia
        self.rng = random.Random(semilla)
        self.semilla = semilla
        self.usados: Set[str] = set()
        self.carpetas_cad: Set[str] = set()

    # --------------------------------------------------------------- Público

    def archivos(self, planos: int = 320, documentos: int = 140, raros: int = 60,
                 repeticiones: int = 1) -> Iterator[ArchivoPlan]:
        """Produce todos los archivos del repositorio, uno por uno."""
        for plano in crear_planos(self.rng, self.taxonomia, planos):
            yield from self._de_plano(plano)
        for documento in crear_documentos(self.rng, self.taxonomia, documentos):
            yield from self._de_documento(documento)
        yield from self._raros(raros, repeticiones)
        yield from self._de_sistema()

    # --------------------------------------------------------------- Planos

    def _de_plano(self, plano: Plano) -> Iterator[ArchivoPlan]:
        rng = self.rng
        proyecto = self.taxonomia.proyectos[plano.proyecto]
        carpeta = self._carpeta_plano(plano)
        caotico = rng.random() < 0.08
        inicio = datetime(proyecto["anio"], 1, 15, tzinfo=timezone.utc) + timedelta(days=rng.randint(0, 150))
        for hoja in range(1, plano.hojas + 1):
            for indice, revision in enumerate(plano.revisiones):
                fecha = inicio + timedelta(days=60 * indice + rng.randint(0, 20), hours=rng.randint(8, 18))
                verdad = {
                    "clase": "plano", "proyecto": plano.proyecto, "disciplina": plano.disciplina,
                    "tipo": plano.tipo, "numero": f"{plano.numero:04d}", "hoja": f"{hoja:02d}",
                    "revision": revision, "titulo": plano.titulo, "convencion": plano.convencion,
                    "nombre_informativo": not caotico, "copia": False,
                }
                if caotico:
                    base = rng.choice(NOMBRES_CAOTICOS).format(n=rng.randint(1, 60))
                    destino = rng.choice([carpeta, "Varios/sin clasificar", "Escaneos/planos"])
                else:
                    base = self._nombre_plano(plano, hoja, revision)
                    destino = carpeta
                # Los planos que el nombre no alcanza a describir se generan como DXF
                # reales, para que la etapa de visión pueda leer su cajetín.
                requiere_lectura = caotico or plano.convencion == "letra"
                extension = "dxf" if requiere_lectura or rng.random() < 0.15 else "dwg"
                identidad = f"plano|{plano.proyecto}|{plano.disciplina}|{plano.numero}|{hoja}|{revision}"
                cajetin = self._cajetin(plano, hoja, revision, fecha) if extension == "dxf" else None
                nativo = self._archivo(destino, f"{base}.{extension}", f"{identidad}|{extension}",
                                       extension, fecha, verdad, cajetin)
                self.carpetas_cad.add(nativo.ruta)
                yield nativo
                yield from self._copias(nativo)
                yield from self._temporales_cad(nativo)

                ultima = indice == len(plano.revisiones) - 1
                if ultima and not caotico and rng.random() < 0.6:
                    pdf = self._archivo(destino, f"{base}.pdf", f"{identidad}|pdf", "pdf",
                                        fecha + timedelta(hours=2), verdad)
                    yield pdf
                    yield from self._copias(pdf)
                if not caotico and rng.random() < 0.015:
                    # Alguien editó el plano sin emitir una nueva revisión.
                    editado = replace(cajetin, semilla=cajetin.semilla + 1) if cajetin else None
                    yield self._archivo(f"{destino}/modificado sin emitir", f"{base}.{extension}",
                                        f"{identidad}|{extension}|editado", extension,
                                        fecha + timedelta(days=120), {**verdad, "conflicto": True}, editado)

    def _carpeta_plano(self, plano: Plano) -> str:
        raiz = self._carpeta_proyecto(plano.proyecto)
        if plano.convencion == "moderna":
            return f"{raiz}/Planos/{plano.disciplina}"
        if plano.convencion == "guiones":
            return f"{raiz}/PLANOS/{ALIAS[plano.disciplina]}"
        if plano.convencion == "contratista":
            return f"{raiz}/Contratista {plano.contratista}/Entrega final"
        return f"{raiz}/planos"

    def _carpeta_proyecto(self, codigo: str) -> str:
        if codigo in CARPETA_SIN_CODIGO:
            return CARPETA_SIN_CODIGO[codigo]
        proyecto = self.taxonomia.proyectos[codigo]
        return f"Proyectos/{proyecto['anio']}/{codigo} {proyecto['nombre']}"

    def _nombre_plano(self, plano: Plano, hoja: int, revision: str) -> str:
        numero = f"{plano.numero:04d}"
        if plano.convencion == "moderna":
            base = f"{plano.proyecto}-{plano.disciplina}-{plano.tipo}-{numero}-H{hoja:02d}-Rev{revision}"
            return f"{base} {plano.titulo}" if self.rng.random() < 0.6 else base
        if plano.convencion == "guiones":
            base = f"{plano.proyecto.replace('-', '')}_{ALIAS[plano.disciplina]}_{numero}_{hoja}_{revision}"
            return f"{base} {plano.titulo}" if self.rng.random() < 0.7 else base
        if plano.convencion == "contratista":
            base = f"{plano.contratista}-{numero}-{plano.disciplina}-R{revision}"
            if plano.hojas > 1:
                base += f"-L{hoja}"
            return f"{base} {plano.titulo}" if self.rng.random() < 0.5 else base
        letra = LETRA[plano.disciplina]
        return f"{letra}-{numero}-{hoja}-{revision}" if plano.hojas > 1 else f"{letra}-{numero}-{revision}"

    def _cajetin(self, plano: Plano, hoja: int, revision: str, fecha: datetime) -> DatosCajetin:
        numero = f"{plano.numero:04d}"
        if plano.convencion == "moderna":
            codigo = f"{plano.proyecto}-{plano.disciplina}-{plano.tipo}-{numero}"
        elif plano.convencion == "guiones":
            codigo = f"{plano.proyecto.replace('-', '')}_{ALIAS[plano.disciplina]}_{numero}"
        elif plano.convencion == "contratista":
            codigo = f"{plano.contratista}-{numero}-{plano.disciplina}"
        else:
            codigo = f"{LETRA[plano.disciplina]}-{numero}"
        return DatosCajetin(
            proyecto=plano.proyecto, nombre_proyecto=self.taxonomia.proyectos[plano.proyecto]["nombre"],
            titulo=plano.titulo, subtitulo=plano.subtitulo, codigo_impreso=codigo, hoja=hoja, hojas=plano.hojas,
            revision=revision, revisiones=plano.revisiones[:plano.revisiones.index(revision) + 1],
            fecha=fecha.date(), escala=self.rng.choice(["1:50", "1:100", "1:200", "1:500", "S/E"]),
            estilo=ESTILO_CAJETIN[plano.convencion], semilla=self.rng.randint(0, 10 ** 9))

    # ----------------------------------------------------------- Documentos

    def _de_documento(self, documento: Documento) -> Iterator[ArchivoPlan]:
        rng = self.rng
        proyecto = self.taxonomia.proyectos[documento.proyecto]
        carpeta = f"{self._carpeta_proyecto(documento.proyecto)}/Documentos"
        fecha = datetime(proyecto["anio"], 3, 1, tzinfo=timezone.utc) + timedelta(days=rng.randint(0, 250))
        for indice, revision in enumerate(documento.revisiones):
            con_codigo = documento.convencion in ("moderna", "guiones")
            if con_codigo:
                base = f"{documento.proyecto}-{documento.tipo}-{documento.numero:03d} {documento.titulo} Rev{revision}"
                extension = rng.choices(["pdf", "docx", "xlsx"], weights=[60, 30, 10])[0]
            else:
                # Los proyectos antiguos nombraban los documentos solo por su título.
                base = f"{documento.titulo} rev {revision}"
                extension = rng.choice(["doc", "pdf", "xls"])
            verdad = {
                "clase": "documento", "proyecto": documento.proyecto, "tipo": documento.tipo,
                "numero": f"{documento.numero:03d}", "revision": revision, "titulo": documento.titulo,
                "convencion": documento.convencion, "nombre_informativo": con_codigo, "copia": False,
            }
            identidad = f"doc|{documento.proyecto}|{documento.tipo}|{documento.numero}|{revision}|{extension}"
            archivo = self._archivo(carpeta, f"{base}.{extension}", identidad, extension,
                                    fecha + timedelta(days=40 * indice), verdad)
            yield archivo
            yield from self._copias(archivo, probabilidad=0.2)
            if extension == "docx" and rng.random() < 0.08:
                yield self._archivo(carpeta, f"~${base[2:]}.docx", f"{identidad}|bloqueo", "otro", archivo.modificado,
                                    {"clase": "basura"})

    # -------------------------------------------------- Copias y temporales

    def _copias(self, original: ArchivoPlan, probabilidad: float = 0.28) -> Iterator[ArchivoPlan]:
        rng = self.rng
        if rng.random() >= probabilidad:
            return
        base, punto, extension = original.nombre.rpartition(".")
        for _ in range(rng.choices([1, 2], weights=[75, 25])[0]):
            variante = rng.choices(["igual", "copia_de", "sufijo"], weights=[60, 25, 15])[0]
            if variante == "igual":
                carpeta = f"{rng.choice(CARPETAS_COPIA)}/{original.ruta.rsplit('/', 1)[-1]}"
                nombre = original.nombre
            elif variante == "copia_de":
                carpeta = rng.choice([original.ruta, rng.choice(CARPETAS_COPIA)])
                nombre = f"Copia de {original.nombre}"
            else:
                carpeta = original.ruta
                nombre = f"{base}{rng.choice([' (1)', ' - copia', '_final', ' (2)'])}{punto}{extension}"
            yield self._archivo(carpeta, nombre, original.contenido, original.tipo_real,
                                original.modificado + timedelta(days=rng.randint(0, 400)),
                                {**original.verdad, "copia": True}, original.cajetin)

    def _temporales_cad(self, nativo: ArchivoPlan) -> Iterator[ArchivoPlan]:
        rng = self.rng
        base = nativo.nombre.rpartition(".")[0]
        if rng.random() < 0.12:
            yield self._archivo(nativo.ruta, f"{base}.bak", f"{nativo.contenido}|bak", "dwg",
                                nativo.modificado - timedelta(hours=3), {"clase": "basura"})
        if rng.random() < 0.04:
            for extension in ("dwl", "dwl2"):
                yield self._archivo(nativo.ruta, f"{base}.{extension}", f"{nativo.contenido}|{extension}", "otro",
                                    nativo.modificado, {"clase": "basura"})

    def _de_sistema(self) -> Iterator[ArchivoPlan]:
        """Archivos que deja el sistema operativo o el trazador en las carpetas de trabajo."""
        rng = self.rng
        fecha = datetime(2020, 6, 1, tzinfo=timezone.utc)
        for carpeta in sorted(self.carpetas_cad):
            if rng.random() < 0.3:
                yield self._archivo(carpeta, "Thumbs.db", f"thumbs|{carpeta}", "otro", fecha, {"clase": "basura"})
            if rng.random() < 0.12:
                yield self._archivo(carpeta, "plot.log", f"plot|{carpeta}", "otro", fecha, {"clase": "basura"})

    # ------------------------------------------------- Extensiones extrañas

    def _raros(self, cantidad: int, repeticiones: int) -> Iterator[ArchivoPlan]:
        rng = self.rng
        carpetas = ["Varios", "Varios/sin clasificar", "Varios/para revisar", "Escaneos", "Respaldo/disco antiguo",
                    "Usuarios/usuario1/Escritorio"] + [self._carpeta_proyecto(codigo) for codigo in sorted(self.taxonomia.proyectos)]
        temas = ["Informe visita", "Acta reunion", "Plano sala bombas", "Listado materiales", "Minuta obra",
                 "PLANO_ELECTRICO", "Detalle camara", "Presupuesto", "Cubicacion", "Levantamiento"]
        for indice, (extension, tipo_real) in enumerate(extensiones_raras()[:cantidad]):
            for copia in range(rng.randint(1, repeticiones)):
                tema = rng.choice(temas)
                if extension.isdigit() and len(extension) == 4:
                    nombre = f"{tema} {rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{extension}"
                elif " " in extension:
                    nombre = f"{tema} v{rng.randint(1, 3)}.{extension}"
                else:
                    nombre = f"{tema} {indice + copia}.{extension}"
                fecha = datetime(rng.randint(2008, 2025), rng.randint(1, 12), rng.randint(1, 28), tzinfo=timezone.utc)
                yield self._archivo(rng.choice(carpetas), nombre, f"raro|{indice}|{copia}", tipo_real, fecha,
                                    {"clase": "otro", "tipo_real": tipo_real})
        # Archivos guardados sin extensión.
        for indice in range(max(3, cantidad // 12)):
            tipo_real = rng.choice(["dwg", "pdf", "doc", "otro"])
            fecha = datetime(rng.randint(2008, 2025), rng.randint(1, 12), rng.randint(1, 28), tzinfo=timezone.utc)
            yield self._archivo(rng.choice(carpetas), f"{rng.choice(temas)} {indice + 1}", f"sin_ext|{indice}",
                                tipo_real, fecha, {"clase": "otro", "tipo_real": tipo_real})

    # --------------------------------------------------------------- Interno

    def _archivo(self, carpeta: str, nombre: str, contenido: str, tipo_real: str, fecha: datetime, verdad: dict,
                 cajetin: Optional[DatosCajetin] = None) -> ArchivoPlan:
        """Crea el registro de un archivo, cuidando que su ruta no se repita."""
        base, punto, extension = nombre.rpartition(".")
        if not punto:
            base, extension = nombre, ""
        intento, final = 1, nombre
        while f"{carpeta}/{final}".lower() in self.usados:
            intento += 1
            final = f"{base} [{intento}]{punto}{extension}"
        self.usados.add(f"{carpeta}/{final}".lower())
        mediano = TAMANO_MEDIANO.get(tipo_real, 6_000)
        tamano = max(120, int(self.rng.lognormvariate(0, 0.6) * mediano))
        return ArchivoPlan(carpeta, final, contenido, tipo_real, tamano, fecha, verdad, cajetin)


def contenido_de_relleno(archivo: ArchivoPlan) -> bytes:
    """Bytes de un archivo que no es un DXF real: su firma y un relleno determinista."""
    semilla = hashlib.sha256(archivo.contenido.encode("utf-8")).digest()
    return FIRMA.get(archivo.tipo_real, b"") + semilla * (4 + semilla[0] % 12)


def materializar(archivos: Iterable[ArchivoPlan], destino: Path) -> Dict[str, dict]:
    """Escribe los archivos en disco y devuelve la verdad de cada uno, por ruta."""
    from .planos_dxf import crear_dxf

    verdad: Dict[str, dict] = {}
    dxf_generados: Dict[str, bytes] = {}
    for archivo in archivos:
        ruta = destino / archivo.ruta / archivo.nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        if archivo.cajetin is not None:
            if archivo.contenido not in dxf_generados:
                dxf_generados[archivo.contenido] = crear_dxf(archivo.cajetin)
            datos = dxf_generados[archivo.contenido]
        else:
            datos = contenido_de_relleno(archivo)
        ruta.write_bytes(datos)
        marca = archivo.modificado.timestamp()
        os.utime(ruta, (marca, marca))
        verdad[archivo.origen_id] = {**archivo.verdad, "contenido": archivo.contenido}
    return verdad
