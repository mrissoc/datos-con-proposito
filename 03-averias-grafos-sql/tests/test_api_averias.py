"""Pruebas de la API: operaciones, validaciones y login corporativo."""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from averias.api import crear_app
from averias.auth import ROL_OPERADOR, ConfiguracionAuth

TENANT = "11111111-2222-3333-4444-555555555555"
AUDIENCIA = "api://averias-demo"


@pytest.fixture
def cliente(ruta_bd):
    return TestClient(crear_app(ruta_bd, ConfiguracionAuth(modo="demo")))


def _ids(cliente):
    red = cliente.get("/api/red").json()
    return ({n["codigo"]: n["id"] for n in red["nodos"]}, {a["codigo"]: a["id"] for a in red["arcos"]})


def test_salud_y_red(cliente):
    assert cliente.get("/api/salud").json() == {"estado": "ok", "auth": "demo"}
    red = cliente.get("/api/red").json()
    assert len(red["nodos"]) == 26 and len(red["arcos"]) == 27
    assert red["resumen"]["clientes_sin_suministro"] == 0


def test_registrar_y_cerrar_una_averia(cliente):
    _, arcos = _ids(cliente)
    respuesta = cliente.post("/api/averias", json={
        "elemento_tipo": "arco", "elemento_id": arcos["TUB-019"], "tipo_falla": "rotura",
        "severidad": "alta", "fuera_servicio": True, "descripcion": "  Rotura en calzada  "})
    assert respuesta.status_code == 201
    averia = respuesta.json()
    assert averia["codigo"].startswith("AV-") and averia["estado"] == "abierta"
    assert averia["elemento_codigo"] == "TUB-019" and averia["descripcion"] == "Rotura en calzada"
    assert averia["reportada_por"] == "demo@ejemplo.com"

    assert cliente.get("/api/red").json()["resumen"]["clientes_sin_suministro"] == 650
    assert len(cliente.get("/api/averias", params={"estado": "activas"}).json()) == 1

    cerrada = cliente.post(f"/api/averias/{averia['id']}/estado", json={"estado": "cerrada"}).json()
    assert cerrada["estado"] == "cerrada" and cerrada["cerrada_en"]
    assert cliente.get("/api/red").json()["resumen"]["clientes_sin_suministro"] == 0
    eventos = cliente.get(f"/api/averias/{averia['id']}/eventos").json()
    assert [e["estado"] for e in eventos] == ["abierta", "cerrada"]

    repetir = cliente.post(f"/api/averias/{averia['id']}/estado", json={"estado": "en_reparacion"})
    assert repetir.status_code == 409


def test_validaciones_al_registrar(cliente):
    nodos, arcos = _ids(cliente)
    base = {"elemento_tipo": "arco", "elemento_id": arcos["TUB-019"], "tipo_falla": "rotura",
            "severidad": "alta"}
    assert cliente.post("/api/averias", json={**base, "elemento_id": 9999}).status_code == 404
    assert cliente.post("/api/averias", json={**base, "severidad": "grave"}).status_code == 422
    assert cliente.post("/api/averias", json={**base, "tipo_falla": "no_existe"}).status_code == 422
    # "bomba" es una falla de nodo: no se puede registrar sobre una tubería.
    assert cliente.post("/api/averias", json={**base, "tipo_falla": "bomba"}).status_code == 422
    assert cliente.post("/api/averias/9999/estado", json={"estado": "cerrada"}).status_code == 404
    assert cliente.get("/api/averias", params={"estado": "activas"}).json() == []


def test_analisis(cliente):
    nodos, arcos = _ids(cliente)
    simulacion = cliente.get("/api/simulacion", params={
        "elemento_tipo": "arco", "elemento_id": arcos["TUB-012"]}).json()
    assert {f["codigo"] for f in simulacion if f["tipo"] == "sector"} == {"SEC-01", "SEC-02"}

    criticidad = cliente.get("/api/criticidad").json()
    assert criticidad[0]["codigo"] == "UN-01"

    ruta = cliente.get(f"/api/ruta-suministro/{nodos['SEC-01']}").json()
    assert ruta["longitud_m"] == 11050

    estadisticas = cliente.get("/api/estadisticas").json()
    assert set(estadisticas) == {"mensual", "elementos"}

    historial = cliente.get(f"/api/elementos/arco/{arcos['TUB-012']}/averias").json()
    assert historial == []


