"""Almacén sobre una biblioteca de documentos de SharePoint, vía Microsoft Graph.

Puntos que importan a la escala de cientos de miles de archivos:

- El inventario usa la consulta `delta`, que recorre toda la biblioteca en
  páginas sin tener que entrar carpeta por carpeta.
- La huella de cada archivo (`quickXorHash`) viene en el listado: las copias se
  detectan sin descargar nada.
- Graph limita la cantidad de solicitudes. Ante un 429 o un 503 se respeta el
  tiempo que indica la cabecera `Retry-After` antes de reintentar.
- Reubicar es mover dentro de la misma biblioteca: no se transfiere contenido y
  el archivo conserva su historial de versiones.
"""

from __future__ import annotations

import os
import random
import threading
import time
from typing import Callable, Dict, Iterator, Optional
from urllib.parse import quote, unquote

import requests

from . import ArchivoOrigen, ErrorTransitorio

GRAPH = "https://graph.microsoft.com/v1.0"
REINTENTABLES = (429, 503, 504)


class ErrorGraph(Exception):
    def __init__(self, estado: int, mensaje: str):
        super().__init__(f"Graph respondió {estado}: {mensaje}")
        self.estado = estado


class ClienteGraph:
    """Solicitudes autenticadas a Microsoft Graph, con reintentos ante límites del servicio."""

    def __init__(self, obtener_token: Callable[[], str], sesion: Optional[requests.Session] = None,
                 max_intentos: int = 4, espera: Callable[[float], None] = time.sleep):
        self.obtener_token = obtener_token
        self.sesion = sesion or requests.Session()
        self.max_intentos = max_intentos
        self.espera = espera

    @classmethod
    def con_credenciales(cls, tenant_id: str, cliente_id: str, secreto: str) -> "ClienteGraph":
        """Autenticación de aplicación (sin usuario), como corre un proceso desatendido."""
        import msal
        aplicacion = msal.ConfidentialClientApplication(
            cliente_id, authority=f"https://login.microsoftonline.com/{tenant_id}", client_credential=secreto)

        def obtener_token() -> str:
            # MSAL guarda el token en memoria y lo renueva cuando está por vencer.
            resultado = aplicacion.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
            if "access_token" not in resultado:
                raise ErrorGraph(401, resultado.get("error_description", "No se obtuvo el token"))
            return resultado["access_token"]

        return cls(obtener_token)

    def solicitar(self, metodo: str, url: str, aceptar: tuple = (), **opciones) -> requests.Response:
        if not url.startswith("http"):
            url = GRAPH + url
        cabeceras = opciones.pop("headers", {})
        for intento in range(1, self.max_intentos + 1):
            respuesta = self.sesion.request(
                metodo, url, timeout=60, headers={"Authorization": f"Bearer {self.obtener_token()}", **cabeceras},
                **opciones)
            if respuesta.status_code in REINTENTABLES:
                pedida = respuesta.headers.get("Retry-After", "")
                segundos = float(pedida) if pedida.isdigit() else min(2 ** intento, 60) + random.uniform(0, 1)
                if intento == self.max_intentos:
                    raise ErrorTransitorio(f"Graph sigue limitando las solicitudes ({respuesta.status_code})", segundos)
                self.espera(segundos)
                continue
            if respuesta.status_code >= 400 and respuesta.status_code not in aceptar:
                try:
                    mensaje = respuesta.json()["error"]["message"]
                except (ValueError, KeyError, TypeError):
                    mensaje = respuesta.text[:200]
                raise ErrorGraph(respuesta.status_code, mensaje)
            return respuesta
        raise AssertionError("inalcanzable")


class AlmacenSharePoint:
    def __init__(self, cliente: ClienteGraph, drive_id: str):
        self.cliente = cliente
        self.drive = f"/drives/{drive_id}"
        self._carpetas: Dict[str, str] = {}
        self._candado = threading.Lock()

    @classmethod
    def desde_entorno(cls, drive_id: str) -> "AlmacenSharePoint":
        """Lee las credenciales de GRAPH_TENANT_ID, GRAPH_CLIENT_ID y GRAPH_CLIENT_SECRET."""
        faltan = [v for v in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET") if not os.environ.get(v)]
        if faltan:
            raise SystemExit(f"Faltan variables de entorno: {', '.join(faltan)}")
        return cls(ClienteGraph.con_credenciales(os.environ["GRAPH_TENANT_ID"], os.environ["GRAPH_CLIENT_ID"],
                                                 os.environ["GRAPH_CLIENT_SECRET"]), drive_id)

    def listar(self) -> Iterator[ArchivoOrigen]:
        url: Optional[str] = (f"{self.drive}/root/delta"
                              "?$select=id,name,size,file,deleted,parentReference,lastModifiedDateTime")
        while url:
            pagina = self.cliente.solicitar("GET", url).json()
            for elemento in pagina.get("value", []):
                if "file" not in elemento or "deleted" in elemento:
                    continue                                 # carpetas y elementos eliminados
                ruta = unquote(elemento.get("parentReference", {}).get("path", "").partition("root:")[2]).strip("/")
                huellas = elemento["file"].get("hashes", {})
                yield ArchivoOrigen(
                    origen_id=elemento["id"], ruta=ruta, nombre=elemento["name"], tamano=elemento.get("size", 0),
                    modificado=elemento.get("lastModifiedDateTime"),
                    hash=huellas.get("quickXorHash") or huellas.get("sha256Hash"))
            url = pagina.get("@odata.nextLink")

    def leer_inicio(self, origen_id: str, cantidad: int = 32) -> bytes:
        respuesta = self.cliente.solicitar("GET", f"{self.drive}/items/{origen_id}/content",
                                           headers={"Range": f"bytes=0-{cantidad - 1}"})
        return respuesta.content[:cantidad]

    def leer(self, origen_id: str) -> bytes:
        return self.cliente.solicitar("GET", f"{self.drive}/items/{origen_id}/content").content

    def reubicar(self, origen_id: str, destino: str, metadatos: Dict[str, str]) -> str:
        carpeta, _, nombre = destino.rpartition("/")
        self.cliente.solicitar("PATCH", f"{self.drive}/items/{origen_id}",
                               json={"parentReference": {"id": self._carpeta(carpeta)}, "name": nombre})
        # Los metadatos van a las columnas de la biblioteca.
        self.cliente.solicitar("PATCH", f"{self.drive}/items/{origen_id}/listItem/fields", json=metadatos)
        return origen_id

    def _carpeta(self, ruta: str) -> str:
        """Id de una carpeta de destino; la crea, con sus carpetas superiores, si no existe."""
        with self._candado:
            if ruta in self._carpetas:
                return self._carpetas[ruta]
            if not ruta:
                identificador = self.cliente.solicitar("GET", f"{self.drive}/root?$select=id").json()["id"]
            else:
                consulta = f"{self.drive}/root:/{quote(ruta)}?$select=id"
                respuesta = self.cliente.solicitar("GET", consulta, aceptar=(404,))
                if respuesta.status_code == 404:
                    superior, _, nombre = ruta.rpartition("/")
                    self._candado.release()
                    try:
                        padre = self._carpeta(superior)
                    finally:
                        self._candado.acquire()
                    respuesta = self.cliente.solicitar(
                        "POST", f"{self.drive}/items/{padre}/children", aceptar=(409,),
                        json={"name": nombre, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
                    if respuesta.status_code == 409:         # otro hilo la creó entretanto
                        respuesta = self.cliente.solicitar("GET", consulta)
                identificador = respuesta.json()["id"]
            self._carpetas[ruta] = identificador
            return identificador
