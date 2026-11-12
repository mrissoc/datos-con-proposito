"""Fixtures compartidas: una red intacta por prueba, sin averías."""

from __future__ import annotations

import pytest

from averias import bd
from averias.construir_bd import construir


@pytest.fixture
def ruta_bd(tmp_path):
    return construir(tmp_path / "red.db", averias=0, con_abiertas=False)


@pytest.fixture
def conexion(ruta_bd):
    conexion = bd.conectar(ruta_bd)
    yield conexion
    conexion.close()


class Red:
    """Atajos para escribir las pruebas con códigos (TUB-019) en vez de ids."""

    def __init__(self, conexion):
        self.conexion = conexion
        self.nodos = {f["codigo"]: f for f in bd.consultar(conexion, "red_nodos")}
        self.arcos = {f["codigo"]: f for f in bd.consultar(conexion, "red_arcos")}

    def elemento(self, codigo):
        if codigo in self.nodos:
            return "nodo", self.nodos[codigo]["id"]
        return "arco", self.arcos[codigo]["id"]

    def averiar(self, codigo, fuera_servicio=True, tipo_falla=None):
        """Registra una avería sobre el elemento y devuelve su id."""
        tipo, elemento_id = self.elemento(codigo)
        tipo_falla = tipo_falla or ("electrica" if tipo == "nodo" else "rotura")
        with self.conexion:
            cursor = bd.ejecutar(
                self.conexion, "averia_crear", elemento_tipo=tipo, elemento_id=elemento_id,
                tipo_falla=tipo_falla, severidad="alta", fuera_servicio=int(fuera_servicio),
                descripcion="prueba", usuario="prueba@ejemplo.com")
        return cursor.lastrowid

    def cerrar(self, averia_id):
        with self.conexion:
            bd.ejecutar(self.conexion, "averia_cambiar_estado", id=averia_id, estado="cerrada",
                        usuario="prueba@ejemplo.com")

    def sin_suministro(self):
        """Códigos de los sectores que hoy no reciben agua."""
        return {f["codigo"] for f in bd.consultar(self.conexion, "red_nodos")
                if f["tipo"] == "sector" and f["estado"] != "operativo"}


@pytest.fixture
def red(conexion):
    return Red(conexion)
