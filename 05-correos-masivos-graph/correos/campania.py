"""Campaña de recordatorios: a quién se escribe, con copia a quién, y qué recibe cada persona.

Los destinatarios no vienen de una lista fija: salen de una consulta sobre la base,
de modo que cada ejecución escribe solo a quienes hoy tienen cursos pendientes.
La copia a las jefaturas se arma subiendo por el organigrama.
"""

from __future__ import annotations

import base64
import html
import io
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

# Pendientes de cada persona. La cadena de jefaturas se obtiene con una consulta
# recursiva sobre el organigrama: nivel 1 es la jefatura directa, nivel 2 la siguiente.
PENDIENTES = """
SELECT e.id, e.nombre, e.correo, e.gerencia, e.pais, c.nombre AS curso, c.horas, a.fecha_limite
FROM asignacion a
JOIN empleado e ON e.id = a.empleado_id
JOIN curso c    ON c.id = a.curso_id
WHERE a.completado_en IS NULL AND e.pais = :pais
ORDER BY e.nombre, e.id, a.fecha_limite
"""

JEFATURAS = """
WITH RECURSIVE cadena (empleado_id, jefe_id, nivel) AS (
    SELECT id, jefe_id, 1 FROM empleado WHERE jefe_id IS NOT NULL
    UNION ALL
    SELECT c.empleado_id, j.jefe_id, c.nivel + 1
    FROM cadena c
    JOIN empleado j ON j.id = c.jefe_id
    WHERE j.jefe_id IS NOT NULL AND c.nivel < :niveles
)
SELECT c.empleado_id, c.nivel, j.nombre, j.correo
FROM cadena c
JOIN empleado j ON j.id = c.jefe_id
ORDER BY c.empleado_id, c.nivel
"""

TEXTOS = {
    "Chile": {"saludo": "Hola", "firma": "Equipo de Cumplimiento", "formato_fecha": "%d-%m-%Y"},
    "México": {"saludo": "Hola", "firma": "Equipo de Cumplimiento México", "formato_fecha": "%d/%m/%Y"},
    "Argentina": {"saludo": "Hola", "firma": "Equipo de Compliance Argentina", "formato_fecha": "%d/%m/%Y"},
}


@dataclass
class Curso:
    nombre: str
    horas: float
    fecha_limite: date

    def dias_de_atraso(self, hoy: date) -> int:
        return max(0, (hoy - self.fecha_limite).days)


@dataclass
class Destinatario:
    id: int
    nombre: str
    correo: str
    gerencia: str
    pais: str
    cursos: List[Curso] = field(default_factory=list)
    copia: List[str] = field(default_factory=list)          # correos de las jefaturas


@dataclass
class Mensaje:
    para: str
    copia: List[str]
    asunto: str
    html: str
    adjunto_nombre: str
    adjunto: bytes


def destinatarios(conexion: sqlite3.Connection, pais: str, niveles_copia: int = 1) -> List[Destinatario]:
    """Personas del país con cursos pendientes, cada una con sus jefaturas en copia."""
    personas: Dict[int, Destinatario] = {}
    for fila in conexion.execute(PENDIENTES, {"pais": pais}):
        persona = personas.setdefault(fila["id"], Destinatario(
            fila["id"], fila["nombre"], fila["correo"], fila["gerencia"], fila["pais"]))
        persona.cursos.append(Curso(fila["curso"], fila["horas"], date.fromisoformat(fila["fecha_limite"])))
    if niveles_copia > 0:
        for fila in conexion.execute(JEFATURAS, {"niveles": niveles_copia}):
            persona = personas.get(fila["empleado_id"])
            if persona and fila["correo"] != persona.correo and fila["correo"] not in persona.copia:
                persona.copia.append(fila["correo"])
    return list(personas.values())


