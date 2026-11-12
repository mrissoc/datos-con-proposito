"""Pruebas del cliente OData y de la sincronización, contra el simulador de SAP."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from integracion_sap.cliente import ClienteSap, ErrorSap, a_fecha_odata, de_fecha_odata, literal
from integracion_sap.simulador import RUTA_SERVICIO
from integracion_sap.sincronizar import sincronizar

AVISO = {"NotificationType": "M2", "NotificationText": "Falla de bomba EB-01", "Equipment": "10000005",
         "MaintPriority": "1", "ExternalReference": "AV-PRUEBA-1"}


def _solicitudes(simulador, metodo, sufijo=""):
    return [s for s in simulador.app.state.solicitudes if s[0] == metodo and s[1].endswith(sufijo)]


# ------------------------------------------------------------------ Cliente

def test_fechas_odata_ida_y_vuelta():
    momento = datetime(2026, 11, 11, 14, 30, 5, tzinfo=timezone.utc)
    assert a_fecha_odata(momento) == "/Date(1794407405000)/"
    assert de_fecha_odata("/Date(1794407405000)/") == momento
    assert de_fecha_odata("/Date(1794407405000+0000)/") == momento
    with pytest.raises(ValueError):
        de_fecha_odata("2026-11-11")


def test_literal_escapa_comillas():
    assert literal("O'Higgins") == "'O''Higgins'"


def test_lee_todos_los_equipos_siguiendo_la_paginacion_del_servidor(simulador, sap):
    equipos = list(sap.leer("Equipment"))
    assert len(equipos) == len(simulador.app.state.equipos) == 45
    assert len({e["Equipment"] for e in equipos}) == 45                     # sin repetidos
    assert len(_solicitudes(simulador, "GET", "/Equipment")) == 5            # 45 filas en páginas de 10


def test_lee_todo_con_ventanas_mas_chicas_que_la_pagina_del_servidor(simulador, sap):
    equipos = list(sap.leer("Equipment", tamano_pagina=7))
    assert [e["Equipment"] for e in equipos] == [e["Equipment"] for e in simulador.app.state.equipos]


def test_filtra_por_campo(sap):
    equipos = list(sap.leer("Equipment", filtro="TechnicalIdentNumber eq 'EB-03'"))
    assert [e["EquipmentName"] for e in equipos] == ["Estación de Bombeo Oriente"]


def test_credenciales_incorrectas(simulador):
    cliente = ClienteSap(simulador.url + RUTA_SERVICIO, "demo", "otra", espera=lambda s: None)
    with pytest.raises(ErrorSap) as error:
        list(cliente.leer("Equipment"))
    assert error.value.estado == 401 and "incorrectos" in error.value.mensaje


def test_crear_pide_el_token_csrf_una_sola_vez(simulador, sap):
    primero = sap.crear("MaintenanceNotification", AVISO)
    segundo = sap.crear("MaintenanceNotification", {**AVISO, "ExternalReference": "AV-PRUEBA-2"})
    assert primero["MaintenanceNotification"] != segundo["MaintenanceNotification"]
    assert len(simulador.app.state.avisos) == 2
    assert len(_solicitudes(simulador, "GET", RUTA_SERVICIO + "/")) == 1     # un solo Fetch


def test_token_vencido_se_renueva_y_la_operacion_se_completa(simulador, sap):
    sap.crear("MaintenanceNotification", AVISO)
    simulador.app.state.fallas["vencer_tokens"] = True                      # la sesión de SAP expira
    sap.crear("MaintenanceNotification", {**AVISO, "ExternalReference": "AV-PRUEBA-2"})
    assert len(simulador.app.state.avisos) == 2
    assert len(_solicitudes(simulador, "GET", RUTA_SERVICIO + "/")) == 2     # pidió un token nuevo


def test_las_lecturas_se_reintentan_ante_errores_transitorios(simulador, sap):
    simulador.app.state.fallas["get_503"] = 2
    assert len(list(sap.leer("Equipment", filtro="EquipmentCategory eq 'V'"))) == 1


def test_una_creacion_limitada_por_volumen_se_reintenta(simulador, sap):
    simulador.app.state.fallas["post_429"] = 1                              # SAP no procesó la solicitud
    sap.crear("MaintenanceNotification", AVISO)
    assert len(simulador.app.state.avisos) == 1


def test_una_creacion_con_error_del_servidor_no_se_repite_a_ciegas(simulador, sap):
    simulador.app.state.fallas["post_503"] = 1
    with pytest.raises(ErrorSap) as error:
        sap.crear("MaintenanceNotification", AVISO)
    assert error.value.estado == 503
    assert len(_solicitudes(simulador, "POST")) == 1                         # un solo intento


def test_error_de_validacion_trae_el_mensaje_de_sap(sap):
    with pytest.raises(ErrorSap) as error:
        sap.crear("MaintenanceNotification", {**AVISO, "NotificationText": "x" * 41})
    assert error.value.estado == 400 and error.value.codigo == "ZPM/022"
    assert "40 caracteres" in error.value.mensaje


def test_actualizar_un_aviso(simulador, sap):
    numero = sap.crear("MaintenanceNotification", AVISO)["MaintenanceNotification"]
    sap.actualizar("MaintenanceNotification", numero, {"NotificationPhase": "4"})
    assert simulador.app.state.avisos[numero]["NotificationPhase"] == "4"
    with pytest.raises(ErrorSap) as error:
        sap.actualizar("MaintenanceNotification", "999", {"NotificationPhase": "4"})
    assert error.value.estado == 404


# ----------------------------------------------------------- Sincronización

def test_vincula_equipos_e_informa_las_diferencias(conexion, sap):
    resultado = sincronizar(conexion, sap)
    assert resultado.equipos_vinculados == 43                 # 16 nodos y 27 tramos
    assert resultado.elementos_sin_equipo == ["UN-05"]        # está en la red y no en SAP
    assert len(resultado.equipos_sin_elemento) == 2           # camión y grupo electrógeno
    fila = conexion.execute("SELECT id_externo FROM nodo WHERE codigo = 'EB-03'").fetchone()
    assert fila["id_externo"] == "10000007"
    assert conexion.execute("SELECT COUNT(*) FROM arco WHERE id_externo IS NULL").fetchone()[0] == 0
    assert conexion.execute(
        "SELECT COUNT(*) FROM nodo WHERE tipo = 'sector' AND id_externo IS NOT NULL").fetchone()[0] == 0


def test_envia_cada_averia_abierta_una_sola_vez(conexion, sap, simulador):
    primera = sincronizar(conexion, sap)
    assert primera.avisos_creados == 3 and primera.errores == []
    avisos = simulador.app.state.avisos
    por_referencia = {a["ExternalReference"]: a for a in avisos.values()}
    rotura = por_referencia[conexion.execute(
        "SELECT codigo FROM v_averia WHERE elemento_codigo = 'TUB-019' AND estado <> 'cerrada'").fetchone()[0]]
    assert rotura["NotificationText"] == "Rotura de tubería TUB-019"
    assert rotura["MaintPriority"] == "1" and rotura["MaintenanceObjectIsDown"] is True
    assert rotura["MalfunctionStartDate"].startswith("/Date(")
    assert all(len(a["NotificationText"]) <= 40 for a in avisos.values())

    segunda = sincronizar(conexion, sap)
    assert segunda.avisos_creados == 0 and segunda.avisos_recuperados == 0
    assert len(avisos) == 3
    guardados = conexion.execute(
        "SELECT COUNT(*) FROM averia WHERE estado <> 'cerrada' AND id_externo IS NOT NULL").fetchone()[0]
    assert guardados == 3


def test_recupera_el_aviso_si_ya_existe_en_sap(conexion, sap, simulador):
    """Si una ejecución anterior creó el aviso y se cortó antes de guardarlo, no lo duplica."""
    codigo = conexion.execute("SELECT codigo FROM v_averia WHERE elemento_codigo = 'EB-02' AND estado <> 'cerrada'").fetchone()[0]
    previo = sap.crear("MaintenanceNotification", {**AVISO, "ExternalReference": codigo})

    resultado = sincronizar(conexion, sap)
    assert resultado.avisos_recuperados == 1 and resultado.avisos_creados == 2
    assert len(simulador.app.state.avisos) == 3
    guardado = conexion.execute("SELECT id_externo FROM averia WHERE codigo = ?", (codigo,)).fetchone()[0]
    assert guardado == previo["MaintenanceNotification"]


def test_un_error_no_detiene_a_las_demas_y_se_recupera_despues(conexion, sap, simulador):
    simulador.app.state.fallas["post_503"] = 1
    primera = sincronizar(conexion, sap)
    assert primera.avisos_creados == 2 and len(primera.errores) == 1
    pendiente = conexion.execute(
        "SELECT ultimo_error, intentos FROM sap_aviso WHERE aviso IS NULL").fetchone()
    assert "503" in pendiente["ultimo_error"] and pendiente["intentos"] == 1

    segunda = sincronizar(conexion, sap)
    assert segunda.avisos_creados == 1 and segunda.errores == []
    assert len(simulador.app.state.avisos) == 3
    assert conexion.execute("SELECT COUNT(*) FROM sap_aviso WHERE ultimo_error IS NOT NULL").fetchone()[0] == 0


def test_averia_sobre_elemento_sin_equipo_queda_informada(conexion, sap):
    nodo = conexion.execute("SELECT id FROM nodo WHERE codigo = 'UN-05'").fetchone()[0]
    with conexion:
        conexion.execute(
            "INSERT INTO averia (codigo, nodo_id, tipo_falla, severidad, reportada_por) "
            "VALUES ('AV-SIN-EQUIPO', ?, 'control', 'media', 'prueba')", (nodo,))
    resultado = sincronizar(conexion, sap)
    assert resultado.avisos_creados == 3
    assert resultado.errores == ["AV-SIN-EQUIPO: UN-05 no tiene equipo en SAP"]


def test_informa_a_sap_el_cierre_de_las_averias(conexion, sap, simulador):
    sincronizar(conexion, sap)
    averia = conexion.execute("SELECT id, id_externo FROM averia WHERE estado <> 'cerrada' LIMIT 1").fetchone()
    with conexion:
        conexion.execute(
            "UPDATE averia SET estado = 'cerrada', cerrada_en = '2026-11-11T20:00:00Z' WHERE id = ?", (averia["id"],))

    resultado = sincronizar(conexion, sap)
    assert resultado.avisos_cerrados == 1
    aviso = simulador.app.state.avisos[averia["id_externo"]]
    assert aviso["NotificationPhase"] == "4"
    assert de_fecha_odata(aviso["MalfunctionEndDate"]) == datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)
    assert sincronizar(conexion, sap).avisos_cerrados == 0     # no se informa dos veces


def test_puede_enviar_tambien_el_historial_cerrado(conexion, sap, simulador):
    resultado = sincronizar(conexion, sap, incluir_cerradas=True)
    assert resultado.avisos_creados == 9                        # 3 abiertas y 6 históricas
    assert resultado.avisos_cerrados == 6
    assert "Avisos creados: 9" in resultado.resumen()
