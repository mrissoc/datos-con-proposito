"""Lectura del cajetín de un plano con visión por IA (API de Anthropic).

El modelo recibe la imagen y devuelve los campos del cajetín en un JSON que
cumple un esquema fijo (salidas estructuradas), con las disciplinas, los tipos
de plano y los proyectos limitados a los valores de la taxonomía. Así la
respuesta se puede validar y cargar sin interpretar texto libre.

Estrategia en dos pasadas, para cuidar el costo: primero se envía solo el recorte
de la esquina inferior derecha; si ahí no aparece un cajetín legible, se envía la
lámina completa.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import anthropic

from ..config import Taxonomia
from .dibujo import lamina_completa, recortar_cajetin

MODELO = "claude-opus-5-5"
CAMPOS = ["codigo_plano", "numero", "proyecto", "disciplina", "tipo_plano", "titulo", "subtitulo", "hoja",
          "total_hojas", "revision", "fecha", "escala", "observaciones"]


class ErrorDeConfiguracion(Exception):
    """La API rechazó la solicitud por credenciales, permisos o parámetros: no tiene sentido seguir."""


class LecturaRechazada(Exception):
    """El modelo declinó responder (stop_reason = refusal)."""


@dataclass
class Lectura:
    legible: bool = False
    codigo_plano: Optional[str] = None
    numero: Optional[str] = None
    proyecto: Optional[str] = None
    disciplina: Optional[str] = None
    tipo_plano: Optional[str] = None
    titulo: Optional[str] = None
    subtitulo: Optional[str] = None
    hoja: Optional[str] = None
    total_hojas: Optional[int] = None
    revision: Optional[str] = None
    fecha: Optional[str] = None
    escala: Optional[str] = None
    observaciones: Optional[str] = None
    # Datos de la llamada, para auditoría y control de costos.
    modelo: str = ""
    pasadas: int = 1
    tokens_entrada: int = 0
    tokens_salida: int = 0
    respuestas: List[dict] = field(default_factory=list)

    def informados(self) -> int:
        return sum(getattr(self, campo) is not None for campo in ("numero", "disciplina", "titulo", "revision", "proyecto"))

    def suficiente(self) -> bool:
        """Hay cajetín y trae al menos el número y el título."""
        return self.legible and self.numero is not None and self.titulo is not None


def esquema(taxonomia: Taxonomia) -> dict:
    """Esquema JSON de la respuesta. Todo campo puede venir nulo si no se lee en el plano."""
    def opcional(tipo: dict) -> dict:
        return {"anyOf": [tipo, {"type": "null"}]}

    texto = opcional({"type": "string"})
    entero = opcional({"type": "integer"})
    propiedades = {
        "legible": {"type": "boolean"},
        "codigo_plano": texto,
        "numero": texto,
        "proyecto": opcional({"type": "string", "enum": sorted(taxonomia.proyectos)}),
        "disciplina": opcional({"type": "string", "enum": sorted(taxonomia.disciplinas)}),
        "tipo_plano": opcional({"type": "string", "enum": sorted(taxonomia.tipos_plano)}),
        "titulo": texto,
        "subtitulo": texto,
        "hoja": entero,
        "total_hojas": entero,
        "revision": texto,
        "fecha": texto,
        "escala": texto,
        "observaciones": texto,
    }
    return {"type": "object", "properties": propiedades, "required": list(propiedades), "additionalProperties": False}


def instrucciones(taxonomia: Taxonomia) -> str:
    """Instrucciones del sistema: son fijas para toda la corrida, por lo que se pueden guardar en caché."""
    lista = lambda pares: "\n".join(f"- {codigo}: {nombre}" for codigo, nombre in pares)   # noqa: E731
    proyectos = lista((codigo, f"{p['nombre']} ({p['anio']})") for codigo, p in sorted(taxonomia.proyectos.items()))
    return f"""Lees cajetines (cuadros de rotulación) de planos de ingeniería para catalogarlos en una biblioteca técnica.

Recibirás la imagen de un plano o de la zona donde suele estar su cajetín. Los cajetines vienen de distintas épocas y oficinas: pueden ser horizontales o una franja vertical, y tener los rótulos en español o en inglés (TITLE, DWG No., SHT, REV, DATE, SCALE).

Devuelve los campos del cajetín. Estas reglas importan porque el resultado se carga sin revisión humana previa:

- Transcribe lo que está escrito. Si un dato no aparece o no se lee con seguridad, devuelve null en ese campo: un dato inventado es peor que un dato faltante.
- legible: true solo si la imagen muestra un cajetín con datos del plano. Si la imagen no contiene el cajetín, devuelve legible false y los demás campos en null.
- codigo_plano: el identificador del plano tal como está impreso (por ejemplo "PRY-021-HID-PL-0301", "S-0201" o "CTX-0501-ELE").
- numero: solo el correlativo numérico del plano dentro de ese código, con sus ceros (por ejemplo "0301").
- titulo: el título del plano. subtitulo: el contenido, sector o área, si viene aparte.
- revision: la revisión vigente, que es la de la casilla de revisión; si hay una tabla de revisiones, coincide con la más reciente.
- hoja y total_hojas: de expresiones como "1 DE 2", "1/2" o "1 OF 2".
- fecha: en formato AAAA-MM-DD. Los años de dos dígitos son del siglo XXI.
- disciplina, tipo_plano y proyecto: clasifícalos con los códigos de las listas de abajo, a partir del código del plano, del título y del nombre del proyecto u obra. Si la evidencia no alcanza para decidir, devuelve null.
- observaciones: una frase breve solo si hay algo que una persona deba revisar (texto ilegible, datos contradictorios); si no, null.

