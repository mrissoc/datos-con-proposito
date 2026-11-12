"""Sincronización entre la aplicación de averías y SAP.

Tres pasos, cada uno seguro de repetir:

1. Equipos: vincula cada nodo y tramo de la red con su equipo en SAP, usando el
   código del elemento como identificación técnica del equipo.
2. Avisos: crea en SAP un aviso de mantenimiento por cada avería que aún no lo
   tiene. Antes de crear, busca en SAP un aviso con la misma referencia externa:
   así, si una ejecución anterior creó el aviso pero se cortó antes de guardar el
   número, se recupera en vez de duplicarlo.
3. Cierres: informa a SAP el término de las averías que ya se cerraron.

El estado de la integración se guarda en la tabla `sap_aviso`, propia de este
módulo, para no mezclarlo con las tablas de la aplicación.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

import requests

from .cliente import ClienteSap, ErrorSap, a_fecha_odata, literal

log = logging.getLogger(__name__)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS sap_aviso (
    averia_id    INTEGER PRIMARY KEY REFERENCES averia (id),
    aviso        TEXT,
    enviado_en   TEXT,
    cerrado_en   TEXT,
    intentos     INTEGER NOT NULL DEFAULT 0,
    ultimo_error TEXT
);
"""

PRIORIDAD = {"alta": "1", "media": "2", "baja": "3"}
TIPO_AVISO_AVERIA = "M2"
FASE_CONCLUIDO = "4"
LARGO_TEXTO_BREVE = 40

PENDIENTES_DE_ENVIO = """
SELECT v.id, v.codigo, v.severidad, v.fuera_servicio, v.descripcion, v.reportada_en,
       v.tipo_falla_nombre, v.elemento_codigo,
       COALESCE(n.id_externo, a.id_externo) AS equipo
FROM v_averia v
LEFT JOIN nodo n ON v.elemento_tipo = 'nodo' AND n.id = v.elemento_id
LEFT JOIN arco a ON v.elemento_tipo = 'arco' AND a.id = v.elemento_id
WHERE v.id_externo IS NULL
  AND (v.estado <> 'cerrada' OR :incluir_cerradas = 1)
ORDER BY v.reportada_en, v.id
"""

PENDIENTES_DE_CIERRE = """
SELECT v.id, v.codigo, v.cerrada_en, s.aviso
FROM averia v
JOIN sap_aviso s ON s.averia_id = v.id
WHERE v.estado = 'cerrada' AND s.aviso IS NOT NULL AND s.cerrado_en IS NULL
ORDER BY v.cerrada_en, v.id
"""


