"""Línea de comandos del pipeline.

    python -m pipeline demo                  genera un repositorio sintético en _demo/
    python -m pipeline ejecutar              inventario, perfilado, clasificación, copias, migración y catálogo
    python -m pipeline vision --max 20       lee cajetines con IA (requiere credenciales de Anthropic)
    python -m pipeline reporte               reporte del estado del repositorio
    python -m pipeline evaluar               exactitud contra la verdad del repositorio sintético
    python -m pipeline escala --archivos 315000   mide las etapas a escala, sin crear archivos

Cada etapa también se puede ejecutar por separado: inventario, perfilar, clasificar,
deduplicar, planificar, migrar, catalogo.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import bd
from .almacenes.local import AlmacenLocal
from .config import RAIZ, Reglas, Taxonomia
from .etapas import catalogo, clasificacion, deduplicacion, inventario, migracion

DEMO = RAIZ / "_demo"
ETAPAS = ["inventario", "perfilar", "clasificar", "deduplicar", "planificar", "migrar", "catalogo"]


def _almacen(args):
    if str(args.origen).startswith("sharepoint://"):
        from .almacenes.sharepoint import AlmacenSharePoint
        return AlmacenSharePoint.desde_entorno(str(args.origen)[len("sharepoint://"):])
    return AlmacenLocal(args.origen, args.destino)


def _ejecutar_etapa(nombre: str, conexion, almacen, taxonomia: Taxonomia, reglas: Reglas, args) -> dict:
    with bd.etapa(conexion, nombre) as resultado:
        if nombre == "inventario":
            resultado["archivos"] = inventario.inventariar(conexion, almacen)
        elif nombre == "perfilar":
            resultado.update(inventario.perfilar(conexion, almacen, reglas))
        elif nombre == "clasificar":
            resultado.update(clasificacion.clasificar(conexion, taxonomia))
        elif nombre == "deduplicar":
            resultado.update(deduplicacion.deduplicar(conexion))
        elif nombre == "planificar":
            resultado.update(migracion.planificar(conexion, taxonomia, reglas))
        elif nombre == "migrar":
            resultado.update(migracion.migrar(conexion, almacen, hilos=args.hilos))
        elif nombre == "catalogo":
            resultado.update(catalogo.exportar(conexion, taxonomia, args.catalogo, _vistas(almacen, args)))
    return resultado


def _vistas(almacen, args):
    """Genera la imagen de vista previa de hasta `--vistas` planos DXF vigentes."""
    if not args.vistas:
        return None
    carpeta = args.catalogo.parent / "vistas"
    carpeta.mkdir(parents=True, exist_ok=True)
    generadas = {"n": 0}

    def vista(fila):
        if generadas["n"] >= args.vistas or fila["extension_real"] != "dxf":
            return None
        from .vision.dibujo import dxf_a_png, lamina_completa
        (carpeta / f"{fila['id']}.png").write_bytes(lamina_completa(dxf_a_png(almacen.leer(fila["origen_id"]), 1800)))
        generadas["n"] += 1
        return f"vistas/{fila['id']}.png"

    return vista


def comando_demo(args) -> None:
    from .sintetico.repositorio import Generador, materializar
    taxonomia = Taxonomia.cargar()
    generador = Generador(taxonomia, args.semilla)
    verdad = materializar(generador.archivos(args.planos, args.documentos, args.raros), args.carpeta / "repositorio")
    (args.carpeta / "verdad.json").write_text(json.dumps(verdad, ensure_ascii=False), encoding="utf-8")
    print(f"{len(verdad)} archivos en {args.carpeta / 'repositorio'}")


def comando_escala(args) -> None:
    """Corre las etapas sobre metadatos sintéticos, sin escribir archivos."""
    from .almacenes.sintetico import AlmacenSintetico
    from .sintetico.repositorio import Generador
    taxonomia, reglas = Taxonomia.cargar(), Reglas.cargar()
    planos, documentos = int(args.archivos / 6.45), int(args.archivos / 18)
    generador = Generador(taxonomia, args.semilla)
    almacen = AlmacenSintetico(generador.archivos(planos, documentos, raros=330, repeticiones=8), limite_cada=50)
    conexion = bd.conectar(args.bd)
    args.hilos, args.catalogo, args.vistas = 8, args.bd.with_name("catalogo_escala.json"), 0
    for nombre in ETAPAS:
        if nombre == "migrar":
            # Se migra una muestra para ejercitar los reintentos ante límites del servicio.
            with bd.etapa(conexion, "migrar") as resultado:
                resultado.update(migracion.migrar(conexion, almacen, hilos=8, espera=lambda s: None, limite=20000))
        else:
            resultado = _ejecutar_etapa(nombre, conexion, almacen, taxonomia, reglas, args)
        print(f"{nombre:<11} {resultado.pop('segundos'):>7.1f} s  {resultado}")
    print()
    print(catalogo.reporte(conexion))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline", description="Pipeline documental")
    comandos = parser.add_subparsers(dest="comando", required=True)

    demo = comandos.add_parser("demo", help="genera un repositorio sintético")
    demo.add_argument("--carpeta", type=Path, default=DEMO)
    demo.add_argument("--planos", type=int, default=320)
    demo.add_argument("--documentos", type=int, default=140)
    demo.add_argument("--raros", type=int, default=60)
    demo.add_argument("--semilla", type=int, default=7)

    escala = comandos.add_parser("escala", help="mide el pipeline a escala con metadatos sintéticos")
    escala.add_argument("--archivos", type=int, default=315000)
    escala.add_argument("--bd", type=Path, default=DEMO / "escala.db")
    escala.add_argument("--semilla", type=int, default=7)

    for nombre in ["ejecutar", "vision", "reporte", "evaluar"] + ETAPAS:
        comando = comandos.add_parser(nombre)
        comando.add_argument("--origen", default=DEMO / "repositorio",
                             help="carpeta de origen, o sharepoint://<id de la biblioteca>")
        comando.add_argument("--destino", type=Path, default=DEMO / "biblioteca")
        comando.add_argument("--bd", type=Path, default=DEMO / "staging.db")
        comando.add_argument("--catalogo", type=Path, default=RAIZ.parent / "02-biblioteca-web" / "datos" / "catalogo.json")
        comando.add_argument("--vistas", type=int, default=0, help="cantidad de vistas previas de planos a generar")
        comando.add_argument("--hilos", type=int, default=4)
        if nombre == "vision":
            comando.add_argument("--max", type=int, default=20, help="máximo de planos a leer en esta ejecución")
            comando.add_argument("--modelo", default=None)
            comando.add_argument("--esfuerzo", default="low", choices=["low", "medium", "high"])
        if nombre == "evaluar":
            comando.add_argument("--verdad", type=Path, default=DEMO / "verdad.json")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("ezdxf").setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.comando == "demo":
        return comando_demo(args)
    if args.comando == "escala":
        if args.bd.exists():
            args.bd.unlink()
        return comando_escala(args)

    conexion = bd.conectar(args.bd)
    taxonomia, reglas = Taxonomia.cargar(), Reglas.cargar()
    if args.comando == "reporte":
        print(catalogo.reporte(conexion))
    elif args.comando == "evaluar":
        from .evaluacion import evaluar
        print(evaluar(conexion, json.loads(args.verdad.read_text(encoding="utf-8"))))
    elif args.comando == "vision":
        from .vision.etapa import leer_cajetines
        with bd.etapa(conexion, "vision") as resultado:
            resultado.update(leer_cajetines(conexion, _almacen(args), taxonomia, maximo=args.max,
                                            modelo=args.modelo, esfuerzo=args.esfuerzo))
        print(f"vision  {resultado}")
    else:
        almacen = _almacen(args)
        for nombre in (ETAPAS if args.comando == "ejecutar" else [args.comando]):
            resultado = _ejecutar_etapa(nombre, conexion, almacen, taxonomia, reglas, args)
            print(f"{nombre:<11} {resultado.pop('segundos'):>6.1f} s  {resultado}")


if __name__ == "__main__":
    main()