Disciplinas:
{lista(sorted(taxonomia.disciplinas.items()))}

En los códigos de plano la disciplina suele ir abreviada: C o CI (civil), S o ES (estructuras), H o HI (hidráulica), M o ME (mecánica), E o EL (eléctrica), I o IN (instrumentación), A o AR (arquitectura), T o TO (topografía).

Tipos de plano:
{lista(sorted(taxonomia.tipos_plano.items()))}

Proyectos:
{proyectos}
"""


class LectorCajetines:
    def __init__(self, taxonomia: Taxonomia, cliente: Optional[anthropic.Anthropic] = None,
                 modelo: Optional[str] = None, esfuerzo: str = "low"):
        # Sin argumentos, el cliente toma las credenciales del entorno (ANTHROPIC_API_KEY o `ant auth login`).
        self.cliente = cliente or anthropic.Anthropic()
        self.modelo = modelo or MODELO
        self.esfuerzo = esfuerzo
        self.taxonomia = taxonomia
        self._esquema = esquema(taxonomia)
        self._instrucciones = instrucciones(taxonomia)

    def leer_plano(self, lamina_png: bytes) -> Lectura:
        """Lee el cajetín de una lámina: primero el recorte y, si no basta, la lámina completa."""
        lectura = self.leer_imagen(recortar_cajetin(lamina_png))
        if lectura.suficiente():
            return lectura
        completa = self.leer_imagen(lamina_completa(lamina_png))
        mejor = completa if completa.informados() >= lectura.informados() else lectura
        mejor.pasadas = 2
        mejor.tokens_entrada = lectura.tokens_entrada + completa.tokens_entrada
        mejor.tokens_salida = lectura.tokens_salida + completa.tokens_salida
        mejor.respuestas = lectura.respuestas + completa.respuestas
        return mejor

    def leer_imagen(self, png: bytes) -> Lectura:
        """Una llamada a la API con una imagen."""
        try:
            respuesta = self.cliente.beta.messages.create(
                model=self.modelo,
                max_tokens=16000,
                # Si un clasificador de seguridad declina la solicitud, la API la repite
                # en el modelo de respaldo que recomienda, dentro de la misma llamada.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": self._instrucciones, "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": self.esfuerzo, "format": {"type": "json_schema", "schema": self._esquema}},
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.standard_b64encode(png).decode("ascii")}},
                    {"type": "text", "text": "Lee el cajetín de este plano."},
                ]}],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError,
                anthropic.NotFoundError) as error:
            raise ErrorDeConfiguracion(f"La API rechazó la solicitud: {error.message}") from error

        if respuesta.stop_reason == "refusal":
            raise LecturaRechazada("El modelo declinó leer esta imagen")
        if respuesta.stop_reason == "max_tokens":
            raise ValueError("La respuesta quedó incompleta por el límite de tokens")
        texto = next((bloque.text for bloque in respuesta.content if bloque.type == "text"), None)
        if texto is None:
            raise ValueError("La respuesta no trae texto")
        lectura = self.normalizar(json.loads(texto))
        lectura.modelo = respuesta.model
        lectura.tokens_entrada = _tokens_de_entrada(respuesta.usage)
        lectura.tokens_salida = respuesta.usage.output_tokens
        return lectura

    def normalizar(self, datos: Dict[str, Any]) -> Lectura:
        """Valida la respuesta contra la taxonomía y la deja en el formato de la base."""
        def limpio(campo: str) -> Optional[str]:
            valor = datos.get(campo)
            return str(valor).strip() or None if valor is not None else None

        def de_lista(campo: str, validos) -> Optional[str]:
            valor = (limpio(campo) or "").upper()
            return valor if valor in validos else None

        numero = re.sub(r"\D", "", limpio("numero") or "")
        titulo = limpio("titulo")
        hoja = datos.get("hoja")
        lectura = Lectura(
            legible=bool(datos.get("legible")),
            codigo_plano=limpio("codigo_plano"),
            numero=numero.zfill(4)[-4:] if numero else None,
            proyecto=de_lista("proyecto", self.taxonomia.proyectos),
            disciplina=de_lista("disciplina", self.taxonomia.disciplinas),
            tipo_plano=de_lista("tipo_plano", self.taxonomia.tipos_plano),
            titulo=_tipo_oracion(titulo),
            subtitulo=limpio("subtitulo"),
            hoja=f"{int(hoja):02d}" if isinstance(hoja, int) and 0 < hoja < 100 else None,
            total_hojas=datos.get("total_hojas") if isinstance(datos.get("total_hojas"), int) else None,
            revision=(limpio("revision") or "").upper()[:3] or None,
            fecha=_fecha_iso(limpio("fecha")),
            escala=limpio("escala"),
            observaciones=limpio("observaciones"),
        )
        lectura.respuestas = [datos]
        return lectura


def _tipo_oracion(texto: Optional[str]) -> Optional[str]:
    """Los cajetines van en mayúsculas: se pasa a tipo oración, conservando las siglas conocidas."""
    if not texto or not texto.isupper():
        return texto
    return re.sub(r"p&id", "P&ID", texto.capitalize(), flags=re.IGNORECASE)


def _tokens_de_entrada(uso: Any) -> int:
    """Tokens de entrada, incluidos los que se leyeron o escribieron en caché."""
    return (uso.input_tokens + (getattr(uso, "cache_read_input_tokens", 0) or 0)
            + (getattr(uso, "cache_creation_input_tokens", 0) or 0))


def _fecha_iso(texto: Optional[str]) -> Optional[str]:
    if not texto:
        return None
    try:
        return datetime.strptime(texto[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None
