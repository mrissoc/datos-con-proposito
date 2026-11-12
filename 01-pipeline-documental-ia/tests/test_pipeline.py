"""El pipeline de punta a punta sobre un repositorio sintético, y la migración ante fallas."""

from __future__ import annotations

import json

import pytest

from pipeline import bd
from pipeline.almacenes import ErrorTransitorio
from pipeline.almacenes.local import AlmacenLocal
from pipeline.almacenes.sintetico import AlmacenSintetico
from pipeline.etapas import catalogo, clasificacion, deduplicacion, inventario, migracion
from pipeline.evaluacion import evaluar
from pipeline.sintetico.repositorio import Generador, extensiones_raras


def ejecutar(conexion, almacen, taxonomia, reglas):
    inventario.inventariar(conexion, almacen)
    inventario.perfilar(conexion, almacen, reglas)
    clasificacion.clasificar(conexion, taxonomia)
    deduplicacion.deduplicar(conexion)
    return migracion.planificar(conexion, taxonomia, reglas)


@pytest.fixture
def corrida(repositorio, tmp_path, taxonomia, reglas):
    conexion = bd.conectar(tmp_path / "staging.db")
    almacen = AlmacenLocal(repositorio["carpeta"], tmp_path / "biblioteca")
    plan = ejecutar(conexion, almacen, taxonomia, reglas)
    yield {"conexion": conexion, "almacen": almacen, "plan": plan, "destino": tmp_path / "biblioteca"}
    conexion.close()


def test_el_generador_es_determinista(taxonomia):
    primera = [a.origen_id for a in Generador(taxonomia, 5).archivos(20, 10, 10)]
    segunda = [a.origen_id for a in Generador(taxonomia, 5).archivos(20, 10, 10)]
    assert primera == segunda and len(set(primera)) == len(primera)
    assert len({extension for extension, _ in extensiones_raras()}) > 300


def test_inventario_completo_y_repetible(corrida, repositorio, taxonomia, reglas):
    conexion = corrida["conexion"]
    total = conexion.execute("SELECT COUNT(*) FROM archivo").fetchone()[0]
    assert total == len(repositorio["verdad"])
    antes = conexion.execute("SELECT COUNT(*), SUM(duplicado_de IS NOT NULL), SUM(estado_migracion = 'pendiente') "
                             "FROM archivo").fetchone()
    ejecutar(conexion, corrida["almacen"], taxonomia, reglas)              # segunda pasada completa
    despues = conexion.execute("SELECT COUNT(*), SUM(duplicado_de IS NOT NULL), SUM(estado_migracion = 'pendiente') "
                               "FROM archivo").fetchone()
    assert tuple(antes) == tuple(despues)


def test_copias_exactas_coinciden_con_la_verdad(corrida, repositorio):
    conexion, verdad = corrida["conexion"], repositorio["verdad"]
    filas = {f["id"]: f for f in conexion.execute("SELECT id, origen_id, duplicado_de FROM archivo")}
    marcadas = [f for f in filas.values() if f["duplicado_de"] is not None]
    for fila in marcadas:
        canonico = filas[fila["duplicado_de"]]
        assert verdad[fila["origen_id"]]["contenido"] == verdad[canonico["origen_id"]]["contenido"]
        assert canonico["duplicado_de"] is None                           # el canónico no es copia de otro
    contenidos = [v["contenido"] for v in verdad.values()]
    assert len(marcadas) == len(contenidos) - len(set(contenidos))


def test_lo_clasificado_por_nombre_es_correcto(corrida, repositorio):
    verdad = repositorio["verdad"]
    filas = corrida["conexion"].execute(
        "SELECT origen_id, proyecto, disciplina, tipo_doc, numero, hoja, revision FROM archivo "
        "WHERE clase = 'plano' AND patron IS NOT NULL").fetchall()
    assert len(filas) > 100
    for fila in filas:
        real = verdad[fila["origen_id"]]
        for columna, clave in [("proyecto", "proyecto"), ("disciplina", "disciplina"), ("tipo_doc", "tipo"),
                               ("numero", "numero"), ("hoja", "hoja"), ("revision", "revision")]:
            assert fila[columna] in (None, real[clave]), (fila["origen_id"], columna)


def test_perfilado_detecta_basura_y_tipos_disfrazados(corrida, repositorio):
    verdad = repositorio["verdad"]
    for fila in corrida["conexion"].execute("SELECT origen_id, categoria, extension_real, tipo_por_firma FROM archivo"):
        real = verdad[fila["origen_id"]]
        if real["clase"] == "basura":
            assert fila["categoria"] in ("temporal", "sistema"), fila["origen_id"]
        if real.get("tipo_real") in ("dwg", "pdf", "doc"):
            assert fila["tipo_por_firma"] == 1 and fila["extension_real"] == real["tipo_real"], fila["origen_id"]


def test_solo_una_revision_vigente_por_plano(corrida):
    repetidos = corrida["conexion"].execute(
        "SELECT proyecto, disciplina, numero, hoja, COUNT(DISTINCT revision) AS n FROM archivo "
        "WHERE vigente = 1 AND clase = 'plano' AND proyecto IS NOT NULL "
        "GROUP BY proyecto, disciplina, numero, hoja HAVING n > 1").fetchall()
    assert repetidos == []


