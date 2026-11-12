"""Construye la base de demostración: red de ejemplo e historial sintético de averías.

Uso:
    python -m averias.construir_bd                     # datos/averias_demo.db
    python -m averias.construir_bd --salida otra.db --semilla 3
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .bd import RAIZ, RUTA_ESQUEMA, conectar

RUTA_RED = RAIZ.parent / "datos-ejemplo" / "red.json"
RUTA_DEMO = RAIZ / "datos" / "averias_demo.db"

TIPOS_FALLA = [
    ("rotura", "Rotura de tubería", "arco"),
    ("fuga", "Fuga", "arco"),
    ("obstruccion", "Obstrucción", "arco"),
    ("valvula", "Falla de válvula", "arco"),
    ("bomba", "Falla de bomba", "nodo"),
    ("electrica", "Falla eléctrica", "nodo"),
    ("instrumentacion", "Falla de instrumentación", "nodo"),
    ("control", "Falla de tablero de control", "nodo"),
    ("calidad", "Parámetro de calidad fuera de rango", "nodo"),
]

DESCRIPCIONES = {
    "rotura": ["Rotura detectada por caída brusca de presión.", "Rotura por trabajos de terceros en la vía."],
    "fuga": ["Fuga detectada en inspección de rutina.", "Afloramiento de agua reportado por vecinos."],
    "obstruccion": ["Caudal reducido por acumulación de sedimentos.", "Obstrucción parcial en cámara de inspección."],
    "valvula": ["Válvula de corte no cierra completamente.", "Válvula reguladora de presión descalibrada."],
    "bomba": ["Bomba con vibración excesiva.", "Bomba no parte en modo automático.", "Sobrecalentamiento de motor."],
    "electrica": ["Corte de suministro eléctrico en la instalación.", "Disparo de protección en tablero general."],
    "instrumentacion": ["Sensor de nivel entrega lecturas erráticas.", "Caudalímetro sin señal."],
    "control": ["Pérdida de comunicación con el PLC.", "Tablero de control en modo manual por alarma."],
    "calidad": ["Turbiedad sobre el límite de operación.", "Cloro residual bajo el rango esperado."],
}

USUARIOS = ["turno.a@ejemplo.com", "turno.b@ejemplo.com", "turno.noche@ejemplo.com", "telemetria@ejemplo.com"]

# Elementos que fallan más seguido, para que el ranking de reincidencia tenga sentido.
REINCIDENTES = {"EB-03": 7, "TUB-016": 6, "TUB-007": 4, "EB-01": 4, "PTA-02": 3}

FALLAS_POR_TIPO_NODO = {
    "captacion": ["instrumentacion", "calidad"],
    "planta": ["calidad", "control", "electrica"],
    "bombeo": ["bomba", "bomba", "electrica", "control"],
    "estanque": ["instrumentacion", "control"],
}
FALLAS_ARCO = ["fuga", "fuga", "rotura", "valvula", "obstruccion"]
HORAS_POR_SEVERIDAD = {"baja": (2, 12), "media": (4, 30), "alta": (6, 72)}


def _iso(momento: datetime) -> str:
    return momento.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cargar_red(conexion: sqlite3.Connection) -> tuple[dict, dict]:
    """Inserta nodos y arcos desde el archivo de ejemplo; devuelve código → fila."""
    red = json.loads(RUTA_RED.read_text(encoding="utf-8"))
    nodos = {}
    for nodo in red["nodos"]:
        cursor = conexion.execute(
            "INSERT INTO nodo (codigo, nombre, tipo, x, y, clientes, anio_servicio) "
            "VALUES (:codigo, :nombre, :tipo, :x, :y, :clientes, :anio_servicio)", nodo)
        nodos[nodo["codigo"]] = {"id": cursor.lastrowid, **nodo}
    arcos = {}
    for arco in red["arcos"]:
        cursor = conexion.execute(
            "INSERT INTO arco (codigo, origen_id, destino_id, tipo, longitud_m, diametro_mm, "
            "material, anio_instalacion, bidireccional) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (arco["codigo"], nodos[arco["origen"]]["id"], nodos[arco["destino"]]["id"],
             arco["tipo"], arco["longitud_m"], arco["diametro_mm"], arco["material"],
             arco["anio_instalacion"], int(arco["bidireccional"])))
        arcos[arco["codigo"]] = {"id": cursor.lastrowid, **arco}
    return nodos, arcos


def _insertar_averia(conexion: sqlite3.Connection, codigo: str, elemento: tuple[str, int],
                     tipo_falla: str, severidad: str, fuera_servicio: bool, descripcion: str,
                     usuario: str, reportada: datetime, cerrada: datetime | None,
                     estado_abierto: str = "abierta") -> None:
    """Inserta una avería y, si corresponde, la cierra para que el trigger deje la bitácora."""
    tipo_elemento, elemento_id = elemento
    cursor = conexion.execute(
        "INSERT INTO averia (codigo, nodo_id, arco_id, tipo_falla, severidad, fuera_servicio, "
        "estado, descripcion, reportada_por, reportada_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (codigo,
         elemento_id if tipo_elemento == "nodo" else None,
         elemento_id if tipo_elemento == "arco" else None,
         tipo_falla, severidad, int(fuera_servicio), estado_abierto, descripcion, usuario,
         _iso(reportada)))
    if cerrada is not None:
        conexion.execute(
            "UPDATE averia SET estado = 'cerrada', cerrada_en = ?, actualizada_por = ? WHERE id = ?",
            (_iso(cerrada), usuario, cursor.lastrowid))


def _generar_historial(conexion: sqlite3.Connection, nodos: dict, arcos: dict,
                       rng: random.Random, ahora: datetime, cantidad: int,
                       con_abiertas: bool) -> None:
    """Genera averías cerradas repartidas en los últimos 18 meses y tres abiertas."""
    candidatos, pesos = [], []
    for codigo, nodo in nodos.items():
        if nodo["tipo"] in FALLAS_POR_TIPO_NODO:
            candidatos.append(("nodo", codigo))
            pesos.append(REINCIDENTES.get(codigo, 1))
    for codigo in arcos:
        candidatos.append(("arco", codigo))
        pesos.append(REINCIDENTES.get(codigo, 1))

    eventos = []
    for _ in range(cantidad):
        tipo_elemento, codigo = rng.choices(candidatos, weights=pesos)[0]
        if tipo_elemento == "nodo":
            elemento = ("nodo", nodos[codigo]["id"])
            tipo_falla = rng.choice(FALLAS_POR_TIPO_NODO[nodos[codigo]["tipo"]])
        else:
            elemento = ("arco", arcos[codigo]["id"])
            tipo_falla = rng.choice(FALLAS_ARCO)
        severidad = rng.choices(["baja", "media", "alta"], weights=[5, 4, 2])[0]
        fuera = tipo_falla == "rotura" or (severidad == "alta" and rng.random() < 0.7)
        minimo, maximo = HORAS_POR_SEVERIDAD[severidad]
        reportada = ahora - timedelta(days=rng.uniform(3, 540), minutes=rng.randint(0, 59))
        cerrada = reportada + timedelta(hours=rng.uniform(minimo, maximo))
        eventos.append((reportada, cerrada, elemento, tipo_falla, severidad, fuera))

    correlativos: dict[int, int] = {}
    for reportada, cerrada, elemento, tipo_falla, severidad, fuera in sorted(eventos, key=lambda e: e[0]):
        correlativos[reportada.year] = correlativos.get(reportada.year, 0) + 1
        codigo = f"AV-{reportada.year}-{correlativos[reportada.year]:04d}"
        _insertar_averia(conexion, codigo, elemento, tipo_falla, severidad, fuera,
                         rng.choice(DESCRIPCIONES[tipo_falla]), rng.choice(USUARIOS),
                         reportada, cerrada)

    if not con_abiertas:
        return
    # Averías abiertas: muestran un corte real, una falla cubierta por la
    # redundancia de la red y una falla menor que no detiene nada.
    siguiente = correlativos.get(ahora.year, 0)
    abiertas = [
        (("arco", arcos["TUB-019"]["id"]), "rotura", "alta", True,
         "Rotura de matriz por trabajos de terceros. Cuadrilla en terreno.", 5.5, "en_reparacion"),
        (("nodo", nodos["EB-02"]["id"]), "electrica", "alta", True,
         "Disparo de protección en tablero general. El sector se abastece por la interconexión.",
         9.0, "en_reparacion"),
        (("nodo", nodos["EST-03"]["id"]), "instrumentacion", "baja", False,
         "Sensor de nivel entrega lecturas erráticas. Estanque operando con medición manual.",
         26.0, "abierta"),
    ]
    for elemento, tipo_falla, severidad, fuera, descripcion, horas, estado in abiertas:
        siguiente += 1
        _insertar_averia(conexion, f"AV-{ahora.year}-{siguiente:04d}", elemento, tipo_falla,
                         severidad, fuera, descripcion, rng.choice(USUARIOS),
                         ahora - timedelta(hours=horas), None, estado_abierto=estado)


def construir(ruta: Path | str = RUTA_DEMO, semilla: int = 11, ahora: datetime | None = None,
              averias: int = 72, con_abiertas: bool = True) -> Path:
    """Crea la base desde cero.

    Las pruebas usan `averias=0, con_abiertas=False` para partir de la red intacta.
    """
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.exists():
        ruta.unlink()
    ahora = (ahora or datetime.now(timezone.utc)).replace(microsecond=0)
    conexion = conectar(ruta)
    try:
        conexion.executescript(RUTA_ESQUEMA.read_text(encoding="utf-8"))
        with conexion:
            nodos, arcos = _cargar_red(conexion)
            conexion.executemany("INSERT INTO tipo_falla (codigo, nombre, aplica_a) VALUES (?, ?, ?)",
                                 TIPOS_FALLA)
            _generar_historial(conexion, nodos, arcos, random.Random(semilla), ahora, averias,
                               con_abiertas)
            conexion.execute("INSERT INTO meta (clave, valor) VALUES ('construida_en', ?)",
                             (_iso(ahora),))
        conexion.execute("VACUUM")
    finally:
        conexion.close()
    return ruta


def main() -> None:
    parser = argparse.ArgumentParser(description="Construye la base de demostración de averías")
    parser.add_argument("--salida", type=Path, default=RUTA_DEMO)
    parser.add_argument("--semilla", type=int, default=11)
    args = parser.parse_args()
    ruta = construir(args.salida, args.semilla)
    conexion = conectar(ruta)
    try:
        totales = conexion.execute(
            "SELECT (SELECT COUNT(*) FROM nodo), (SELECT COUNT(*) FROM arco), "
            "(SELECT COUNT(*) FROM averia), (SELECT COUNT(*) FROM averia WHERE estado <> 'cerrada')"
        ).fetchone()
    finally:
        conexion.close()
    print(f"{ruta}: {totales[0]} nodos, {totales[1]} arcos, {totales[2]} averías ({totales[3]} abiertas)")


if __name__ == "__main__":
    main()
