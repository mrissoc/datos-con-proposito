"""Línea de comandos.

    python -m correos datos                      crea la base de ejemplo y 30 días de historial de KPIs
    python -m correos enviar --pais Chile        simula la campaña: deja cada correo en salida/
    python -m correos enviar --pais Chile --real envía por Microsoft Graph (requiere credenciales)
    python -m correos foto                       guarda la foto de KPIs de hoy y exporta el historial
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from . import campania, datos, kpi

RAIZ = Path(__file__).resolve().parent.parent
BD = RAIZ / "datos" / "capacitacion.db"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m correos")
    comandos = parser.add_subparsers(dest="comando", required=True)
    comandos.add_parser("datos")
    comandos.add_parser("foto")
    enviar = comandos.add_parser("enviar")
    enviar.add_argument("--pais", default="Chile", choices=sorted(datos.DOTACION))
    enviar.add_argument("--campania", default=f"recordatorio-{date.today():%Y-%m}")
    enviar.add_argument("--niveles-copia", type=int, default=1, help="niveles de jefatura que reciben copia")
    enviar.add_argument("--limite", type=int, default=None, help="máximo de correos en esta ejecución")
    enviar.add_argument("--real", action="store_true", help="envía por Microsoft Graph en vez de simular")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    hoy = date.today()

    if args.comando == "datos":
        # Se parte 30 días atrás y se avanza día a día, para que el historial tenga forma.
        conexion = datos.crear_ejemplo(BD, hoy - timedelta(days=30))
        for dia in range(30, -1, -1):
            fecha = hoy - timedelta(days=dia)
            datos.avanzar_dia(conexion, fecha, semilla=dia)
            kpi.tomar_foto(conexion, fecha)
        kpi.exportar(conexion, RAIZ / "powerbi")
        total = conexion.execute("SELECT COUNT(*) FROM empleado").fetchone()[0]
        print(f"{BD}: {total} personas, 31 días de historial de KPIs")
        return

    conexion = datos.conectar(BD)
    if args.comando == "foto":
        filas = kpi.tomar_foto(conexion, hoy)
        kpi.exportar(conexion, RAIZ / "powerbi")
        print(f"Foto del {hoy}: {filas} filas. Historial exportado a powerbi/")
    else:
        if args.real:
            from .graph import ClienteCorreo
            despachar = ClienteCorreo.desde_entorno().enviar
        else:
            despachar = campania.simulador(RAIZ / "salida" / args.pais)
        resultado = campania.enviar(conexion, args.campania, args.pais, hoy, despachar,
                                    niveles_copia=args.niveles_copia, limite=args.limite)
        print(f"Campaña {args.campania} ({args.pais}): {resultado}")


if __name__ == "__main__":
    main()
