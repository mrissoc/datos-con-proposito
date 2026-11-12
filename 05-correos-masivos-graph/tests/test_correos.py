"""Campaña de recordatorios: selección, copias, contenido, envío por Graph y KPIs."""

from __future__ import annotations

import base64
import io
import json
from datetime import date, timedelta

import pytest
import responses
from openpyxl import load_workbook

from correos import campania, datos, kpi
from correos.graph import GRAPH, ClienteCorreo, ErrorDeEnvio

HOY = date(2026, 11, 10)


@pytest.fixture
def conexion(tmp_path):
    conexion = datos.crear_ejemplo(tmp_path / "capacitacion.db", HOY)
    yield conexion
    conexion.close()


def test_los_destinatarios_salen_de_la_base_y_son_solo_quienes_tienen_pendientes(conexion):
    personas = campania.destinatarios(conexion, "Chile")
    esperados = {fila[0] for fila in conexion.execute(
        "SELECT DISTINCT e.id FROM empleado e JOIN asignacion a ON a.empleado_id = e.id "
        "WHERE e.pais = 'Chile' AND a.completado_en IS NULL")}
    assert {p.id for p in personas} == esperados and len(personas) > 20
    assert all(p.pais == "Chile" and p.cursos for p in personas)
    una = personas[0]
    assert [c.fecha_limite for c in una.cursos] == sorted(c.fecha_limite for c in una.cursos)

    # Al completar sus cursos, la persona deja de recibir el recordatorio.
    with conexion:
        conexion.execute("UPDATE asignacion SET completado_en = ? WHERE empleado_id = ?", (HOY.isoformat(), una.id))
    assert una.id not in {p.id for p in campania.destinatarios(conexion, "Chile")}


def test_la_copia_sube_por_el_organigrama(conexion):
    jefe_de = dict(conexion.execute("SELECT id, jefe_id FROM empleado"))
    correo_de = dict(conexion.execute("SELECT id, correo FROM empleado"))
    un_nivel = {p.id: p for p in campania.destinatarios(conexion, "Chile", niveles_copia=1)}
    dos_niveles = {p.id: p for p in campania.destinatarios(conexion, "Chile", niveles_copia=2)}
    analista = next(p for p in un_nivel.values() if jefe_de[p.id] and jefe_de[jefe_de[p.id]])
    directo, superior = jefe_de[analista.id], jefe_de[jefe_de[analista.id]]
    assert analista.copia == [correo_de[directo]]
    assert dos_niveles[analista.id].copia == [correo_de[directo], correo_de[superior]]
    assert all(not p.copia for p in campania.destinatarios(conexion, "Chile", niveles_copia=0))
    gerente_general = next((p for p in un_nivel.values() if jefe_de[p.id] is None), None)
    assert gerente_general is None or gerente_general.copia == []


def test_el_correo_y_la_planilla_muestran_los_cursos_de_la_persona():
    persona = campania.Destinatario(1, "Ana <b>Pérez</b>", "ana@ejemplo.com", "Finanzas", "Chile", cursos=[
        campania.Curso("Ética & conducta <script>", 2, HOY - timedelta(days=3)),
        campania.Curso("Protección de datos", 1, HOY + timedelta(days=10))], copia=["jefa@ejemplo.com"])
    mensaje = campania.armar(persona, HOY)
    assert mensaje.asunto == "Cursos obligatorios: 2 pendientes, 1 vencido"
    assert "Hola, Ana:" in mensaje.html and "Vencido hace 3 días" in mensaje.html and "07-11-2026" in mensaje.html
    assert "<script>" not in mensaje.html and "Ética &amp; conducta &lt;script&gt;" in mensaje.html   # datos escapados

    hoja = load_workbook(io.BytesIO(mensaje.adjunto)).active
    filas = list(hoja.iter_rows(values_only=True))
    assert filas[0] == ("Curso", "Horas", "Fecha límite", "Días de atraso", "Estado")
    assert filas[1][0] == "Ética & conducta <script>" and filas[1][3:] == (3, "Vencido")
    assert filas[2][3:] == (0, "En plazo")


def test_la_campania_no_repite_envios_y_reintenta_los_fallidos(conexion):
    enviados = []

    def despachar(mensaje):
        if len(enviados) == 2 and not despachar.recuperado:
            despachar.recuperado = True
            raise ConnectionError("sin red")
        enviados.append(mensaje.para)
    despachar.recuperado = False

    total = len(campania.destinatarios(conexion, "Argentina"))
    primera = campania.enviar(conexion, "nov", "Argentina", HOY, despachar)
    assert primera == {"enviados": total - 1, "errores": 1, "omitidos": 0}
    error = conexion.execute("SELECT detalle FROM envio WHERE estado = 'error'").fetchone()[0]
    assert error == "ConnectionError: sin red"

    segunda = campania.enviar(conexion, "nov", "Argentina", HOY, despachar)
    assert segunda == {"enviados": 1, "errores": 0, "omitidos": total - 1}
    assert len(enviados) == len(set(enviados)) == total                    # nadie recibió dos correos
    assert campania.enviar(conexion, "dic", "Argentina", HOY, despachar)["enviados"] == total   # otra campaña parte de cero


