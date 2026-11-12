"""Lectura de cajetines: el SDK real de Anthropic contra una API simulada.

Se comprueba lo que la aplicación envía (modelo, esquema, imagen) y cómo trata
cada tipo de respuesta. La calidad de lectura del modelo no se prueba aquí: se
mide con `python -m pipeline evaluar` después de una corrida real.
"""

from __future__ import annotations

import anthropic
import pytest

from pipeline import bd
from pipeline.almacenes.local import AlmacenLocal
from pipeline.etapas import clasificacion, deduplicacion, inventario
from pipeline.sintetico.planos_dxf import crear_dxf
from pipeline.sintetico.repositorio import Generador
from pipeline.vision.dibujo import dimensiones, dxf_a_png, lamina_completa, recortar_cajetin
from pipeline.vision.etapa import leer_cajetines
from pipeline.vision.extractor import ErrorDeConfiguracion, LecturaRechazada, LectorCajetines, esquema

LEIDO = {"legible": True, "codigo_plano": "S-0201", "numero": "201", "proyecto": "PRY-009", "disciplina": "EST",
         "tipo_plano": "DE", "titulo": "DETALLES DE ARMADURA", "subtitulo": "SECTOR SUR", "hoja": 1, "total_hojas": 1,
         "revision": "a", "fecha": "2011-05-18", "escala": "1:200", "observaciones": None}
SIN_CAJETIN = {**{clave: None for clave in LEIDO}, "legible": False}


@pytest.fixture
def lector(api_simulada, taxonomia):
    cliente = anthropic.Anthropic(api_key="clave-de-prueba", base_url=api_simulada.url, max_retries=0)
    return LectorCajetines(taxonomia, cliente=cliente)


@pytest.fixture(scope="module")
def lamina(taxonomia):
    plano = next(a for a in Generador(taxonomia, 7).archivos(40, 0, 0) if a.cajetin)
    return dxf_a_png(crear_dxf(plano.cajetin), ancho_px=2000)


def test_dibujo_del_dxf_y_recorte(lamina):
    ancho, alto = dimensiones(lamina)
    assert ancho == 2000 and 1300 < alto < 1500                          # proporción de una lámina A1
    recorte_ancho, recorte_alto = dimensiones(recortar_cajetin(lamina))
    assert recorte_ancho == round(ancho * 0.36) and recorte_alto < alto / 3
    assert max(dimensiones(lamina_completa(lamina))) <= 2576


