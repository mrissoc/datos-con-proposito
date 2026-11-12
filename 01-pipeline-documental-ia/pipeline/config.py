"""Carga de la taxonomía documental y de las reglas de perfilado.

Ambas viven en archivos JSON de `config/` para que se puedan ajustar sin tocar
el código: la taxonomía la define quien administra la biblioteca.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CONFIG = RAIZ / "config"


def sin_acentos(texto: str) -> str:
    """Minúsculas sin tildes, para comparar textos escritos de formas distintas."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


@dataclass
class Taxonomia:
    disciplinas: Dict[str, str]                 # código -> nombre
    alias_disciplina: Dict[str, str]            # alias en mayúsculas -> código
    tipos_plano: Dict[str, str]
    palabras_tipo: List[Tuple[str, str]]        # (palabra sin acentos, código de tipo)
    tipos_documento: Dict[str, str]
    proyectos: Dict[str, dict]                  # código -> nombre, año e instalación

    @classmethod
    def cargar(cls, ruta: Optional[Path] = None) -> "Taxonomia":
        datos = json.loads((ruta or RUTA_CONFIG / "taxonomia.json").read_text(encoding="utf-8"))
        alias = {}
        for disciplina in datos["disciplinas"]:
            for nombre_alias in disciplina["alias"]:
                alias[nombre_alias.upper()] = disciplina["codigo"]
        palabras = [(sin_acentos(palabra), tipo["codigo"])
                    for tipo in datos["tipos_plano"] for palabra in tipo["palabras"]]
        return cls(
            disciplinas={d["codigo"]: d["nombre"] for d in datos["disciplinas"]},
            alias_disciplina=alias,
            tipos_plano={t["codigo"]: t["nombre"] for t in datos["tipos_plano"]},
            palabras_tipo=palabras,
            tipos_documento={t["codigo"]: t["nombre"] for t in datos["tipos_documento"]},
            proyectos={p["codigo"]: p for p in datos["proyectos"]},
        )

    def disciplina_de(self, alias: Optional[str]) -> Optional[str]:
        """Código de disciplina a partir de cualquiera de sus abreviaturas."""
        return self.alias_disciplina.get(alias.upper()) if alias else None

    def tipo_por_titulo(self, titulo: Optional[str]) -> Optional[str]:
        """Deduce el tipo de plano por las palabras del título.

        Manda la palabra que aparece primero ("Detalles de anclaje en planta" es un
        plano de detalles); ante un empate gana la expresión más específica
        ("planta general" antes que "planta").
        """
        if not titulo:
            return None
        texto = sin_acentos(titulo)
        mejor: Optional[Tuple[int, int, str]] = None
        for palabra, codigo in self.palabras_tipo:
            coincidencia = re.search(r"(?<![a-z0-9])" + re.escape(palabra), texto)
            if coincidencia:
                candidato = (coincidencia.start(), -len(palabra), codigo)
                if mejor is None or candidato < mejor:
                    mejor = candidato
        return mejor[2] if mejor else None


@dataclass
class Reglas:
    equivalencias: Dict[str, str]
    categoria_de: Dict[str, str]                # extensión -> categoría
    alcance: Dict[str, str]                     # categoría -> migrar | revisar | descartar
    firmas: List[Tuple[bytes, str]]             # (bytes iniciales, tipo de archivo)

    @classmethod
    def cargar(cls, ruta: Optional[Path] = None) -> "Reglas":
        datos = json.loads((ruta or RUTA_CONFIG / "extensiones.json").read_text(encoding="utf-8"))
        categoria_de = {ext: categoria for categoria, lista in datos["categorias"].items() for ext in lista}
        firmas = [(bytes.fromhex(f["hex"]), f["tipo"]) for f in datos["firmas"]]
        return cls(datos["equivalencias"], categoria_de, datos["alcance"], firmas)

    def normalizar(self, extension: str) -> str:
        extension = extension.lower().strip()
        return self.equivalencias.get(extension, extension)

    def categoria(self, extension: str) -> str:
        return self.categoria_de.get(extension, "otro")

    def tipo_por_firma(self, inicio: bytes) -> Optional[str]:
        """Reconoce el tipo real de un archivo por sus primeros bytes."""
        for firma, tipo in self.firmas:
            if inicio.startswith(firma):
                return tipo
        # Un DXF en texto comienza con el código de grupo 0 y la palabra SECTION.
        if re.match(rb"\s*0\s*\r?\n\s*SECTION", inicio):
            return "dxf"
        return None
