"""Servidor de la biblioteca.

Sirve la aplicación web y agrega la búsqueda en lenguaje natural: recibe una
frase ("planos eléctricos de la estación de bombeo sur desde 2015") y la
convierte, con Claude, en los filtros del catálogo. La credencial de la API se
queda en el servidor; el navegador nunca la ve.

    python -m biblioteca            http://127.0.0.1:8010/

Sin credenciales de Anthropic el servidor funciona igual y la página interpreta
las consultas con sus reglas locales.
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

mimetypes.add_type("text/javascript", ".js")

RAIZ = Path(__file__).resolve().parent.parent
MODELO = "claude-opus-5-5"


class Consulta(BaseModel):
    consulta: str = Field(..., min_length=1, max_length=300)


def esquema(catalogo: dict) -> dict:
    """Esquema de la respuesta: los filtros solo admiten códigos que existen en el catálogo."""
    taxonomia = catalogo["taxonomia"]
    codigos = lambda lista: [elemento["codigo"] for elemento in lista]            # noqa: E731
    lista_de = lambda valores: {"type": "array", "items": {"type": "string", "enum": valores}}   # noqa: E731
    entero = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    propiedades = {
        "clase": {"anyOf": [{"type": "string", "enum": ["plano", "documento"]}, {"type": "null"}]},
        "disciplinas": lista_de(codigos(taxonomia["disciplinas"])),
        "tipos": lista_de(codigos(taxonomia["tipos_plano"]) + codigos(taxonomia["tipos_documento"])),
        "proyectos": lista_de(codigos(taxonomia["proyectos"])),
        "instalaciones": lista_de(codigos(catalogo["instalaciones"])),
        "formatos": lista_de(["dwg", "dxf", "pdf", "docx", "xlsx", "doc", "xls"]),
        "anio_desde": entero,
        "anio_hasta": entero,
        "texto": {"type": "string"},
    }
    return {"type": "object", "properties": propiedades, "required": list(propiedades), "additionalProperties": False}


def instrucciones(catalogo: dict) -> str:
    taxonomia = catalogo["taxonomia"]
    lista = lambda elementos: "\n".join(f"- {e['codigo']}: {e['nombre']}" for e in elementos)   # noqa: E731
    proyectos = "\n".join(f"- {p['codigo']}: {p['nombre']} ({p['anio']}), instalación {p['instalacion']}"
                          for p in taxonomia["proyectos"])
    return f"""Conviertes consultas en lenguaje natural en filtros para el catálogo de una biblioteca técnica de planos y documentos de ingeniería.

Quien busca es personal de operaciones y mantenimiento. Escribe como habla: con sinónimos, abreviaturas, errores de tipeo y nombres parciales de instalaciones. Tu trabajo es entender qué quiere y expresarlo solo con los códigos del catálogo.

Cómo llenar cada filtro:
- clase: "plano" o "documento" si la consulta lo deja claro (un tipo de documento como memoria, informe o manual implica "documento"); null si puede ser cualquiera.
- disciplinas, tipos, proyectos, instalaciones y formatos: los códigos que correspondan; lista vacía si la consulta no restringe ese aspecto. Un filtro de más esconde resultados, así que agrega un código solo cuando la consulta lo pide.
- Si mencionan una instalación, usa el filtro de instalaciones y no agregues además sus proyectos.
- anio_desde y anio_hasta: límites del año del proyecto. "Desde 2015" es anio_desde 2015; "de 2018" son ambos 2018; "los últimos años" no es un dato preciso y se deja en null.
- texto: palabras que hay que buscar literalmente en el código o el título y que ningún filtro representa (un número de plano, "bombas", "cloración"). Sin tildes y en minúsculas. Cadena vacía si no queda nada.

Disciplinas:
{lista(taxonomia["disciplinas"])}

Tipos de plano:
{lista(taxonomia["tipos_plano"])}

Tipos de documento:
{lista(taxonomia["tipos_documento"])}

Proyectos:
{proyectos}

Instalaciones:
{lista(catalogo["instalaciones"])}
"""


def crear_app(cliente: Optional[anthropic.Anthropic] = None, ruta_catalogo: Optional[Path] = None,
              modelo: str = MODELO) -> FastAPI:
    app = FastAPI(title="Biblioteca técnica", version="1.0.0")
    catalogo = json.loads((ruta_catalogo or RAIZ / "datos" / "catalogo.json").read_text(encoding="utf-8"))
    con_credenciales = cliente is not None or bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    estado: Dict[str, Any] = {"cliente": cliente}
    esquema_filtros, texto_sistema = esquema(catalogo), instrucciones(catalogo)

    @app.get("/api/salud")
    def salud() -> Dict[str, Any]:
        """`ia` indica si este servidor puede interpretar consultas con Claude."""
        return {"estado": "ok", "ia": con_credenciales}

    @app.post("/api/interpretar")
    def interpretar(datos: Consulta) -> Dict[str, Any]:
        """Convierte una consulta en filtros del catálogo."""
        if not con_credenciales:
            raise HTTPException(503, "El servidor no tiene credenciales de la API de Anthropic")
        if estado["cliente"] is None:
            estado["cliente"] = anthropic.Anthropic()
        try:
            respuesta = estado["cliente"].beta.messages.create(
                model=modelo,
                max_tokens=16000,
                # Ante un rechazo de los clasificadores de seguridad, la API reintenta
                # en el modelo de respaldo que recomienda, dentro de la misma llamada.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": texto_sistema, "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": esquema_filtros}},
                messages=[{"role": "user", "content": datos.consulta}],
            )
        except anthropic.RateLimitError:
            raise HTTPException(503, "La API está limitando las solicitudes; intenta de nuevo en un momento")
        except anthropic.APIStatusError as error:
            raise HTTPException(502, f"La API respondió con un error ({error.status_code})")
        except anthropic.APIConnectionError:
            raise HTTPException(502, "No se pudo conectar con la API")

        if respuesta.stop_reason == "refusal":
            raise HTTPException(502, "El modelo declinó interpretar esta consulta")
        texto = next((bloque.text for bloque in respuesta.content if bloque.type == "text"), None)
        if respuesta.stop_reason == "max_tokens" or texto is None:
            raise HTTPException(502, "La respuesta del modelo llegó incompleta")
        return {**_validar(json.loads(texto), esquema_filtros), "modelo": respuesta.model}

    app.mount("/", StaticFiles(directory=RAIZ, html=True), name="web")
    return app


def _validar(filtros: dict, esquema_filtros: dict) -> dict:
    """Segunda barrera: descarta cualquier código que no esté en el catálogo."""
    limpio: Dict[str, Any] = {}
    for campo, definicion in esquema_filtros["properties"].items():
        valor = filtros.get(campo)
        if definicion.get("type") == "array":
            permitidos = set(definicion["items"]["enum"])
            limpio[campo] = [v for v in (valor or []) if v in permitidos]
        elif campo == "clase":
            limpio[campo] = valor if valor in ("plano", "documento") else None
        elif campo == "texto":
            limpio[campo] = str(valor or "").strip().lower()
        else:
            limpio[campo] = valor if isinstance(valor, int) and 1900 < valor < 2200 else None
    return limpio


def main() -> None:
    import argparse

    import uvicorn
    parser = argparse.ArgumentParser(description="Servidor de la biblioteca técnica")
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 para aceptar conexiones de otros equipos")
    parser.add_argument("--puerto", type=int, default=8010)
    args = parser.parse_args()
    uvicorn.run("biblioteca.servidor:crear_app", factory=True, host=args.host, port=args.puerto)