def test_la_solicitud_lleva_modelo_esquema_e_imagen(lector, api_simulada, lamina, taxonomia):
    api_simulada.app.state.respuestas = [{"datos": LEIDO}]
    lectura = lector.leer_imagen(recortar_cajetin(lamina))

    solicitud = api_simulada.app.state.solicitudes[0]
    cuerpo = solicitud["cuerpo"]
    assert cuerpo["model"] == "claude-opus-5-5"
    assert cuerpo["output_config"]["effort"] == "low"
    assert cuerpo["output_config"]["format"] == {"type": "json_schema", "schema": esquema(taxonomia)}
    assert cuerpo["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in solicitud["cabeceras"]["anthropic-beta"]
    assert "temperature" not in cuerpo and "thinking" not in cuerpo
    imagen, texto = cuerpo["messages"][0]["content"]
    assert imagen["type"] == "image" and imagen["source"]["media_type"] == "image/png"
    assert len(imagen["source"]["data"]) > 1000 and texto["type"] == "text"
    assert "PRY-021" in cuerpo["system"][0]["text"] and cuerpo["system"][0]["cache_control"] == {"type": "ephemeral"}

    # La respuesta se normaliza al formato de la base.
    assert (lectura.numero, lectura.revision, lectura.hoja, lectura.titulo) == ("0201", "A", "01", "Detalles de armadura")
    assert lectura.disciplina == "EST" and lectura.fecha == "2011-05-18" and lectura.suficiente()
    assert lectura.tokens_entrada == 1800 and lectura.tokens_salida == 150 and lectura.modelo == "claude-opus-5-5"


def test_el_esquema_cumple_las_reglas_de_salidas_estructuradas(taxonomia):
    definicion = esquema(taxonomia)
    assert definicion["additionalProperties"] is False
    assert definicion["required"] == list(definicion["properties"])
    assert definicion["properties"]["disciplina"]["anyOf"][0]["enum"] == sorted(taxonomia.disciplinas)


def test_valores_fuera_de_la_taxonomia_se_descartan(lector):
    lectura = lector.normalizar({**LEIDO, "disciplina": "XYZ", "proyecto": "PRY-999", "fecha": "18/05/11", "hoja": 0})
    assert lectura.disciplina is None and lectura.proyecto is None and lectura.fecha is None and lectura.hoja is None


def test_segunda_pasada_con_la_lamina_completa_si_el_recorte_no_trae_cajetin(lector, api_simulada, lamina):
    api_simulada.app.state.respuestas = [{"datos": SIN_CAJETIN}, {"datos": LEIDO}]
    lectura = lector.leer_plano(lamina)
    assert lectura.pasadas == 2 and lectura.numero == "0201"
    assert lectura.tokens_entrada == 3600 and len(lectura.respuestas) == 2
    primera, segunda = [s["cuerpo"]["messages"][0]["content"][0]["source"]["data"] for s in api_simulada.app.state.solicitudes]
    assert len(segunda) > len(primera)                                    # la segunda imagen es la lámina entera


def test_una_sola_pasada_cuando_el_recorte_basta(lector, api_simulada, lamina):
    api_simulada.app.state.respuestas = [{"datos": LEIDO}]
    assert lector.leer_plano(lamina).pasadas == 1
    assert len(api_simulada.app.state.solicitudes) == 1


def test_rechazo_del_modelo_y_respuesta_incompleta(lector, api_simulada, lamina):
    api_simulada.app.state.respuestas = [{"stop_reason": "refusal"}, {"datos": LEIDO, "stop_reason": "max_tokens"}]
    with pytest.raises(LecturaRechazada):
        lector.leer_imagen(lamina)
    with pytest.raises(ValueError, match="incompleta"):
        lector.leer_imagen(lamina)


def test_credenciales_invalidas_detienen_la_etapa(lector, api_simulada, lamina):
    api_simulada.app.state.respuestas = [{"estado": 401, "tipo": "authentication_error", "mensaje": "invalid x-api-key"}]
    with pytest.raises(ErrorDeConfiguracion, match="invalid x-api-key"):
        lector.leer_imagen(lamina)


# ------------------------------------------------------------------ La etapa

@pytest.fixture
def base_con_planos(taxonomia, reglas, tmp_path):
    """Tres planos DXF reales: uno sin nombre útil, uno antiguo sin proyecto y una copia."""
    origen = tmp_path / "origen"
    planos = [a for a in Generador(taxonomia, 7).archivos(120, 0, 0) if a.cajetin and not a.verdad["copia"]]
    antiguo = next(a for a in planos if a.verdad["proyecto"] == "PRY-009" and a.verdad["nombre_informativo"])
    (origen / "Varios").mkdir(parents=True)
    (origen / "Varios" / "Plano7.dxf").write_bytes(crear_dxf(planos[0].cajetin))
    (origen / "Varios" / antiguo.nombre).write_bytes(crear_dxf(antiguo.cajetin))
    (origen / "Respaldo").mkdir()
    (origen / "Respaldo" / "Copia de Plano7.dxf").write_bytes(crear_dxf(planos[0].cajetin))
    conexion = bd.conectar(tmp_path / "s.db")
    almacen = AlmacenLocal(origen)
    inventario.inventariar(conexion, almacen)
    inventario.perfilar(conexion, almacen, reglas)
    clasificacion.clasificar(conexion, taxonomia)
    deduplicacion.deduplicar(conexion)
    return conexion, almacen, antiguo


def test_la_etapa_completa_los_campos_y_no_lee_copias(base_con_planos, lector, api_simulada, taxonomia):
    conexion, almacen, antiguo = base_con_planos
    real = antiguo.verdad
    del_antiguo = {**LEIDO, "numero": real["numero"], "disciplina": real["disciplina"], "revision": real["revision"],
                   "hoja": int(real["hoja"]), "titulo": real["titulo"].upper(), "tipo_plano": real["tipo"]}
    api_simulada.app.state.respuestas = [{"datos": LEIDO}, {"datos": del_antiguo}]

    cuenta = leer_cajetines(conexion, almacen, taxonomia, lector=lector)
    assert cuenta["leidos"] == 2 and cuenta["completos"] == 2 and cuenta["pendientes"] == 0
    assert len(api_simulada.app.state.solicitudes) == 2                    # la copia no se envió

    sin_nombre = conexion.execute("SELECT * FROM archivo WHERE nombre = 'Plano7.dxf'").fetchone()
    assert (sin_nombre["fuente"], sin_nombre["proyecto"], sin_nombre["numero"], sin_nombre["revision"]) == \
        ("ia", "PRY-009", "0201", "A")
    assert sin_nombre["requiere_revision"] == 0 and sin_nombre["revision_orden"] == 1 and sin_nombre["vigente"] == 1

    fila = conexion.execute("SELECT * FROM archivo WHERE nombre = ?", (antiguo.nombre,)).fetchone()
    assert fila["fuente"] == "nombre+ia" and fila["proyecto"] == "PRY-009" and fila["titulo"] == real["titulo"]
    assert conexion.execute("SELECT COUNT(*), SUM(tokens_entrada) FROM lectura_ia WHERE estado = 'ok'").fetchone()[:] == (2, 3600)

    # Una segunda ejecución no vuelve a pagar por lo ya leído.
    assert leer_cajetines(conexion, almacen, taxonomia, lector=lector) == {"leidos": 0, "pendientes": 0}


def test_una_contradiccion_entre_nombre_y_cajetin_queda_para_revision(base_con_planos, lector, api_simulada, taxonomia):
    conexion, almacen, antiguo = base_con_planos
    otro_numero = {**LEIDO, "numero": "0999", "disciplina": antiguo.verdad["disciplina"],
                   "revision": antiguo.verdad["revision"]}
    api_simulada.app.state.respuestas = [{"datos": LEIDO}, {"datos": otro_numero}]
    cuenta = leer_cajetines(conexion, almacen, taxonomia, lector=lector)
    assert cuenta["con_contradiccion"] == 1
    fila = conexion.execute("SELECT * FROM archivo WHERE nombre = ?", (antiguo.nombre,)).fetchone()
    assert fila["numero"] == antiguo.verdad["numero"]                      # se conserva el dato del nombre
    assert fila["requiere_revision"] == 1 and "el cajetín dice 0999" in fila["nota_revision"]


def test_un_error_del_servicio_no_pierde_lo_ya_leido(base_con_planos, lector, api_simulada, taxonomia):
    conexion, almacen, _ = base_con_planos
    api_simulada.app.state.respuestas = [
        {"datos": LEIDO}, {"estado": 529, "tipo": "overloaded_error", "mensaje": "Overloaded"}]
    cuenta = leer_cajetines(conexion, almacen, taxonomia, lector=lector)
    assert cuenta["leidos"] == 1 and cuenta["errores"] == 1 and cuenta["pendientes"] == 1
    estados = dict(conexion.execute("SELECT estado, COUNT(*) FROM lectura_ia GROUP BY estado"))
    assert estados == {"ok": 1, "error": 1}