@dataclass
class Resultado:
    equipos_vinculados: int = 0
    equipos_sin_elemento: List[str] = field(default_factory=list)   # están en SAP y no en la red
    elementos_sin_equipo: List[str] = field(default_factory=list)   # están en la red y no en SAP
    avisos_creados: int = 0
    avisos_recuperados: int = 0
    avisos_cerrados: int = 0
    errores: List[str] = field(default_factory=list)

    def resumen(self) -> str:
        lineas = [
            f"Equipos vinculados: {self.equipos_vinculados}",
            f"Equipos de SAP sin elemento en la red: {len(self.equipos_sin_elemento)}",
            f"Elementos de la red sin equipo en SAP: {len(self.elementos_sin_equipo)}"
            + (f" ({', '.join(self.elementos_sin_equipo)})" if self.elementos_sin_equipo else ""),
            f"Avisos creados: {self.avisos_creados}",
            f"Avisos recuperados (ya existían en SAP): {self.avisos_recuperados}",
            f"Avisos cerrados: {self.avisos_cerrados}",
            f"Errores: {len(self.errores)}",
        ]
        return "\n".join(lineas + [f"  - {error}" for error in self.errores])


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _a_fecha(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def sincronizar_equipos(conexion: sqlite3.Connection, sap: ClienteSap, resultado: Resultado) -> None:
    """Guarda en cada nodo y tramo el número de su equipo en SAP."""
    por_codigo = {}
    for equipo in sap.leer("Equipment"):
        codigo = equipo.get("TechnicalIdentNumber")
        if codigo:
            por_codigo[codigo] = equipo["Equipment"]
        else:
            resultado.equipos_sin_elemento.append(equipo["Equipment"])

    en_red = set()
    with conexion:
        for tabla in ("nodo", "arco"):
            # Los sectores son zonas de consumo, no equipos mantenibles.
            condicion = "WHERE tipo <> 'sector'" if tabla == "nodo" else ""
            for fila in conexion.execute(f"SELECT id, codigo, id_externo FROM {tabla} {condicion}").fetchall():
                en_red.add(fila["codigo"])
                equipo = por_codigo.get(fila["codigo"])
                if equipo is None:
                    resultado.elementos_sin_equipo.append(fila["codigo"])
                    continue
                if fila["id_externo"] != equipo:
                    conexion.execute(f"UPDATE {tabla} SET id_externo = ? WHERE id = ?", (equipo, fila["id"]))
                resultado.equipos_vinculados += 1
    resultado.equipos_sin_elemento.extend(
        equipo for codigo, equipo in sorted(por_codigo.items()) if codigo not in en_red)


def _registrar_error(conexion: sqlite3.Connection, averia_id: int, mensaje: str) -> None:
    with conexion:
        conexion.execute(
            "INSERT INTO sap_aviso (averia_id, intentos, ultimo_error) VALUES (?, 1, ?) "
            "ON CONFLICT (averia_id) DO UPDATE SET intentos = intentos + 1, ultimo_error = excluded.ultimo_error",
            (averia_id, mensaje))


def enviar_averias(conexion: sqlite3.Connection, sap: ClienteSap, resultado: Resultado,
                   incluir_cerradas: bool = False) -> None:
    """Crea en SAP el aviso de cada avería pendiente. Un error no detiene a las demás."""
    pendientes = conexion.execute(PENDIENTES_DE_ENVIO, {"incluir_cerradas": int(incluir_cerradas)}).fetchall()
    for averia in pendientes:
        if not averia["equipo"]:
            mensaje = f"{averia['codigo']}: {averia['elemento_codigo']} no tiene equipo en SAP"
            _registrar_error(conexion, averia["id"], mensaje)
            resultado.errores.append(mensaje)
            continue
        try:
            existentes = list(sap.leer("MaintenanceNotification",
                                       filtro=f"ExternalReference eq {literal(averia['codigo'])}"))
            if existentes:
                aviso = existentes[0]["MaintenanceNotification"]
                resultado.avisos_recuperados += 1
            else:
                aviso = sap.crear("MaintenanceNotification", {
                    "NotificationType": TIPO_AVISO_AVERIA,
                    "NotificationText": f"{averia['tipo_falla_nombre']} {averia['elemento_codigo']}"[:LARGO_TEXTO_BREVE],
                    "LongText": averia["descripcion"],
                    "Equipment": averia["equipo"],
                    "MaintPriority": PRIORIDAD[averia["severidad"]],
                    "MalfunctionStartDate": a_fecha_odata(_a_fecha(averia["reportada_en"])),
                    "MaintenanceObjectIsDown": bool(averia["fuera_servicio"]),
                    "ExternalReference": averia["codigo"],
                })["MaintenanceNotification"]
                resultado.avisos_creados += 1
        except (ErrorSap, requests.RequestException) as error:
            mensaje = f"{averia['codigo']}: {error}"
            log.warning("No se pudo enviar %s", mensaje)
            _registrar_error(conexion, averia["id"], str(error))
            resultado.errores.append(mensaje)
            continue
        with conexion:
            conexion.execute("UPDATE averia SET id_externo = ? WHERE id = ?", (aviso, averia["id"]))
            conexion.execute(
                "INSERT INTO sap_aviso (averia_id, aviso, enviado_en, intentos) VALUES (?, ?, ?, 1) "
                "ON CONFLICT (averia_id) DO UPDATE SET aviso = excluded.aviso, enviado_en = excluded.enviado_en, "
                "intentos = intentos + 1, ultimo_error = NULL",
                (averia["id"], aviso, _ahora()))


def cerrar_avisos(conexion: sqlite3.Connection, sap: ClienteSap, resultado: Resultado) -> None:
    """Informa a SAP el término de las averías cerradas en la aplicación."""
    for averia in conexion.execute(PENDIENTES_DE_CIERRE).fetchall():
        try:
            sap.actualizar("MaintenanceNotification", averia["aviso"], {
                "MalfunctionEndDate": a_fecha_odata(_a_fecha(averia["cerrada_en"])),
                "NotificationPhase": FASE_CONCLUIDO,
            })
        except (ErrorSap, requests.RequestException) as error:
            resultado.errores.append(f"{averia['codigo']}: {error}")
            continue
        with conexion:
            conexion.execute("UPDATE sap_aviso SET cerrado_en = ? WHERE averia_id = ?", (_ahora(), averia["id"]))
        resultado.avisos_cerrados += 1


def sincronizar(conexion: sqlite3.Connection, sap: ClienteSap, incluir_cerradas: bool = False) -> Resultado:
    """Ejecuta los tres pasos y devuelve el resumen de lo ocurrido."""
    conexion.row_factory = sqlite3.Row
    conexion.executescript(ESQUEMA)
    resultado = Resultado()
    sincronizar_equipos(conexion, sap, resultado)
    enviar_averias(conexion, sap, resultado, incluir_cerradas)
    cerrar_avisos(conexion, sap, resultado)
    return resultado
