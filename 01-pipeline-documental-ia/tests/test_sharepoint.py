"""Conector de SharePoint: se prueba contra respuestas simuladas de Microsoft Graph."""

from __future__ import annotations

import json

import pytest
import responses

from pipeline.almacenes import ErrorTransitorio
from pipeline.almacenes.sharepoint import GRAPH, AlmacenSharePoint, ClienteGraph, ErrorGraph

DRIVE = f"{GRAPH}/drives/b!biblioteca"


def _archivo(identificador, nombre, carpeta, huella):
    return {"id": identificador, "name": nombre, "size": 1234, "lastModifiedDateTime": "2024-05-02T10:00:00Z",
            "parentReference": {"path": f"/drives/b!biblioteca/root:{carpeta}"},
            "file": {"hashes": {"quickXorHash": huella}}}


@pytest.fixture
def almacen():
    esperas = []
    cliente = ClienteGraph(lambda: "token-de-prueba", espera=esperas.append)
    almacen = AlmacenSharePoint(cliente, "b!biblioteca")
    almacen.esperas = esperas
    return almacen


@responses.activate
def test_inventario_recorre_las_paginas_y_omite_carpetas_y_eliminados(almacen):
    responses.get(f"{DRIVE}/root/delta", json={
        "value": [
            {"id": "c1", "name": "Planos", "folder": {"childCount": 2}, "parentReference": {"path": "/drives/x/root:"}},
            _archivo("a1", "E-0505-B.dwg", "/Proyectos/PRY-006%20Bombeo%20Norte/planos", "hash-1"),
            {"id": "a9", "name": "borrado.dwg", "file": {}, "deleted": {"state": "deleted"}},
        ],
        "@odata.nextLink": f"{DRIVE}/root/delta?token=pagina2"})
    responses.get(f"{DRIVE}/root/delta?token=pagina2", json={
        "value": [_archivo("a2", "Copia de E-0505-B.dwg", "/Respaldo", "hash-1")],
        "@odata.deltaLink": f"{DRIVE}/root/delta?token=final"})

    archivos = list(almacen.listar())
    assert [(a.origen_id, a.ruta, a.nombre, a.hash) for a in archivos] == [
        ("a1", "Proyectos/PRY-006 Bombeo Norte/planos", "E-0505-B.dwg", "hash-1"),
        ("a2", "Respaldo", "Copia de E-0505-B.dwg", "hash-1")]
    assert responses.calls[0].request.headers["Authorization"] == "Bearer token-de-prueba"


@responses.activate
def test_respeta_retry_after_cuando_graph_limita_las_solicitudes(almacen):
    responses.get(f"{DRIVE}/items/a1/content", status=429, headers={"Retry-After": "7"})
    responses.get(f"{DRIVE}/items/a1/content", status=503)
    responses.get(f"{DRIVE}/items/a1/content", body=b"AC1027\x00\x00resto del archivo")
    assert almacen.leer_inicio("a1", 6) == b"AC1027"
    assert almacen.esperas[0] == 7 and len(almacen.esperas) == 2
    assert responses.calls[0].request.headers["Range"] == "bytes=0-5"


@responses.activate
def test_si_el_limite_persiste_avisa_que_conviene_reintentar(almacen):
    responses.get(f"{DRIVE}/items/a1/content", status=429, headers={"Retry-After": "30"})
    with pytest.raises(ErrorTransitorio) as error:
        almacen.leer("a1")
    assert error.value.esperar == 30 and len(responses.calls) == 4


@responses.activate
def test_reubicar_crea_las_carpetas_mueve_y_escribe_las_columnas(almacen):
    responses.get(f"{DRIVE}/root:/Planos/ELE%20El%C3%A9ctrica", status=404, json={"error": {"message": "no existe"}})
    responses.get(f"{DRIVE}/root:/Planos", json={"id": "carpeta-planos"})
    responses.post(f"{DRIVE}/items/carpeta-planos/children", json={"id": "carpeta-ele"}, status=201)
    responses.patch(f"{DRIVE}/items/a1", json={"id": "a1"})
    responses.patch(f"{DRIVE}/items/a1/listItem/fields", json={})

    almacen.reubicar("a1", "Planos/ELE Eléctrica/PRY-006-ELE-0505-H01-RevB.dwg", {"Proyecto": "PRY-006", "Revision": "B"})
    crear, mover, columnas = [json.loads(llamada.request.body) for llamada in responses.calls[2:]]
    assert crear == {"name": "ELE Eléctrica", "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
    assert mover == {"parentReference": {"id": "carpeta-ele"}, "name": "PRY-006-ELE-0505-H01-RevB.dwg"}
    assert columnas == {"Proyecto": "PRY-006", "Revision": "B"}

    # La carpeta ya resuelta no se vuelve a consultar.
    almacen.reubicar("a1", "Planos/ELE Eléctrica/otro.dwg", {})
    assert len(responses.calls) == 7


@responses.activate
def test_un_error_definitivo_trae_el_mensaje_de_graph(almacen):
    responses.get(f"{DRIVE}/items/zzz/content", status=404, json={"error": {"code": "itemNotFound", "message": "No existe"}})
    with pytest.raises(ErrorGraph, match="No existe") as error:
        almacen.leer("zzz")
    assert error.value.estado == 404
