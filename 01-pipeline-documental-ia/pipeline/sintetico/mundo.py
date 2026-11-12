"""Los planos y documentos que existen en el mundo ficticio, antes de convertirse en archivos."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..config import Taxonomia

# Cada proyecto usó la convención de nombres de su época.
CONVENCION = {
    "PRY-006": "letra", "PRY-009": "letra",
    "PRY-014": "guiones", "PRY-017": "guiones", "PRY-019": "guiones",
    "PRY-021": "moderna", "PRY-024": "moderna", "PRY-027": "moderna", "PRY-030": "moderna", "PRY-033": "moderna",
}
# Proyectos en que parte de los planos llegó en entregas de un contratista.
CONTRATISTA = {"PRY-017": "CTX", "PRY-024": "ZNT"}

# Rango de numeración de cada disciplina dentro de un proyecto.
BASE_NUMERO = {"CIV": 100, "EST": 200, "HID": 300, "MEC": 400, "ELE": 500, "INS": 600, "ARQ": 700, "TOP": 800}

TITULOS: Dict[Tuple[str, str], List[str]] = {
    ("CIV", "PG"): ["Planta general de emplazamiento", "Disposición general de obras"],
    ("CIV", "PL"): ["Planta de excavaciones", "Planta de pavimentos y accesos", "Planta de drenajes"],
    ("CIV", "CE"): ["Cortes y perfiles de terreno", "Perfil longitudinal de camino de acceso"],
    ("CIV", "DE"): ["Detalles de cámaras y sumideros", "Detalles de cierros perimetrales"],
    ("EST", "PL"): ["Planta de fundaciones", "Planta de losa de fondo", "Planta de estructura de techumbre"],
    ("EST", "CE"): ["Elevaciones de muros", "Cortes de estructura de hormigón"],
    ("EST", "DE"): ["Detalles de armadura", "Detalles de anclajes y pernos"],
    ("HID", "PL"): ["Planta de tuberías", "Planta de interconexiones"],
    ("HID", "PI"): ["P&ID sistema de impulsión", "P&ID sistema de cloración", "P&ID llenado y vaciado"],
    ("HID", "IS"): ["Isométrico manifold de descarga", "Isométrico línea de succión"],
    ("HID", "DE"): ["Detalles de piezas especiales", "Detalles de soportes de tubería"],
    ("MEC", "PL"): ["Planta de disposición de equipos", "Planta sala de bombas"],
    ("MEC", "CE"): ["Cortes sala de bombas", "Elevaciones de equipos"],
    ("MEC", "DE"): ["Detalles de montaje de bombas", "Detalles de puente grúa"],
    ("ELE", "DU"): ["Diagrama unilineal general", "Diagrama unilineal centro de control de motores"],
    ("ELE", "PL"): ["Planta de canalizaciones eléctricas", "Planta de alumbrado", "Planta de malla de tierra"],
    ("ELE", "DE"): ["Detalles de montaje de tableros", "Detalles de canalizaciones"],
    ("INS", "PI"): ["P&ID instrumentación de proceso"],
    ("INS", "EC"): ["Esquema de control de bombas", "Lazos de control de nivel", "Conexionado de tablero de control"],
    ("INS", "PL"): ["Planta de ubicación de instrumentos"],
    ("ARQ", "PL"): ["Planta de arquitectura edificio de control", "Planta de cubiertas"],
    ("ARQ", "CE"): ["Elevaciones edificio de control", "Cortes de arquitectura"],
    ("TOP", "PG"): ["Levantamiento topográfico general"],
}
AREAS = ["Sala de bombas", "Edificio de control", "Sala eléctrica", "Patio de maniobras", "Cámara de válvulas",
         "Sector norte", "Sector sur", "Recinto de cloración", "Acceso principal"]

TITULOS_DOCUMENTO = {
    "ET": ["Especificación técnica de bombas", "Especificación técnica de válvulas", "Especificación técnica de tableros"],
    "MC": ["Memoria de cálculo estructural", "Memoria de cálculo hidráulico", "Memoria de cálculo eléctrico"],
    "INF": ["Informe de mecánica de suelos", "Informe de puesta en marcha", "Informe de inspección"],
    "MAN": ["Manual de operación", "Manual de mantenimiento de bombas"],
    "PRO": ["Procedimiento de pruebas hidráulicas", "Procedimiento de bloqueo de equipos"],
    "ACT": ["Acta de recepción provisoria", "Acta de reunión de coordinación"],
}

REVISIONES = {
    "moderna": [("A",), ("A", "B"), ("A", "B", "0"), ("A", "B", "0", "1")],
    "guiones": [("A",), ("A", "B"), ("A", "B", "C")],
    "letra": [("A",), ("A", "B"), ("A", "B", "C")],
    "contratista": [("0",), ("0", "1"), ("0", "1", "2")],
}


@dataclass(frozen=True)
class Plano:
    proyecto: str
    disciplina: str
    tipo: str
    numero: int
    titulo: str
    subtitulo: str
    hojas: int
    revisiones: Tuple[str, ...]
    convencion: str                      # moderna | guiones | letra | contratista
    contratista: Optional[str] = None


@dataclass(frozen=True)
class Documento:
    proyecto: str
    tipo: str
    numero: int
    titulo: str
    revisiones: Tuple[str, ...]
    convencion: str


def crear_planos(rng: random.Random, taxonomia: Taxonomia, cantidad: int) -> List[Plano]:
    """Reparte la cantidad pedida entre los proyectos y numera por disciplina."""
    combinaciones = sorted(TITULOS)
    proyectos = sorted(taxonomia.proyectos)
    siguiente: Dict[Tuple[str, str], int] = {}
    planos = []
    for indice in range(cantidad):
        proyecto = proyectos[indice % len(proyectos)]
        disciplina, tipo = rng.choice(combinaciones)
        clave = (proyecto, disciplina)
        siguiente[clave] = siguiente.get(clave, BASE_NUMERO[disciplina]) + 1
        convencion, contratista = CONVENCION[proyecto], None
        if proyecto in CONTRATISTA and rng.random() < 0.4:
            convencion, contratista = "contratista", CONTRATISTA[proyecto]
        planos.append(Plano(
            proyecto=proyecto, disciplina=disciplina, tipo=tipo, numero=siguiente[clave],
            titulo=rng.choice(TITULOS[(disciplina, tipo)]), subtitulo=rng.choice(AREAS),
            hojas=rng.choices([1, 2, 3], weights=[75, 20, 5])[0],
            revisiones=rng.choice(REVISIONES[convencion]),
            convencion=convencion, contratista=contratista))
    return planos


def crear_documentos(rng: random.Random, taxonomia: Taxonomia, cantidad: int) -> List[Documento]:
    proyectos = sorted(taxonomia.proyectos)
    tipos = sorted(TITULOS_DOCUMENTO)
    siguiente: Dict[Tuple[str, str], int] = {}
    documentos = []
    for indice in range(cantidad):
        proyecto = proyectos[indice % len(proyectos)]
        tipo = rng.choice(tipos)
        clave = (proyecto, tipo)
        siguiente[clave] = siguiente.get(clave, 0) + 1
        documentos.append(Documento(
            proyecto=proyecto, tipo=tipo, numero=siguiente[clave],
            titulo=rng.choice(TITULOS_DOCUMENTO[tipo]),
            revisiones=rng.choice([("A",), ("A", "B"), ("0",), ("A", "0")]),
            convencion=CONVENCION[proyecto]))
    return documentos
