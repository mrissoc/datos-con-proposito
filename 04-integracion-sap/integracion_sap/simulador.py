"""Simulador de un servicio OData V2 de SAP Gateway, para desarrollar y probar sin SAP.

Reproduce el comportamiento que un cliente debe manejar: autenticación básica,
token CSRF ligado a la sesión, paginación del servidor con `__next`, filtros
`$filter`, fechas `/Date(ms)/` y errores en el formato de OData.

Los nombres de entidades y campos son ilustrativos (se parecen a los de las API
estándar de equipos y avisos de mantenimiento). En un proyecto real se toman
del servicio que publique la organización.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

RUTA_SERVICIO = "/sap/opu/odata/sap/ZPM_ACTIVOS_SRV"
RUTA_RED = Path(__file__).resolve().parent.parent.parent / "datos-ejemplo" / "red.json"
COOKIE_SESION = "SAP_SESSIONID_DEM_100"

CAMPOS_AVISO = {"NotificationType", "NotificationText", "LongText", "Equipment", "MaintPriority",
                "MalfunctionStartDate", "MalfunctionEndDate", "MaintenanceObjectIsDown",
                "ExternalReference", "NotificationPhase"}


def equipos_de_ejemplo() -> List[dict]:
    """Maestro de equipos derivado de la red de ejemplo.

    Incluye dos equipos que no existen en la red y omite un nodo, para que la
    conciliación tenga diferencias que informar.
    """
    red = json.loads(RUTA_RED.read_text(encoding="utf-8"))
    equipos: List[dict] = []

    def agregar(codigo: str, nombre: str, categoria: str) -> None:
        equipos.append({
            "Equipment": str(10000001 + len(equipos)),
            "EquipmentName": nombre[:40],
            "EquipmentCategory": categoria,
            "TechnicalIdentNumber": codigo,
            "FunctionalLocation": f"RED-{codigo}" if codigo else "",
        })

    for nodo in red["nodos"]:
        if nodo["tipo"] != "sector" and nodo["codigo"] != "UN-05":
            agregar(nodo["codigo"], nodo["nombre"], "M")
    for arco in red["arcos"]:
        agregar(arco["codigo"], f"Tubería {arco['origen']} a {arco['destino']}", "T")
    agregar("VEH-01", "Camión cisterna 01", "V")
    agregar("", "Grupo electrógeno móvil", "M")
    return equipos


def _error(estado: int, mensaje: str, codigo: str = "ZPM/000", cabeceras: Optional[dict] = None) -> JSONResponse:
    cuerpo = {"error": {"code": codigo, "message": {"lang": "es", "value": mensaje}}}
    return JSONResponse(cuerpo, status_code=estado, headers=cabeceras)


def _filtrar(filas: List[dict], filtro: Optional[str]) -> List[dict]:
    """Aplica un $filter con condiciones `Campo eq 'valor'` unidas por `and`."""
    if not filtro:
        return filas
    for condicion in re.split(r"\s+and\s+", filtro.strip()):
        coincidencia = re.fullmatch(r"(\w+)\s+eq\s+'((?:[^']|'')*)'", condicion)
        if not coincidencia:
            raise ValueError(f"Filtro no admitido: {condicion}")
        campo, valor = coincidencia.group(1), coincidencia.group(2).replace("''", "'")
        filas = [fila for fila in filas if str(fila.get(campo, "")) == valor]
    return filas


def crear_simulador(equipos: Optional[List[dict]] = None, *, usuario: str = "demo", clave: str = "demo",
                    pagina_maxima: int = 50) -> FastAPI:
    app = FastAPI(title="Simulador SAP Gateway (OData V2)", docs_url=None, redoc_url=None)
    estado = app.state
    estado.equipos = equipos if equipos is not None else equipos_de_ejemplo()
    estado.avisos = {}                 # número de aviso -> datos
    estado.tokens = {}                 # id de sesión -> token CSRF
    estado.siguiente_aviso = 300000001
    estado.solicitudes = []            # (método, ruta) de cada solicitud recibida
    # Fallas que las pruebas pueden activar: cantidad de respuestas con ese error.
    estado.fallas = {"post_503": 0, "get_503": 0, "post_429": 0, "vencer_tokens": False}

    def con_metadatos(entidad: str, clave_campo: str, fila: dict, base: str) -> dict:
        uri = f"{base}{RUTA_SERVICIO}/{entidad}('{fila[clave_campo]}')"
        return {"__metadata": {"id": uri, "uri": uri, "type": f"ZPM_ACTIVOS_SRV.{entidad}"}, **fila}

    def coleccion(request: Request, entidad: str, clave_campo: str, filas: List[dict]) -> Response:
        parametros = request.query_params
        try:
            filas = _filtrar(filas, parametros.get("$filter"))
        except ValueError as error:
            return _error(400, str(error), "ZPM/010")
        total = len(filas)
        inicio = int(parametros.get("$skiptoken") or parametros.get("$skip") or 0)
        pedido = int(parametros["$top"]) if "$top" in parametros else total
        fin = min(inicio + pedido, total)
        base = str(request.base_url).rstrip("/")
        cuerpo: Dict[str, Any] = {}
        if fin - inicio > pagina_maxima:
            # Paginación del servidor: se corta la página y se entrega el enlace a la
            # siguiente, que conserva cuántas filas faltan del $top original.
            restantes = fin - inicio - pagina_maxima
            fin = inicio + pagina_maxima
            siguiente = {"$skiptoken": fin, "$top": restantes, "$format": "json"}
            if parametros.get("$filter"):
                siguiente["$filter"] = parametros["$filter"]
            cuerpo["__next"] = f"{base}{RUTA_SERVICIO}/{entidad}?{urlencode(siguiente)}"
        cuerpo["results"] = [con_metadatos(entidad, clave_campo, fila, base) for fila in filas[inicio:fin]]
        if parametros.get("$inlinecount") == "allpages":
            cuerpo["__count"] = str(total)
        return JSONResponse({"d": cuerpo})

    def token_valido(request: Request) -> bool:
        sesion = request.cookies.get(COOKIE_SESION)
        return bool(sesion) and estado.tokens.get(sesion) == request.headers.get("X-CSRF-Token")

    @app.middleware("http")
    async def puerta(request: Request, siguiente):
        estado.solicitudes.append((request.method, request.url.path))
        # Autenticación básica, como en un usuario técnico de integración.
        esperado = "Basic " + base64.b64encode(f"{usuario}:{clave}".encode()).decode()
        if request.headers.get("Authorization") != esperado:
            return _error(401, "Usuario o clave incorrectos", "ZPM/401",
                          {"WWW-Authenticate": 'Basic realm="SAP NetWeaver Application Server"'})
        if request.method == "GET" and estado.fallas["get_503"] > 0:
            estado.fallas["get_503"] -= 1
            return _error(503, "Servicio no disponible", "ZPM/503")
        if request.method != "GET":
            if estado.fallas["vencer_tokens"]:
                estado.fallas["vencer_tokens"] = False
                estado.tokens.clear()
            if not token_valido(request):
                return Response("CSRF token validation failed", status_code=403,
                                headers={"X-CSRF-Token": "Required"})
            if request.method == "POST" and estado.fallas["post_429"] > 0:
                estado.fallas["post_429"] -= 1
                return _error(429, "Demasiadas solicitudes", "ZPM/429", {"Retry-After": "1"})
            if request.method == "POST" and estado.fallas["post_503"] > 0:
                estado.fallas["post_503"] -= 1
                return _error(503, "Servicio no disponible", "ZPM/503")
        respuesta = await siguiente(request)
        if request.headers.get("X-CSRF-Token", "").lower() == "fetch":
            sesion = request.cookies.get(COOKIE_SESION) or secrets.token_hex(12)
            estado.tokens[sesion] = secrets.token_urlsafe(18)
            respuesta.headers["X-CSRF-Token"] = estado.tokens[sesion]
            respuesta.set_cookie(COOKIE_SESION, sesion, httponly=True)
        return respuesta

    @app.get(RUTA_SERVICIO + "/")
    def documento_de_servicio() -> dict:
        return {"d": {"EntitySets": ["Equipment", "MaintenanceNotification"]}}

    @app.get(RUTA_SERVICIO + "/Equipment")
    def listar_equipos(request: Request) -> Response:
        return coleccion(request, "Equipment", "Equipment", estado.equipos)

    @app.get(RUTA_SERVICIO + "/MaintenanceNotification")
    def listar_avisos(request: Request) -> Response:
        return coleccion(request, "MaintenanceNotification", "MaintenanceNotification",
                         list(estado.avisos.values()))

    @app.post(RUTA_SERVICIO + "/MaintenanceNotification")
    async def crear_aviso(request: Request) -> Response:
        datos = await request.json()
        desconocidos = set(datos) - CAMPOS_AVISO
        if desconocidos:
            return _error(400, f"Propiedad no válida: {sorted(desconocidos)[0]}", "ZPM/020")
        if not datos.get("NotificationText"):
            return _error(400, "Indique el texto breve del aviso", "ZPM/021")
        if len(datos["NotificationText"]) > 40:
            return _error(400, "El texto breve admite 40 caracteres como máximo", "ZPM/022")
        if not any(equipo["Equipment"] == datos.get("Equipment") for equipo in estado.equipos):
            return _error(400, f"El equipo {datos.get('Equipment')} no existe", "ZPM/023")
        numero = str(estado.siguiente_aviso)
        estado.siguiente_aviso += 1
        aviso = {"MaintenanceNotification": numero, "NotificationPhase": "1",
                 "CreationDate": f"/Date({int(time.time() * 1000)})/", **datos}
        estado.avisos[numero] = aviso
        base = str(request.base_url).rstrip("/")
        return JSONResponse({"d": con_metadatos("MaintenanceNotification", "MaintenanceNotification", aviso, base)},
                            status_code=201)

    @app.patch(RUTA_SERVICIO + "/MaintenanceNotification({clave})")
    async def actualizar_aviso(clave: str, request: Request) -> Response:
        numero = clave.strip("'")
        if numero not in estado.avisos:
            return _error(404, f"El aviso {numero} no existe", "ZPM/404")
        datos = await request.json()
        desconocidos = set(datos) - CAMPOS_AVISO
        if desconocidos:
            return _error(400, f"Propiedad no válida: {sorted(desconocidos)[0]}", "ZPM/020")
        estado.avisos[numero].update(datos)
        return Response(status_code=204)

    return app
