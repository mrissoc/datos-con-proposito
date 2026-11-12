"""Actualiza un modelo semántico (dataset) de Power BI cuando el ETL termina.

Usa la API REST de Power BI con una entidad de servicio (registro de aplicación
de Entra ID con acceso al área de trabajo). Pide la actualización y espera su
resultado, para que la tarea programada falle visiblemente si el modelo no se
pudo actualizar.

Uso, después del ETL:
    python etl.py && python actualizar_powerbi.py

Variables de entorno: PBI_TENANT_ID, PBI_CLIENT_ID, PBI_CLIENT_SECRET,
PBI_WORKSPACE_ID y PBI_DATASET_ID.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Callable, Optional

import requests

API = "https://api.powerbi.com/v1.0/myorg"
ALCANCE = "https://analysis.windows.net/powerbi/api/.default"

log = logging.getLogger("powerbi")


class ErrorPowerBI(Exception):
    pass


class ClientePowerBI:
    def __init__(self, obtener_token: Callable[[], str], sesion: Optional[requests.Session] = None,
                 espera: Callable[[float], None] = time.sleep):
        self.obtener_token = obtener_token
        self.sesion = sesion or requests.Session()
        self.espera = espera

    @classmethod
    def desde_entorno(cls) -> "ClientePowerBI":
        import msal
        faltan = [v for v in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET") if not os.environ.get(v)]
        if faltan:
            raise SystemExit(f"Faltan variables de entorno: {', '.join(faltan)}")
        aplicacion = msal.ConfidentialClientApplication(
            os.environ["PBI_CLIENT_ID"], client_credential=os.environ["PBI_CLIENT_SECRET"],
            authority=f"https://login.microsoftonline.com/{os.environ['PBI_TENANT_ID']}")

        def obtener_token() -> str:
            resultado = aplicacion.acquire_token_for_client(scopes=[ALCANCE])
            if "access_token" not in resultado:
                raise ErrorPowerBI(resultado.get("error_description", "No se obtuvo el token"))
            return resultado["access_token"]

        return cls(obtener_token)

    def _solicitar(self, metodo: str, ruta: str, **opciones) -> requests.Response:
        respuesta = self.sesion.request(metodo, API + ruta, timeout=60,
                                        headers={"Authorization": f"Bearer {self.obtener_token()}"}, **opciones)
        if respuesta.status_code >= 400:
            try:
                detalle = respuesta.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detalle = respuesta.text[:200]
            raise ErrorPowerBI(f"Power BI respondió {respuesta.status_code}: {detalle}")
        return respuesta

    def actualizar(self, area_trabajo: str, modelo: str, intervalo: float = 20, maximo: float = 1800) -> dict:
        """Pide la actualización y espera a que termine. Devuelve el registro de la actualización."""
        ruta = f"/groups/{area_trabajo}/datasets/{modelo}/refreshes"
        self._solicitar("POST", ruta, json={"notifyOption": "NoNotification"})
        log.info("Actualización solicitada")
        esperado = 0.0
        while esperado <= maximo:
            self.espera(intervalo)
            esperado += intervalo
            ultima = self._solicitar("GET", ruta, params={"$top": 1}).json()["value"][0]
            # "Unknown" significa que la actualización sigue en curso.
            if ultima["status"] == "Completed":
                log.info("Actualización completada")
                return ultima
            if ultima["status"] in ("Failed", "Disabled", "Cancelled"):
                detalle = ultima.get("serviceExceptionJson") or ultima["status"]
                raise ErrorPowerBI(f"La actualización terminó con estado {ultima['status']}: {detalle}")
        raise ErrorPowerBI(f"La actualización no terminó en {maximo:.0f} segundos")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    faltan = [v for v in ("PBI_WORKSPACE_ID", "PBI_DATASET_ID") if not os.environ.get(v)]
    if faltan:
        raise SystemExit(f"Faltan variables de entorno: {', '.join(faltan)}")
    try:
        ClientePowerBI.desde_entorno().actualizar(os.environ["PBI_WORKSPACE_ID"], os.environ["PBI_DATASET_ID"])
    except ErrorPowerBI as error:
        log.error("%s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
