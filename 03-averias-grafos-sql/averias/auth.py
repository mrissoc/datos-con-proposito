"""Autenticación de la API.

Dos modos, elegidos con la variable de entorno AUTH_MODO:

- `demo` (por defecto): no pide credenciales y entrega un usuario de prueba con
  permiso de escritura. Es el modo de la demostración local.
- `entra`: login corporativo con Microsoft Entra ID. La aplicación web obtiene un
  token con la cuenta de la organización y lo envía en `Authorization: Bearer`.
  Aquí se valida la firma contra las claves públicas del tenant, además del
  emisor, la audiencia y el vencimiento. Los permisos salen de los roles de
  aplicación asignados al usuario o a su grupo (claim `roles`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, FrozenSet, Optional

import jwt
from fastapi import Depends, HTTPException, Request, status

ROL_OPERADOR = "Averias.Operador"


@dataclass(frozen=True)
class Usuario:
    nombre: str
    roles: FrozenSet[str] = field(default_factory=frozenset)


USUARIO_DEMO = Usuario("demo@ejemplo.com", frozenset({ROL_OPERADOR}))


@lru_cache(maxsize=4)
def _cliente_jwks(url: str) -> jwt.PyJWKClient:
    """Cliente de claves públicas del tenant; guarda las claves en memoria."""
    return jwt.PyJWKClient(url, cache_keys=True)


@dataclass
class ConfiguracionAuth:
    modo: str = "demo"
    tenant_id: str = ""
    audiencia: str = ""           # identificador de la API en Entra ID (claim "aud")
    cliente_web: str = ""         # id del registro de la aplicación web
    alcance: str = ""             # permiso que la web pide para llamar a la API
    # Permite inyectar la clave de verificación en las pruebas.
    resolver_clave: Optional[Callable[[str], Any]] = None

    @classmethod
    def desde_entorno(cls) -> "ConfiguracionAuth":
        config = cls(
            modo=os.environ.get("AUTH_MODO", "demo").lower(),
            tenant_id=os.environ.get("ENTRA_TENANT_ID", ""),
            audiencia=os.environ.get("ENTRA_AUDIENCIA", ""),
            cliente_web=os.environ.get("ENTRA_CLIENTE_WEB", ""),
            alcance=os.environ.get("ENTRA_ALCANCE", ""),
        )
        if config.modo not in ("demo", "entra"):
            raise ValueError("AUTH_MODO debe ser 'demo' o 'entra'")
        if config.modo == "entra" and not (config.tenant_id and config.audiencia):
            raise ValueError("AUTH_MODO=entra requiere ENTRA_TENANT_ID y ENTRA_AUDIENCIA")
        return config

    def datos_publicos(self) -> dict:
        """Lo que la aplicación web necesita para iniciar sesión. No incluye secretos."""
        if self.modo != "entra":
            return {}
        return {"entra": {"tenant_id": self.tenant_id, "cliente_web": self.cliente_web,
                          "alcance": self.alcance}}

    @property
    def emisor(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def url_claves(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    def clave_de(self, token: str) -> Any:
        if self.resolver_clave is not None:
            return self.resolver_clave(token)
        return _cliente_jwks(self.url_claves).get_signing_key_from_jwt(token).key


def _no_autorizado(detalle: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detalle,
                         headers={"WWW-Authenticate": "Bearer"})


def usuario_actual(request: Request) -> Usuario:
    """Dependencia de FastAPI: identifica al usuario de la solicitud."""
    config: ConfiguracionAuth = request.app.state.auth
    if config.modo == "demo":
        return USUARIO_DEMO

    cabecera = request.headers.get("Authorization", "")
    esquema, _, token = cabecera.partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        raise _no_autorizado("Falta el token de acceso")
    token = token.strip()
    try:
        datos = jwt.decode(
            token,
            config.clave_de(token),
            algorithms=["RS256"],
            audience=config.audiencia,
            issuer=config.emisor,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise _no_autorizado("El token de acceso está vencido")
    except jwt.PyJWTError:
        raise _no_autorizado("El token de acceso no es válido")

    nombre = datos.get("preferred_username") or datos.get("upn") or datos.get("sub", "desconocido")
    return Usuario(nombre, frozenset(datos.get("roles", [])))


def requiere_rol(rol: str) -> Callable[..., Usuario]:
    """Dependencia que exige un rol de aplicación además de un token válido."""

    def dependencia(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
        if rol not in usuario.roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"Esta operación requiere el rol {rol}")
        return usuario

    return dependencia
