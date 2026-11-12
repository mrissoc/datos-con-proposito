"""ETL de indicadores y actualización de Power BI, sin depender de servicios externos."""

from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest
import responses

import etl
from actualizar_powerbi import API, ClientePowerBI, ErrorPowerBI

CATALOGO = pd.DataFrame([
    {"codigo": "dolar", "nombre": "Dólar observado", "unidad_medida": "Pesos"},
    {"codigo": "tpm", "nombre": "Tasa Política Monetaria (TPM)", "unidad_medida": "Porcentaje"},
])


def _serie(codigo, valores):
    """Filas como las entrega la API: fecha a medianoche de Chile expresada en UTC."""
    return [{"codigo": codigo, "fecha": f"{fecha}T03:00:00.000Z", "valor": valor} for fecha, valor in valores]


def test_transformar_tipa_valida_y_quita_duplicados():
    bruto = pd.DataFrame(_serie("dolar", [("2026-01-02", 900.5), ("2026-01-02", 901.0), ("2026-01-05", "x"),
                                          ("fecha-mala", 905)]) + _serie("tpm", [("2026-01-02", 4.5)]))
    limpio = etl.transformar(bruto)
    assert limpio.to_dict("records") == [
        {"codigo": "dolar", "fecha": "2026-01-02", "valor": 901.0},       # se queda el último duplicado
        {"codigo": "tpm", "fecha": "2026-01-02", "valor": 4.5},
    ]


@pytest.fixture
def base():
    conexion = sqlite3.connect(":memory:")
    conexion.row_factory = sqlite3.Row
    valores = pd.DataFrame(
        [{"codigo": "dolar", "fecha": f"2025-{m:02d}-15", "valor": 900 + m} for m in range(1, 13)]
        + [{"codigo": "dolar", "fecha": "2026-01-15", "valor": 1000.0}]
        + [{"codigo": "tpm", "fecha": "2025-01-15", "valor": 5.0}, {"codigo": "tpm", "fecha": "2026-01-15", "valor": 4.5}])
    etl.cargar(conexion, CATALOGO, valores, filas_leidas=len(valores))
    yield conexion
    conexion.close()


def test_la_carga_es_idempotente(base):
    valores = pd.DataFrame([{"codigo": "dolar", "fecha": "2026-01-15", "valor": 1010.0}])
    etl.cargar(base, CATALOGO, valores, filas_leidas=1)
    assert base.execute("SELECT COUNT(*) FROM fact_valor").fetchone()[0] == 15          # actualiza, no duplica
    assert base.execute("SELECT valor FROM fact_valor WHERE fecha = '2026-01-15' AND codigo = 'dolar'").fetchone()[0] == 1010
    assert base.execute("SELECT COUNT(*) FROM etl_ejecucion").fetchone()[0] == 2


def test_variaciones_mensuales_y_en_doce_meses(base):
    filas = {(f["codigo"], f["mes"]): dict(f) for f in base.execute("SELECT * FROM vw_mensual")}
    enero = filas[("dolar", "2026-01-01")]
    assert enero["var_mensual_pct"] == round(100 * (1000 - 912) / 912, 2)
    assert enero["var_12m_pct"] == round(100 * (1000 - 901) / 901, 2)
    assert filas[("dolar", "2025-01-01")]["var_mensual_pct"] is None
    # En las tasas no tiene sentido una variación porcentual: se informa la diferencia en puntos.
    tpm = filas[("tpm", "2026-01-01")]
    assert tpm["var_12m_pct"] is None and tpm["dif_12m"] == -0.5
    # Un mes sin datos no desplaza la comparación: el rezago se busca por fecha.
    assert filas[("tpm", "2026-01-01")]["dif_mensual"] is None


@responses.activate
def test_extraer_reintenta_y_arma_el_catalogo(monkeypatch):
    monkeypatch.setattr(etl.time, "sleep", lambda segundos: None)
    url = etl.API_URL.format(codigo="dolar", anio=2026)
    responses.get(url, status=503)
    responses.get(url, json={"codigo": "dolar", "nombre": "Dólar observado", "unidad_medida": "Pesos",
                             "serie": [{"fecha": "2026-01-02T03:00:00.000Z", "valor": 900.5}]})
    catalogo, bruto = etl.extraer(["dolar"], 2026, 2026)
    assert catalogo.to_dict("records") == [{"codigo": "dolar", "nombre": "Dólar observado", "unidad_medida": "Pesos"}]
    assert len(bruto) == 1 and len(responses.calls) == 2


# --------------------------------------------------------------- Power BI

RUTA = f"{API}/groups/area/datasets/modelo/refreshes"


@responses.activate
def test_power_bi_espera_hasta_que_la_actualizacion_termina():
    responses.post(RUTA, status=202)
    responses.get(RUTA, json={"value": [{"status": "Unknown"}]})
    responses.get(RUTA, json={"value": [{"status": "Completed", "endTime": "2026-11-12T08:00:00Z"}]})
    esperas = []
    resultado = ClientePowerBI(lambda: "token", espera=esperas.append).actualizar("area", "modelo", intervalo=5)
    assert resultado["status"] == "Completed" and esperas == [5, 5]
    assert json.loads(responses.calls[0].request.body) == {"notifyOption": "NoNotification"}
    assert responses.calls[0].request.headers["Authorization"] == "Bearer token"


@responses.activate
def test_power_bi_informa_una_actualizacion_fallida():
    responses.post(RUTA, status=202)
    responses.get(RUTA, json={"value": [{"status": "Failed", "serviceExceptionJson": "{\"errorCode\":\"ModelRefreshFailed\"}"}]})
    with pytest.raises(ErrorPowerBI, match="ModelRefreshFailed"):
        ClientePowerBI(lambda: "token", espera=lambda s: None).actualizar("area", "modelo")


@responses.activate
def test_power_bi_sin_permisos():
    responses.post(RUTA, status=401, json={"error": {"code": "PowerBINotAuthorizedException", "message": "Unauthorized"}})
    with pytest.raises(ErrorPowerBI, match="401"):
        ClientePowerBI(lambda: "token").actualizar("area", "modelo")
