"""Pruebas de la lógica de grafos escrita en SQL."""

from __future__ import annotations

import sqlite3

import pytest

from averias import bd

TODOS_LOS_SECTORES = {f"SEC-0{n}" for n in range(1, 10)}


def test_red_intacta_abastece_todo(red):
    assert red.sin_suministro() == set()
    resumen = bd.consultar(red.conexion, "resumen")[0]
    assert resumen["averias_abiertas"] == 0
    assert resumen["clientes_sin_suministro"] == 0
    assert resumen["clientes_totales"] == 36150


def test_falla_en_ramal_terminal_corta_solo_su_sector(red):
    red.averiar("TUB-019")
    assert red.sin_suministro() == {"SEC-05"}
    resumen = bd.consultar(red.conexion, "resumen")[0]
    assert resumen["clientes_sin_suministro"] == 650
    assert resumen["elementos_fuera_servicio"] == 1


def test_averia_que_no_detiene_el_elemento_no_corta_suministro(red):
    red.averiar("TUB-019", fuera_servicio=False, tipo_falla="fuga")
    assert red.sin_suministro() == set()


def test_la_interconexion_cubre_la_caida_de_un_sistema(red):
    """Sin el bombeo norte, el agua llega desde el sur por los arcos bidireccionales."""
    red.averiar("EB-01")
    assert red.sin_suministro() == set()
    estados = {f["codigo"]: f["estado"] for f in bd.consultar(red.conexion, "red_nodos")}
    assert estados["EB-01"] == "fuera_servicio"
    assert estados["EST-01"] == "sin_suministro"   # solo se alimenta desde EB-01
    assert estados["UN-01"] == "operativo"         # recibe por la interconexión


def test_sin_ambos_sistemas_no_hay_suministro(red):
    red.averiar("EB-01")
    red.averiar("EB-02")
    assert red.sin_suministro() == TODOS_LOS_SECTORES


def test_cerrar_la_averia_restablece_el_suministro(red):
    averia_id = red.averiar("TUB-012")
    assert red.sin_suministro() == {"SEC-01", "SEC-02"}
    red.cerrar(averia_id)
    assert red.sin_suministro() == set()


def test_simulacion_no_modifica_datos_y_marca_lo_nuevo(red):
    red.averiar("TUB-019")                                    # SEC-05 ya está sin agua
    tipo, elemento_id = red.elemento("UN-05")
    filas = bd.consultar(red.conexion, "impacto_simulado", elemento_tipo=tipo, elemento_id=elemento_id)
    por_codigo = {f["codigo"]: f for f in filas}

    assert {c for c, f in por_codigo.items() if f["tipo"] == "sector"} == {"SEC-05", "SEC-06", "SEC-07"}
    assert por_codigo["SEC-05"]["nuevo"] == 0                 # ya estaba afectado
    assert por_codigo["SEC-06"]["nuevo"] == 1
    assert red.sin_suministro() == {"SEC-05"}                 # la simulación no escribió nada


def test_criticidad_coincide_con_un_recorrido_independiente(red):
    """Compara el análisis N-1 en SQL con un recorrido del grafo hecho en Python."""
    nodos, arcos = red.nodos, red.arcos
    captaciones = [n["id"] for n in nodos.values() if n["tipo"] == "captacion"]
    sectores = {n["id"]: n["clientes"] for n in nodos.values() if n["tipo"] == "sector"}

    def clientes_afectados(tipo, elemento_id):
        vecinos = {}
        for arco in arcos.values():
            if tipo == "arco" and arco["id"] == elemento_id:
                continue
            vecinos.setdefault(arco["origen_id"], []).append(arco["destino_id"])
            if arco["bidireccional"]:
                vecinos.setdefault(arco["destino_id"], []).append(arco["origen_id"])
        bloqueado = elemento_id if tipo == "nodo" else None
        pendientes = [c for c in captaciones if c != bloqueado]
        vistos = set(pendientes)
        while pendientes:
            for vecino in vecinos.get(pendientes.pop(), []):
                if vecino != bloqueado and vecino not in vistos:
                    vistos.add(vecino)
                    pendientes.append(vecino)
        return sum(c for sector, c in sectores.items() if sector not in vistos)

    filas = bd.consultar(red.conexion, "criticidad")
    assert len(filas) == len(arcos) + sum(1 for n in nodos.values() if n["tipo"] != "sector")
    for fila in filas:
        esperado = clientes_afectados(fila["elemento_tipo"], fila["elemento_id"])
        assert fila["clientes_afectados"] == esperado, fila["codigo"]

    assert filas[0]["codigo"] == "UN-01" and filas[0]["clientes_afectados"] == 12900
    assert filas[0]["posicion"] == 1
    troncal = next(f for f in filas if f["codigo"] == "TUB-005")
    assert troncal["clientes_afectados"] == 0                 # cubierto por la redundancia