def adjunto_excel(persona: Destinatario, hoy: date) -> bytes:
    """Planilla con los cursos pendientes de la persona."""
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Cursos pendientes"
    hoja.append(["Curso", "Horas", "Fecha límite", "Días de atraso", "Estado"])
    for celda in hoja[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F4E79")
    for curso in persona.cursos:
        atraso = curso.dias_de_atraso(hoy)
        hoja.append([curso.nombre, curso.horas, curso.fecha_limite, atraso, "Vencido" if atraso else "En plazo"])
    for fila in hoja.iter_rows(min_row=2, min_col=3, max_col=3):
        fila[0].number_format = "DD-MM-YYYY"
    for indice, ancho in enumerate([42, 8, 14, 15, 12], start=1):
        hoja.column_dimensions[get_column_letter(indice)].width = ancho
    hoja.freeze_panes = "A2"
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


def cuerpo_html(persona: Destinatario, hoy: date) -> str:
    """Correo en HTML con estilos en línea, que es lo que respetan los clientes de correo.

    Todo dato que viene de la base pasa por `html.escape`.
    """
    textos = TEXTOS.get(persona.pais, TEXTOS["Chile"])
    vencidos = sum(curso.dias_de_atraso(hoy) > 0 for curso in persona.cursos)
    filas = []
    for curso in persona.cursos:
        atraso = curso.dias_de_atraso(hoy)
        estado = (f'<span style="color:#b42318;font-weight:600">Vencido hace {atraso} día{"s" if atraso != 1 else ""}</span>'
                  if atraso else '<span style="color:#475467">En plazo</span>')
        filas.append(
            '<tr>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #e4e7ec">{html.escape(curso.nombre)}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #e4e7ec;white-space:nowrap">'
            f'{curso.fecha_limite.strftime(textos["formato_fecha"])}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #e4e7ec;white-space:nowrap">{estado}</td>'
            '</tr>')
    cantidad = len(persona.cursos)
    resumen = (f"Tienes {cantidad} curso{'s' if cantidad != 1 else ''} obligatorio{'s' if cantidad != 1 else ''} "
               f"pendiente{'s' if cantidad != 1 else ''}"
               + (f", {vencido_texto(vencidos)}." if vencidos else "."))
    return f"""<!doctype html>
<html lang="es"><body style="margin:0;background:#f2f4f7;font-family:Segoe UI,Arial,sans-serif;color:#101828">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border:1px solid #e4e7ec;border-radius:8px">
<tr><td style="padding:20px 24px;border-bottom:3px solid #1f4e79;font-size:18px;font-weight:600">Cursos obligatorios pendientes</td></tr>
<tr><td style="padding:20px 24px;font-size:14px;line-height:1.5">
<p style="margin:0 0 12px">{textos["saludo"]}, {html.escape(persona.nombre.split()[0])}:</p>
<p style="margin:0 0 16px">{resumen} El detalle va también en la planilla adjunta.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr style="background:#f2f4f7;text-align:left">
<th style="padding:8px 12px">Curso</th><th style="padding:8px 12px">Fecha límite</th><th style="padding:8px 12px">Estado</th></tr>
{"".join(filas)}
</table>
<p style="margin:16px 0 0">Puedes completarlos en la plataforma de capacitación. Si ya los rendiste, el avance se actualizará en el próximo corte.</p>
<p style="margin:16px 0 0;color:#475467">{html.escape(textos["firma"])}</p>
</td></tr>
<tr><td style="padding:12px 24px;background:#f9fafb;border-top:1px solid #e4e7ec;font-size:12px;color:#667085;border-radius:0 0 8px 8px">
Mensaje automático. Tu jefatura recibe copia de este recordatorio.</td></tr>
</table></td></tr></table></body></html>"""


def vencido_texto(vencidos: int) -> str:
    return f"{vencidos} de ellos vencido" if vencidos == 1 else f"{vencidos} de ellos vencidos"


def armar(persona: Destinatario, hoy: date) -> Mensaje:
    vencidos = sum(curso.dias_de_atraso(hoy) > 0 for curso in persona.cursos)
    asunto = (f"Cursos obligatorios: {len(persona.cursos)} pendiente{'s' if len(persona.cursos) != 1 else ''}"
              + (f", {vencidos} vencido{'s' if vencidos != 1 else ''}" if vencidos else ""))
    return Mensaje(persona.correo, persona.copia, asunto, cuerpo_html(persona, hoy),
                   "Cursos pendientes.xlsx", adjunto_excel(persona, hoy))


def enviar(conexion: sqlite3.Connection, campania: str, pais: str, hoy: date,
           despachar: Callable[[Mensaje], None], niveles_copia: int = 1, limite: Optional[int] = None) -> dict:
    """Envía la campaña. `despachar` hace el envío real (Graph) o lo simula (archivos).

    Cada envío queda registrado: si la ejecución se interrumpe o se repite, nadie
    recibe dos veces el mismo recordatorio y los que fallaron se reintentan.
    """
    ya_enviados = {fila[0] for fila in conexion.execute(
        "SELECT empleado_id FROM envio WHERE campania = ? AND estado = 'enviado'", (campania,))}
    cuenta = {"enviados": 0, "errores": 0, "omitidos": 0}
    for persona in destinatarios(conexion, pais, niveles_copia):
        if persona.id in ya_enviados:
            cuenta["omitidos"] += 1
            continue
        if limite is not None and cuenta["enviados"] >= limite:
            break
        try:
            despachar(armar(persona, hoy))
            estado, detalle = "enviado", None
        except Exception as error:                       # un destinatario con problemas no detiene la campaña
            estado, detalle = "error", f"{type(error).__name__}: {error}"
        with conexion:
            conexion.execute(
                "INSERT OR REPLACE INTO envio (campania, empleado_id, estado, detalle, registrado) VALUES (?, ?, ?, ?, ?)",
                (campania, persona.id, estado, detalle, datetime.now().isoformat(timespec="seconds")))
        cuenta["enviados" if estado == "enviado" else "errores"] += 1
    return cuenta


def simulador(carpeta: Path) -> Callable[[Mensaje], None]:
    """Despachador de prueba: en vez de enviar, guarda cada correo y su adjunto en una carpeta."""
    carpeta.mkdir(parents=True, exist_ok=True)

    def despachar(mensaje: Mensaje) -> None:
        base = mensaje.para.split("@")[0]
        encabezado = (f"<!-- Para: {html.escape(mensaje.para)} | Copia: {html.escape(', '.join(mensaje.copia))} | "
                      f"Asunto: {html.escape(mensaje.asunto)} -->\n")
        (carpeta / f"{base}.html").write_text(encabezado + mensaje.html, encoding="utf-8")
        (carpeta / f"{base}.xlsx").write_bytes(mensaje.adjunto)

    return despachar


def a_graph(mensaje: Mensaje) -> dict:
    """Cuerpo de la solicitud `sendMail` de Microsoft Graph."""
    direccion = lambda correo: {"emailAddress": {"address": correo}}       # noqa: E731
    return {
        "message": {
            "subject": mensaje.asunto,
            "body": {"contentType": "HTML", "content": mensaje.html},
            "toRecipients": [direccion(mensaje.para)],
            "ccRecipients": [direccion(correo) for correo in mensaje.copia],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": mensaje.adjunto_nombre,
                "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "contentBytes": base64.b64encode(mensaje.adjunto).decode("ascii"),
            }],
        },
        "saveToSentItems": True,
    }