def test_sirve_la_aplicacion_web_y_el_sql(cliente):
    assert cliente.get("/sql/consultas/criticidad.sql").status_code == 200
    assert cliente.get("/", follow_redirects=False).headers["location"] == "/web/"


# ------------------------------------------------------- Login corporativo

@pytest.fixture(scope="module")
def clave_privada():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def cliente_entra(ruta_bd, clave_privada):
    config = ConfiguracionAuth(modo="entra", tenant_id=TENANT, audiencia=AUDIENCIA,
                               resolver_clave=lambda token: clave_privada.public_key())
    return TestClient(crear_app(ruta_bd, config))


def _token(clave, roles=(), audiencia=AUDIENCIA, vence_en=600, tenant=TENANT):
    ahora = int(time.time())
    return jwt.encode({
        "iss": f"https://login.microsoftonline.com/{tenant}/v2.0",
        "aud": audiencia,
        "iat": ahora, "exp": ahora + vence_en,
        "preferred_username": "ana.perez@empresa.example",
        "roles": list(roles),
    }, clave, algorithm="RS256")


def _cabecera(token):
    return {"Authorization": f"Bearer {token}"}


def test_entra_exige_token(cliente_entra):
    assert cliente_entra.get("/api/salud").status_code == 200          # la salud es pública
    respuesta = cliente_entra.get("/api/red")
    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == "Bearer"
    assert cliente_entra.get("/api/red", headers={"Authorization": "Basic abc"}).status_code == 401


def test_entra_token_valido_identifica_al_usuario(cliente_entra, clave_privada):
    cabecera = _cabecera(_token(clave_privada, roles=[ROL_OPERADOR]))
    yo = cliente_entra.get("/api/yo", headers=cabecera).json()
    assert yo == {"nombre": "ana.perez@empresa.example", "roles": [ROL_OPERADOR],
                  "puede_escribir": True}
    assert cliente_entra.get("/api/red", headers=cabecera).status_code == 200


def test_entra_escribir_requiere_el_rol(cliente_entra, clave_privada):
    lector = _cabecera(_token(clave_privada))
    operador = _cabecera(_token(clave_privada, roles=[ROL_OPERADOR]))
    arcos = {a["codigo"]: a["id"] for a in cliente_entra.get("/api/red", headers=lector).json()["arcos"]}
    cuerpo = {"elemento_tipo": "arco", "elemento_id": arcos["TUB-019"], "tipo_falla": "fuga",
              "severidad": "baja"}

    assert cliente_entra.post("/api/averias", json=cuerpo, headers=lector).status_code == 403
    creada = cliente_entra.post("/api/averias", json=cuerpo, headers=operador)
    assert creada.status_code == 201
    assert creada.json()["reportada_por"] == "ana.perez@empresa.example"


@pytest.mark.parametrize("cambio", [
    {"vence_en": -60},                                  # vencido
    {"audiencia": "api://otra-aplicacion"},             # emitido para otra aplicación
    {"tenant": "99999999-0000-0000-0000-000000000000"}, # emitido por otra organización
])
def test_entra_rechaza_tokens_invalidos(cliente_entra, clave_privada, cambio):
    token = _token(clave_privada, roles=[ROL_OPERADOR], **cambio)
    assert cliente_entra.get("/api/red", headers=_cabecera(token)).status_code == 401


def test_entra_rechaza_firma_de_otra_clave(cliente_entra):
    otra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(otra, roles=[ROL_OPERADOR])
    assert cliente_entra.get("/api/red", headers=_cabecera(token)).status_code == 401
    assert cliente_entra.get("/api/red", headers=_cabecera("no-es-un-jwt")).status_code == 401
