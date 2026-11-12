"""Cliente de un servicio OData V2 publicado por SAP Gateway.

Resuelve lo que hace particular a SAP frente a una API REST común:

- Token CSRF: antes de crear o modificar hay que pedir un token con
  `X-CSRF-Token: Fetch` y devolverlo, junto con la cookie de sesión, en cada
  escritura. Si la sesión vence, SAP responde 403 con `X-CSRF-Token: Required`;
  el cliente pide un token nuevo y repite la operación una vez.
- Paginación: sigue los enlaces `__next` del servidor y, si no los hay, avanza
  con `$top` y `$skip`.
- Reintentos: las lecturas y actualizaciones se reintentan ante errores
  transitorios. Las creaciones solo se reintentan cuando SAP indica que no
  procesó la solicitud (429), porque repetir a ciegas un POST que sí llegó
  crearía un documento duplicado.
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

import requests

log = logging.getLogger(__name__)

TRANSITORIOS = frozenset({429, 500, 502, 503, 504})
SOLO_NO_PROCESADO = frozenset({429})


class ErrorSap(Exception):
    """Respuesta de error del servicio, con el mensaje que entrega SAP."""

    def __init__(self, estado: int, mensaje: str, codigo: Optional[str] = None):
        super().__init__(f"SAP respondió {estado}: {mensaje}")
        self.estado = estado
        self.mensaje = mensaje
        self.codigo = codigo


def a_fecha_odata(momento: datetime) -> str:
    """Fecha en el formato de OData V2: /Date(milisegundos)/."""
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return f"/Date({int(momento.timestamp() * 1000)})/"


def de_fecha_odata(texto: str) -> datetime:
    coincidencia = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", texto)
    if not coincidencia:
        raise ValueError(f"Fecha OData no reconocida: {texto!r}")
    return datetime.fromtimestamp(int(coincidencia.group(1)) / 1000, tz=timezone.utc)


def literal(valor: str) -> str:
    """Texto entre comillas simples para usar en $filter, con las comillas escapadas."""
    return "'" + valor.replace("'", "''") + "'"


class ClienteSap:
    def __init__(self, url_servicio: str, usuario: str, clave: str, *,
                 sesion: Optional[requests.Session] = None, max_intentos: int = 4,
                 espera: Callable[[float], None] = time.sleep, timeout: float = 30.0):
        self.url = url_servicio.rstrip("/")
        self.sesion = sesion or requests.Session()
        self.sesion.auth = (usuario, clave)
        self.sesion.headers.update({"Accept": "application/json"})
        self.max_intentos = max_intentos
        self.espera = espera
        self.timeout = timeout
        self._token: Optional[str] = None

    # ------------------------------------------------------------ Operaciones

    def leer(self, entidad: str, filtro: Optional[str] = None, tamano_pagina: int = 200) -> Iterator[dict]:
        """Recorre todas las filas de una entidad.

        Pide ventanas de `tamano_pagina` filas con `$top` y `$skip`. Dentro de cada
        ventana sigue los enlaces `__next` cuando el servidor decide entregarla en
        varias partes. Termina cuando una ventana llega incompleta.
        """
        base = {"$format": "json", "$top": str(tamano_pagina)}
        if filtro:
            base["$filter"] = filtro
        saltadas = 0
        while True:
            en_ventana = 0
            url: Optional[str] = f"{self.url}/{entidad}"
            parametros: Optional[dict] = {**base, "$skip": str(saltadas)}
            while url:
                datos = self._enviar("GET", url, TRANSITORIOS, params=parametros).json()["d"]
                en_ventana += len(datos["results"])
                yield from datos["results"]
                # El enlace del servidor ya trae sus propios parámetros.
                url, parametros = datos.get("__next"), None
            if en_ventana < tamano_pagina:
                return
            saltadas += en_ventana

    def crear(self, entidad: str, datos: dict) -> dict:
        """Crea una entidad y devuelve lo que SAP registró (incluido el número asignado)."""
        respuesta = self._modificar("POST", f"{self.url}/{entidad}", datos, SOLO_NO_PROCESADO)
        return respuesta.json()["d"]

    def actualizar(self, entidad: str, clave: str, datos: dict) -> None:
        """Actualiza campos de una entidad existente."""
        self._modificar("PATCH", f"{self.url}/{entidad}({literal(clave)})", datos, TRANSITORIOS)

    # --------------------------------------------------------------- Interno

    def _modificar(self, metodo: str, url: str, datos: dict, reintentar: frozenset) -> requests.Response:
        for intento in (1, 2):
            if self._token is None:
                self._token = self._pedir_token()
            respuesta = self._enviar(metodo, url, reintentar, json=datos, aceptar=(403,),
                                     headers={"X-CSRF-Token": self._token})
            if respuesta.status_code != 403:
                return respuesta
            token_requerido = respuesta.headers.get("X-CSRF-Token", "").lower() == "required"
            if not (token_requerido and intento == 1):
                raise self._error(respuesta)
            log.info("El token CSRF venció; se solicita uno nuevo")
            self._token = None
        raise AssertionError("inalcanzable")

    def _pedir_token(self) -> str:
        respuesta = self._enviar("GET", f"{self.url}/", TRANSITORIOS, headers={"X-CSRF-Token": "Fetch"})
        token = respuesta.headers.get("X-CSRF-Token")
        if not token or token.lower() == "required":
            raise ErrorSap(respuesta.status_code, "El servicio no entregó un token CSRF")
        return token

    def _enviar(self, metodo: str, url: str, reintentar: frozenset, aceptar: tuple = (),
                **opciones: Any) -> requests.Response:
        """Envía una solicitud con reintentos y espera creciente."""
        for intento in range(1, self.max_intentos + 1):
            ultimo = intento == self.max_intentos
            try:
                respuesta = self.sesion.request(metodo, url, timeout=self.timeout, **opciones)
            except (requests.ConnectionError, requests.Timeout) as error:
                # Un POST que no obtuvo respuesta pudo haberse procesado: no se repite.
                if ultimo or metodo == "POST":
                    raise
                log.warning("%s %s: %s. Reintento %d", metodo, url, error, intento)
                self.espera(self._pausa(intento, None))
                continue
            if respuesta.status_code in reintentar and not ultimo:
                log.warning("%s %s: HTTP %d. Reintento %d", metodo, url, respuesta.status_code, intento)
                self.espera(self._pausa(intento, respuesta.headers.get("Retry-After")))
                continue
            if respuesta.status_code >= 400 and respuesta.status_code not in aceptar:
                raise self._error(respuesta)
            return respuesta
        raise AssertionError("inalcanzable")

    @staticmethod
    def _pausa(intento: int, retry_after: Optional[str]) -> float:
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        return min(2 ** intento, 30) + random.uniform(0, 0.5)

    @staticmethod
    def _error(respuesta: requests.Response) -> ErrorSap:
        """Extrae el mensaje del cuerpo de error de OData, si viene en ese formato."""
        codigo, mensaje = None, respuesta.text.strip()[:300] or respuesta.reason
        try:
            error = respuesta.json()["error"]
            codigo = error.get("code")
            mensaje = error["message"]["value"]
        except (ValueError, KeyError, TypeError):
            pass
        return ErrorSap(respuesta.status_code, mensaje, codigo)
