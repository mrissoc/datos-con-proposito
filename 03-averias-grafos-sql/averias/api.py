"""API REST de averías sobre la red de activos.

Ejecutar:
    python -m averias
    Aplicación web:      http://127.0.0.1:8000/
    Documentación API:   http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import mimetypes
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import bd
from .auth import ROL_OPERADOR, ConfiguracionAuth, Usuario, requiere_rol, usuario_actual
from .construir_bd import RUTA_DEMO, construir

# En Windows el registro puede asociar .js a text/plain, y el navegador rechaza
# los módulos JavaScript servidos con ese tipo.
mimetypes.add_type("text/javascript", ".js")

RUTA_TRABAJO = bd.RAIZ / "datos" / "averias.db"


class AveriaNueva(BaseModel):
    elemento_tipo: Literal["nodo", "arco"]
    elemento_id: int
    tipo_falla: str
    severidad: Literal["baja", "media", "alta"]
    fuera_servicio: bool = False
    descripcion: str = Field("", max_length=500)


class CambioEstado(BaseModel):
    estado: Literal["en_reparacion", "cerrada"]


def _conexion(request: Request) -> Iterator[sqlite3.Connection]:
    """Una conexión por solicitud."""
    conexion = bd.conectar(request.app.state.ruta_bd, entre_hilos=True)
    try:
        yield conexion
    finally:
        conexion.close()


def _preparar_bd(ruta: Path) -> Path:
    """Crea la base de trabajo a partir de la de demostración si aún no existe."""
    if not ruta.exists():
        if not RUTA_DEMO.exists():
            construir(RUTA_DEMO)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(RUTA_DEMO, ruta)
    return ruta


publico = APIRouter(prefix="/api")
protegido = APIRouter(prefix="/api", dependencies=[Depends(usuario_actual)])


@publico.get("/salud", tags=["sistema"])
def salud(request: Request) -> Dict[str, Any]:
    """Comprobación de vida. Indica el modo de autenticación y, con login
    corporativo, los datos públicos que la web necesita para iniciar sesión."""
    auth: ConfiguracionAuth = request.app.state.auth
    return {"estado": "ok", "auth": auth.modo, **auth.datos_publicos()}


@protegido.get("/yo", tags=["sistema"])
def yo(usuario: Usuario = Depends(usuario_actual)) -> Dict[str, Any]:
    """Usuario autenticado y sus roles."""
    return {"nombre": usuario.nombre, "roles": sorted(usuario.roles),
            "puede_escribir": ROL_OPERADOR in usuario.roles}


@protegido.get("/red", tags=["red"])
def red(conexion: sqlite3.Connection = Depends(_conexion)) -> Dict[str, Any]:
    """Nodos y arcos con su estado actual, más los indicadores generales."""
    return {
        "nodos": bd.consultar(conexion, "red_nodos"),
        "arcos": bd.consultar(conexion, "red_arcos"),
        "resumen": bd.consultar(conexion, "resumen")[0],
    }


@protegido.get("/tipos-falla", tags=["averías"])
def tipos_falla(conexion: sqlite3.Connection = Depends(_conexion)) -> List[Dict[str, Any]]:
    return bd.consultar(conexion, "tipos_falla")


@protegido.get("/averias", tags=["averías"])
def listar_averias(
    estado: Optional[Literal["activas", "abierta", "en_reparacion", "cerrada"]] = None,
    limite: int = Query(100, ge=1, le=500),
    conexion: sqlite3.Connection = Depends(_conexion),
) -> List[Dict[str, Any]]:
    """Averías de la más reciente a la más antigua. `activas` trae las no cerradas."""
    return bd.consultar(conexion, "averias_listar", estado=estado, limite=limite)


@protegido.post("/averias", status_code=status.HTTP_201_CREATED, tags=["averías"])
def crear_averia(
    datos: AveriaNueva,
    usuario: Usuario = Depends(requiere_rol(ROL_OPERADOR)),
    conexion: sqlite3.Connection = Depends(_conexion),
) -> Dict[str, Any]:
    """Registra una avería sobre un nodo o un arco."""
    tabla = "nodo" if datos.elemento_tipo == "nodo" else "arco"
    if conexion.execute(f"SELECT 1 FROM {tabla} WHERE id = ?", (datos.elemento_id,)).fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe el {tabla} {datos.elemento_id}")
    try:
        with conexion:
            cursor = bd.ejecutar(
                conexion, "averia_crear",
                elemento_tipo=datos.elemento_tipo, elemento_id=datos.elemento_id,
                tipo_falla=datos.tipo_falla, severidad=datos.severidad,
                fuera_servicio=int(datos.fuera_servicio), descripcion=datos.descripcion.strip(),
                usuario=usuario.nombre)
    except sqlite3.IntegrityError as error:
        # Tipo de falla inexistente o que no corresponde al tipo de elemento.
        raise HTTPException(422, str(error))
    return bd.consultar(conexion, "averia_por_id", id=cursor.lastrowid)[0]


@protegido.post("/averias/{averia_id}/estado", tags=["averías"])
def cambiar_estado(
    averia_id: int,
    cambio: CambioEstado,
    usuario: Usuario = Depends(requiere_rol(ROL_OPERADOR)),
    conexion: sqlite3.Connection = Depends(_conexion),
) -> Dict[str, Any]:
    """Pasa una avería a reparación o la cierra."""
    actual = bd.consultar(conexion, "averia_por_id", id=averia_id)
    if not actual:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe la avería {averia_id}")
    try:
        with conexion:
            bd.ejecutar(conexion, "averia_cambiar_estado", id=averia_id, estado=cambio.estado,
                        usuario=usuario.nombre)
    except sqlite3.IntegrityError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error))
    return bd.consultar(conexion, "averia_por_id", id=averia_id)[0]


@protegido.get("/averias/{averia_id}/eventos", tags=["averías"])
def eventos(averia_id: int, conexion: sqlite3.Connection = Depends(_conexion)) -> List[Dict[str, Any]]:
    """Bitácora de cambios de estado de una avería."""
    return bd.consultar(conexion, "averia_eventos", id=averia_id)


@protegido.get("/elementos/{elemento_tipo}/{elemento_id}/averias", tags=["averías"])
def averias_de_elemento(
    elemento_tipo: Literal["nodo", "arco"], elemento_id: int,
    conexion: sqlite3.Connection = Depends(_conexion),
) -> List[Dict[str, Any]]:
    """Historial de averías de un elemento."""
    return bd.consultar(conexion, "averias_por_elemento",
                        elemento_tipo=elemento_tipo, elemento_id=elemento_id)


@protegido.get("/simulacion", tags=["análisis"])
def simulacion(
    elemento_tipo: Literal["nodo", "arco"], elemento_id: int,
    conexion: sqlite3.Connection = Depends(_conexion),
) -> List[Dict[str, Any]]:
    """Nodos que quedarían sin suministro si además fallara el elemento indicado."""
    return bd.consultar(conexion, "impacto_simulado",
                        elemento_tipo=elemento_tipo, elemento_id=elemento_id)


@protegido.get("/criticidad", tags=["análisis"])
def criticidad(conexion: sqlite3.Connection = Depends(_conexion)) -> List[Dict[str, Any]]:
    """Análisis N-1: clientes afectados por la falla individual de cada elemento."""
    return bd.consultar(conexion, "criticidad")


@protegido.get("/ruta-suministro/{nodo_id}", tags=["análisis"])
def ruta_suministro(nodo_id: int,
                    conexion: sqlite3.Connection = Depends(_conexion)) -> Optional[Dict[str, Any]]:
    """Ruta más corta por la que hoy llega el agua al nodo, o `null` si no tiene suministro."""
    filas = bd.consultar(conexion, "ruta_suministro", nodo_id=nodo_id)
    return filas[0] if filas else None


@protegido.get("/estadisticas", tags=["análisis"])
def estadisticas(conexion: sqlite3.Connection = Depends(_conexion)) -> Dict[str, Any]:
    """Averías por mes y elementos reincidentes de los últimos 12 meses."""
    return {
        "mensual": bd.consultar(conexion, "estadisticas_mensual"),
        "elementos": bd.consultar(conexion, "estadisticas_elementos"),
    }


def crear_app(ruta_bd: Optional[Path] = None, auth: Optional[ConfiguracionAuth] = None) -> FastAPI:
    """Arma la aplicación. Las pruebas pasan su propia base y configuración."""
    app = FastAPI(
        title="Averías sobre red de activos",
        description="Registro de averías y análisis de impacto sobre una red modelada como grafo en SQL.",
        version="1.0.0",
    )
    app.state.ruta_bd = _preparar_bd(Path(ruta_bd or os.environ.get("AVERIAS_BD", RUTA_TRABAJO)))
    app.state.auth = auth or ConfiguracionAuth.desde_entorno()
    app.include_router(publico)
    app.include_router(protegido)

    @app.get("/", include_in_schema=False)
    def inicio() -> RedirectResponse:
        return RedirectResponse("/web/")

    # La aplicación web y los archivos SQL que muestra se sirven como estáticos.
    app.mount("/web", StaticFiles(directory=bd.RAIZ / "web", html=True), name="web")
    app.mount("/sql", StaticFiles(directory=bd.RAIZ / "sql"), name="sql")
    return app