def test_migracion_copia_normaliza_y_no_repite(corrida):
    conexion, almacen = corrida["conexion"], corrida["almacen"]
    primera = migracion.migrar(conexion, almacen, espera=lambda s: None)
    assert primera["migrados"] == corrida["plan"]["pendiente"] and primera["errores"] == 0
    fila = conexion.execute("SELECT destino, proyecto, numero, hash FROM archivo WHERE estado_migracion = 'migrado' "
                            "AND clase = 'plano' LIMIT 1").fetchone()
    archivo = corrida["destino"] / fila["destino"]
    assert archivo.is_file() and fila["destino"].startswith("Planos/")
    metadatos = json.loads(archivo.with_name(archivo.name + ".json").read_text(encoding="utf-8"))
    assert metadatos["Proyecto"] == fila["proyecto"] and metadatos["Huella"] == fila["hash"]
    # Las copias y los temporales nunca llegan a la biblioteca.
    assert conexion.execute("SELECT COUNT(*) FROM archivo WHERE estado_migracion IS NOT NULL AND "
                            "(duplicado_de IS NOT NULL OR categoria IN ('temporal', 'sistema'))").fetchone()[0] == 0
    assert migracion.migrar(conexion, almacen)["migrados"] == 0             # nada pendiente


def test_ediciones_sin_nueva_revision_quedan_apartadas(taxonomia, reglas, tmp_path):
    archivos = [a for a in Generador(taxonomia, 11).archivos(400, 0, 0)]
    assert any(a.verdad.get("conflicto") for a in archivos)
    conexion = bd.conectar(tmp_path / "s.db")
    plan = ejecutar(conexion, AlmacenSintetico(archivos), taxonomia, reglas)
    assert plan["conflicto"] >= 2
    nota = conexion.execute("SELECT nota_revision FROM archivo WHERE estado_migracion = 'conflicto'").fetchone()[0]
    assert "contenido distinto" in nota
    repetidos = conexion.execute("SELECT COUNT(*) FROM (SELECT destino FROM archivo WHERE estado_migracion = 'pendiente' "
                                 "GROUP BY destino HAVING COUNT(*) > 1)").fetchone()[0]
    assert repetidos == 0


def test_migracion_reintenta_ante_limites_del_servicio(taxonomia, reglas, tmp_path):
    archivos = list(Generador(taxonomia, 4).archivos(60, 20, 0))
    almacen = AlmacenSintetico(archivos, limite_cada=3)                    # una de cada tres llamadas es rechazada
    conexion = bd.conectar(tmp_path / "s.db")
    plan = ejecutar(conexion, almacen, taxonomia, reglas)
    esperas = []
    resultado = migracion.migrar(conexion, almacen, hilos=4, espera=esperas.append)
    assert resultado["migrados"] == plan["pendiente"] and resultado["errores"] == 0
    assert resultado["reintentos"] > 0 and len(esperas) == resultado["reintentos"]
    assert len(almacen.reubicados) == plan["pendiente"]


def test_un_archivo_con_error_no_detiene_la_migracion_y_se_retoma(taxonomia, reglas, tmp_path):
    class AlmacenConFalla(AlmacenSintetico):
        fallar = True

        def reubicar(self, origen_id, destino, metadatos):
            if self.fallar and "0101" in destino:
                raise ErrorTransitorio("servicio no disponible", esperar=0)
            return super().reubicar(origen_id, destino, metadatos)

    almacen = AlmacenConFalla(list(Generador(taxonomia, 4).archivos(60, 0, 0)))
    conexion = bd.conectar(tmp_path / "s.db")
    plan = ejecutar(conexion, almacen, taxonomia, reglas)
    primera = migracion.migrar(conexion, almacen, max_intentos=2, espera=lambda s: None)
    assert primera["errores"] > 0 and primera["migrados"] + primera["errores"] == plan["pendiente"]
    error = conexion.execute("SELECT error, intentos FROM archivo WHERE estado_migracion = 'error'").fetchone()
    assert error["error"] == "servicio no disponible" and error["intentos"] == 2

    almacen.fallar = False                                                 # el servicio se recupera
    segunda = migracion.migrar(conexion, almacen, espera=lambda s: None)
    assert segunda["migrados"] == primera["errores"] and segunda["errores"] == 0


def test_catalogo_reporte_y_evaluacion(corrida, repositorio, taxonomia, tmp_path):
    conexion = corrida["conexion"]
    salida = tmp_path / "catalogo.json"
    cuenta = catalogo.exportar(conexion, taxonomia, salida)
    datos = json.loads(salida.read_text(encoding="utf-8"))
    assert cuenta["entradas"] == len(datos["items"]) > 30
    item = next(i for i in datos["items"] if i["clase"] == "plano" and len(i["revisiones"]) > 1)
    assert item["revisiones"][0]["vigente"] and not item["revisiones"][-1]["vigente"]
    assert item["revision"] == item["revisiones"][0]["revision"]
    assert {"disciplinas", "tipos_plano", "tipos_documento", "proyectos"} <= set(datos["taxonomia"])
    assert len({(i["clase"], i["proyecto"], i["disciplina"], i["tipo"], i["numero"], i["hoja"]) for i in datos["items"]}) \
        == len(datos["items"])

    texto = catalogo.reporte(conexion)
    assert "Archivos inventariados" in texto and "Extensiones distintas" in texto
    evaluacion = evaluar(conexion, repositorio["verdad"])
    assert "| proyecto |" in evaluacion and "Copias exactas marcadas" in evaluacion
