"""Envío de correo por Microsoft Graph, con una cuenta de aplicación (sin usuario presente).

El proceso corre desatendido: se autentica con las credenciales de un registro de
aplicación que tiene el permiso `Mail.Send` y envía desde un buzón compartido.
"""

from __future__ import annotations

import os
import random
import time
from typing import Callable, Optional

import requests

from .campania import Mensaje, a_graph

GRAPH = "https://graph.microsoft.com/v1.0"


class ErrorDeEnvio(Exception):
    pass


class ClienteCorreo:
    def __init__(self, obtener_token: Callable[[], str], remitente: str,
                 sesion: Optional[requests.Session] = None, max_intentos: int = 5,
                 espera: Callable[[float], None] = time.sleep, pausa: float = 0.0):
        self.obtener_token = obtener_token
        self.remitente = remitente
        self.sesion = sesion or requests.Session()
        self.max_intentos = max_intentos
        self.espera = espera
        self.pausa = pausa                       # segundos entre correos, para no gatillar el límite del buzón

    @classmethod
    def desde_entorno(cls, pausa: float = 2.0) -> "ClienteCorreo":
        """Credenciales en GRAPH_TENANT_ID, GRAPH_CLIENT_ID y GRAPH_CLIENT_SECRET; buzón en CORREO_REMITENTE."""
        import msal
        faltan = [v for v in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "CORREO_REMITENTE")
                  if not os.environ.get(v)]
        if faltan:
            raise SystemExit(f"Faltan variables de entorno: {', '.join(faltan)}")
        aplicacion = msal.ConfidentialClientApplication(
            os.environ["GRAPH_CLIENT_ID"], client_credential=os.environ["GRAPH_CLIENT_SECRET"],
            authority=f"https://login.microsoftonline.com/{os.environ['GRAPH_TENANT_ID']}")

        def obtener_token() -> str:
            resultado = aplicacion.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
            if "access_token" not in resultado:
                raise ErrorDeEnvio(resultado.get("error_description", "No se obtuvo el token"))
            return resultado["access_token"]

        return cls(obtener_token, os.environ["CORREO_REMITENTE"], pausa=pausa)

    def enviar(self, mensaje: Mensaje) -> None:
        """Envía un correo. Ante límites del servicio espera lo que indique `Retry-After`."""
        url = f"{GRAPH}/users/{self.remitente}/sendMail"
        for intento in range(1, self.max_intentos + 1):
            respuesta = self.sesion.post(url, json=a_graph(mensaje), timeout=60,
                                         headers={"Authorization": f"Bearer {self.obtener_token()}"})
            if respuesta.status_code == 202:
                if self.pausa:
                    self.espera(self.pausa)
                return
            if respuesta.status_code in (429, 503, 504) and intento < self.max_intentos:
                pedida = respuesta.headers.get("Retry-After", "")
                self.espera(float(pedida) if pedida.isdigit() else min(2 ** intento, 60) + random.uniform(0, 1))
                continue
            try:
                detalle = respuesta.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detalle = respuesta.text[:200]
            raise ErrorDeEnvio(f"Graph respondió {respuesta.status_code}: {detalle}")
