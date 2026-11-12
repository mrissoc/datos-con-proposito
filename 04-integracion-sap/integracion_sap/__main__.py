"""Línea de comandos.

    python -m integracion_sap simulador [--puerto 8090]
    python -m integracion_sap sincronizar --bd ../03-averias-grafos-sql/datos/averias.db

Las credenciales se leen de las variables de entorno SAP_USUARIO y SAP_CLAVE.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
from pathlib import Path

from .cliente import ClienteSap
from .simulador import RUTA_SERVICIO, crear_simulador
from .sincronizar import sincronizar

BD_POR_DEFECTO = Path(__file__).resolve().parent.parent.parent / "03-averias-grafos-sql" / "datos" / "averias.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Integración de averías con SAP por OData")
    comandos = parser.add_subparsers(dest="comando", required=True)

    simulador = comandos.add_parser("simulador", help="levanta el simulador de SAP Gateway")
    simulador.add_argument("--puerto", type=int, default=8090)

    sincro = comandos.add_parser("sincronizar", help="vincula equipos y envía avisos de avería")
    sincro.add_argument("--bd", type=Path, default=BD_POR_DEFECTO, help="base de la aplicación de averías")
    sincro.add_argument("--url", default=f"http://127.0.0.1:8090{RUTA_SERVICIO}", help="URL del servicio OData")
    sincro.add_argument("--incluir-cerradas", action="store_true",
                        help="envía también las averías históricas ya cerradas")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    if args.comando == "simulador":
        import uvicorn
        print(f"Simulador SAP en http://127.0.0.1:{args.puerto}{RUTA_SERVICIO}/  (usuario demo, clave demo)")
        uvicorn.run(crear_simulador(), host="127.0.0.1", port=args.puerto, log_level="warning")
        return

    if not args.bd.exists():
        parser.error(f"No existe la base {args.bd}. Ejecuta antes la aplicación de averías (python -m averias).")
    sap = ClienteSap(args.url, os.environ.get("SAP_USUARIO", "demo"), os.environ.get("SAP_CLAVE", "demo"))
    conexion = sqlite3.connect(str(args.bd))
    try:
        print(sincronizar(conexion, sap, args.incluir_cerradas).resumen())
    finally:
        conexion.close()


if __name__ == "__main__":
    main()