def test_ruta_de_suministro_mas_corta(red):
    destino = red.nodos["SEC-01"]["id"]
    ruta = bd.consultar(red.conexion, "ruta_suministro", nodo_id=destino)[0]
    arcos = [int(a) for a in ruta["ruta_arcos"].strip("/").split("/")]
    codigos = {a["id"]: a["codigo"] for a in red.arcos.values()}
    assert [codigos[a] for a in arcos] == [
        "TUB-001", "TUB-003", "TUB-005", "TUB-007", "TUB-011", "TUB-012", "TUB-013"]
    assert ruta["longitud_m"] == 11050
    assert ruta["saltos"] == 7


def test_ruta_de_suministro_se_desvia_cuando_falla_un_tramo(red):
    red.averiar("EB-01")
    destino = red.nodos["SEC-01"]["id"]
    ruta = bd.consultar(red.conexion, "ruta_suministro", nodo_id=destino)[0]
    assert ruta["longitud_m"] == 14330                        # ahora llega desde el sur
    nodos = [int(n) for n in ruta["ruta_nodos"].strip("/").split("/")]
    assert nodos[0] == red.nodos["CAP-02"]["id"]


def test_ruta_de_suministro_vacia_si_no_llega_agua(red):
    red.averiar("TUB-019")
    assert bd.consultar(red.conexion, "ruta_suministro", nodo_id=red.nodos["SEC-05"]["id"]) == []


def test_una_averia_afecta_a_exactamente_un_elemento(conexion):
    with pytest.raises(sqlite3.IntegrityError):
        conexion.execute(
            "INSERT INTO averia (codigo, nodo_id, arco_id, tipo_falla, severidad, reportada_por) "
            "VALUES ('X-1', 1, 1, 'fuga', 'baja', 'p')")
    with pytest.raises(sqlite3.IntegrityError):
        conexion.execute(
            "INSERT INTO averia (codigo, tipo_falla, severidad, reportada_por) "
            "VALUES ('X-2', 'fuga', 'baja', 'p')")


def test_el_tipo_de_falla_debe_corresponder_al_elemento(red):
    with pytest.raises(sqlite3.IntegrityError, match="tipo de falla"):
        red.averiar("EB-01", tipo_falla="rotura")             # rotura aplica solo a arcos


def test_una_averia_cerrada_no_se_puede_modificar(red):
    averia_id = red.averiar("TUB-019")
    red.cerrar(averia_id)
    with pytest.raises(sqlite3.IntegrityError, match="cerrada"):
        with red.conexion:
            bd.ejecutar(red.conexion, "averia_cambiar_estado", id=averia_id,
                        estado="en_reparacion", usuario="otro@ejemplo.com")


def test_la_bitacora_registra_cada_cambio_de_estado(red):
    averia_id = red.averiar("EB-03")
    with red.conexion:
        bd.ejecutar(red.conexion, "averia_cambiar_estado", id=averia_id, estado="en_reparacion",
                    usuario="cuadrilla@ejemplo.com")
    red.cerrar(averia_id)
    eventos = bd.consultar(red.conexion, "averia_eventos", id=averia_id)
    assert [e["estado"] for e in eventos] == ["abierta", "en_reparacion", "cerrada"]
    assert eventos[1]["usuario"] == "cuadrilla@ejemplo.com"


def test_consulta_inexistente(conexion):
    with pytest.raises(KeyError):
        bd.consultar(conexion, "no_existe")