def test_la_simulacion_deja_cada_correo_en_una_carpeta(conexion, tmp_path):
    resultado = campania.enviar(conexion, "nov", "México", HOY, campania.simulador(tmp_path / "salida"), limite=3)
    assert resultado["enviados"] == 3
    paginas = sorted((tmp_path / "salida").glob("*.html"))
    assert len(paginas) == 3 and len(list((tmp_path / "salida").glob("*.xlsx"))) == 3
    assert "Equipo de Cumplimiento México" in paginas[0].read_text(encoding="utf-8")


@pytest.fixture
def mensaje():
    persona = campania.Destinatario(1, "Ana Pérez", "ana@ejemplo.com", "Finanzas", "Chile",
                                    cursos=[campania.Curso("Ética", 2, HOY)], copia=["jefa@ejemplo.com"])
    return campania.armar(persona, HOY)


@responses.activate
def test_envio_por_graph_con_adjunto_y_copia(mensaje):
    url = f"{GRAPH}/users/capacitacion@ejemplo.com/sendMail"
    responses.post(url, status=429, headers={"Retry-After": "12"})
    responses.post(url, status=202)
    esperas = []
    ClienteCorreo(lambda: "token", "capacitacion@ejemplo.com", espera=esperas.append).enviar(mensaje)
    assert esperas == [12.0]                                               # respetó el tiempo pedido por el servicio

    solicitud = responses.calls[1].request
    cuerpo = json.loads(solicitud.body)
    assert solicitud.headers["Authorization"] == "Bearer token" and cuerpo["saveToSentItems"] is True
    correo = cuerpo["message"]
    assert correo["toRecipients"] == [{"emailAddress": {"address": "ana@ejemplo.com"}}]
    assert correo["ccRecipients"] == [{"emailAddress": {"address": "jefa@ejemplo.com"}}]
    assert correo["body"]["contentType"] == "HTML" and "Hola, Ana:" in correo["body"]["content"]
    adjunto = correo["attachments"][0]
    assert adjunto["@odata.type"] == "#microsoft.graph.fileAttachment" and adjunto["name"] == "Cursos pendientes.xlsx"
    assert base64.b64decode(adjunto["contentBytes"]) == mensaje.adjunto


@responses.activate
def test_un_rechazo_definitivo_informa_el_motivo(mensaje):
    responses.post(f"{GRAPH}/users/capacitacion@ejemplo.com/sendMail", status=403,
                   json={"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}})
    with pytest.raises(ErrorDeEnvio, match="403: Access is denied."):
        ClienteCorreo(lambda: "token", "capacitacion@ejemplo.com").enviar(mensaje)
    assert len(responses.calls) == 1                                       # no se reintenta lo que no va a cambiar


def test_la_foto_diaria_construye_la_evolucion_y_no_se_duplica(conexion):
    for dia in range(5):
        fecha = HOY + timedelta(days=dia)
        datos.avanzar_dia(conexion, fecha, semilla=dia)
        kpi.tomar_foto(conexion, fecha)
    kpi.tomar_foto(conexion, HOY + timedelta(days=4))                      # repetir el día no agrega filas
    assert conexion.execute("SELECT COUNT(DISTINCT fecha), COUNT(*) FROM kpi_diario").fetchone()[:] == (5, 5 * 3 * 6)

    chile = [fila for fila in kpi.evolucion(conexion) if fila["pais"] == "Chile"]
    avances = [fila["avance_pct"] for fila in chile]
    assert avances == sorted(avances) and avances[-1] > avances[0]         # el avance solo puede crecer
    assert chile[0]["variacion_pp"] is None and chile[-1]["variacion_pp"] >= 0
    real = conexion.execute(
        "SELECT ROUND(100.0 * SUM(a.completado_en IS NOT NULL) / COUNT(*), 1) FROM asignacion a "
        "JOIN empleado e ON e.id = a.empleado_id WHERE e.pais = 'Chile'").fetchone()[0]
    assert avances[-1] == real


def test_exporta_el_historial_para_power_bi(conexion, tmp_path):
    kpi.tomar_foto(conexion, HOY)
    kpi.exportar(conexion, tmp_path)
    encabezado = (tmp_path / "kpi_evolucion.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert encabezado == "fecha,pais,asignados,completados,vencidos,avance_pct,variacion_pp"
    assert len((tmp_path / "kpi_diario.csv").read_text(encoding="utf-8-sig").splitlines()) == 1 + 3 * 6
