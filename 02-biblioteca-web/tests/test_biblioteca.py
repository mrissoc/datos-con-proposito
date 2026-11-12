"""Servidor de la biblioteca: interpretación de consultas con el SDK real contra una API simulada."""

from __future__ import annotations

import json
import threading
import time

import anthropic
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from biblioteca.servidor import crear_app, esquema

CATALOGO = {
    "taxonomia": {
        "disciplinas": [{"codigo": "ELE", "nombre": "Eléctrica"}, {"codigo": "MEC", "nombre": "Mecánica"}],
        "tipos_plano": [{"codigo": "DU", "nombre": "Diagrama unilineal"}],
        "tipos_documento": [{"codigo": "MC", "nombre": "Memoria de cálculo"}],
        "proyectos": [{"codigo": "PRY-017", "nombre": "Estación de Bombeo Sur", "anio": 2015, "instalacion": "EB-02"}],
    },
    "instalaciones": [{"codigo": "EB-02", "nombre": "Estación de Bombeo Sur", "tipo": "bombeo"}],
    "resumen": {"archivos": 10, "copias": 2}, "items": [],
}
FILTROS = {"clase": "plano", "disciplinas": ["ELE"], "tipos": ["DU"], "proyectos": [], "instalaciones": ["EB-02"],
           "formatos": [], "anio_desde": 2015, "anio_hasta": None, "texto": "Tablero"}


@pytest.fixture
def api_simulada():
    """Imita el endpoint de mensajes de la API de Anthropic en un puerto libre."""
    app = FastAPI()
    app.state.respuestas, app.state.solicitudes = [], []

    @app.post("/v1/messages")
    async def mensajes(request: Request):
        cuerpo = await request.json()
        app.state.solicitudes.append({"cuerpo": cuerpo, "cabeceras": dict(request.headers)})
        siguiente = app.state.respuestas.pop(0)
        if "estado" in siguiente:
            return JSONResponse({"type": "error", "error": {"type": "api_error", "message": "falla"}},
                                status_code=siguiente["estado"])
        contenido = [{"type": "text", "text": json.dumps(siguiente["datos"])}] if "datos" in siguiente else []
        return {"id": "msg_prueba", "type": "message", "role": "assistant", "model": cuerpo["model"], "content": contenido,
                "stop_reason": siguiente.get("stop_reason", "end_turn"), "stop_sequence": None,
                "usage": {"input_tokens": 900, "output_tokens": 60}}

    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    while not servidor.started:
        time.sleep(0.01)
    app.state.url = f"http://127.0.0.1:{servidor.servers[0].sockets[0].getsockname()[1]}"
    yield app
    servidor.should_exit = True
    hilo.join(timeout=5)


@pytest.fixture
def ruta_catalogo(tmp_path):
    ruta = tmp_path / "catalogo.json"
    ruta.write_text(json.dumps(CATALOGO), encoding="utf-8")
    return ruta


@pytest.fixture
def cliente(api_simulada, ruta_catalogo):
    sdk = anthropic.Anthropic(api_key="clave-de-prueba", base_url=api_simulada.state.url, max_retries=0)
    return TestClient(crear_app(sdk, ruta_catalogo))


def test_interpreta_una_consulta_y_valida_los_codigos(cliente, api_simulada):
    api_simulada.state.respuestas = [{"datos": {**FILTROS, "disciplinas": ["ELE", "XYZ"], "anio_hasta": 99999}}]
    respuesta = cliente.post("/api/interpretar", json={"consulta": "unilineales eléctricos de bombeo sur desde 2015, tablero"})
    assert respuesta.status_code == 200
    assert respuesta.json() == {**FILTROS, "texto": "tablero", "modelo": "claude-opus-5-5"}   # XYZ y el año absurdo se descartan

    solicitud = api_simulada.state.solicitudes[0]
    cuerpo = solicitud["cuerpo"]
    assert cuerpo["model"] == "claude-opus-5-5" and cuerpo["fallbacks"] == "default"
    assert cuerpo["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": esquema(CATALOGO)}}
    assert cuerpo["messages"] == [{"role": "user", "content": "unilineales eléctricos de bombeo sur desde 2015, tablero"}]
    assert "EB-02: Estación de Bombeo Sur" in cuerpo["system"][0]["text"]
    assert "server-side-fallback-2026-07-01" in solicitud["cabeceras"]["anthropic-beta"]


def test_el_esquema_solo_admite_codigos_del_catalogo():
    definicion = esquema(CATALOGO)
    assert definicion["properties"]["instalaciones"]["items"]["enum"] == ["EB-02"]
    assert definicion["properties"]["tipos"]["items"]["enum"] == ["DU", "MC"]
    assert definicion["required"] == list(definicion["properties"]) and definicion["additionalProperties"] is False


@pytest.mark.parametrize("respuesta_api, estado", [
    ({"estado": 500}, 502), ({"estado": 429}, 503), ({"stop_reason": "refusal"}, 502),
    ({"datos": FILTROS, "stop_reason": "max_tokens"}, 502),
])
def test_errores_de_la_api_se_informan_sin_filtrar_detalles(cliente, api_simulada, respuesta_api, estado):
    api_simulada.state.respuestas = [respuesta_api]
    respuesta = cliente.post("/api/interpretar", json={"consulta": "planos eléctricos"})
    assert respuesta.status_code == estado and "clave-de-prueba" not in respuesta.text


def test_consulta_vacia_o_demasiado_larga(cliente):
    assert cliente.post("/api/interpretar", json={"consulta": ""}).status_code == 422
    assert cliente.post("/api/interpretar", json={"consulta": "x" * 301}).status_code == 422


def test_sin_credenciales_la_pagina_usa_sus_reglas_locales(ruta_catalogo, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    sin_ia = TestClient(crear_app(None, ruta_catalogo))
    assert sin_ia.get("/api/salud").json() == {"estado": "ok", "ia": False}
    assert sin_ia.post("/api/interpretar", json={"consulta": "planos"}).status_code == 503
    assert sin_ia.get("/").status_code == 200 and "Biblioteca técnica" in sin_ia.get("/").text


def test_con_credenciales_lo_informa(cliente):
    assert cliente.get("/api/salud").json() == {"estado": "ok", "ia": True}
