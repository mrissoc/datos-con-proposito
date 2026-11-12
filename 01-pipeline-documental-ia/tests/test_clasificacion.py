"""Clasificación por nombre: una prueba por cada convención del repositorio."""

from __future__ import annotations

import pytest

from pipeline.etapas.clasificacion import clasificar_nombre, faltantes, nombre_limpio, orden_revision
from pipeline.etapas.inventario import extension_de

CASOS = [
    # nombre, carpeta, categoría, campos esperados
    ("PRY-021-ELE-DU-0045-H02-RevB Diagrama unilineal general.dwg", "Proyectos/2018/x", "cad",
     dict(clase="plano", patron="moderna", proyecto="PRY-021", disciplina="ELE", tipo_doc="DU", numero="0045",
          hoja="02", revision="B", titulo="Diagrama unilineal general")),
    ("PRY-021-HID-PL-0301-H01-Rev0.pdf", "", "pdf",
     dict(clase="plano", patron="moderna", proyecto="PRY-021", disciplina="HID", tipo_doc="PL", numero="0301",
          hoja="01", revision="0", titulo=None)),
    ("PRY014_EL_0503_1_A Detalles de montaje de tableros.dwg", "Proyectos/2013/PRY-014 Estanque/PLANOS/EL", "cad",
     dict(clase="plano", patron="guiones", proyecto="PRY-014", disciplina="ELE", tipo_doc="DE", numero="0503",
          hoja="01", revision="A", titulo="Detalles de montaje de tableros")),
    ("CTX-0501-ELE-R1-L2 Diagrama unilineal general.dxf", "Proyectos/2015/PRY-017 Bombeo/Contratista CTX", "cad",
     dict(clase="plano", patron="contratista", proyecto="PRY-017", disciplina="ELE", tipo_doc="DU", numero="0501",
          hoja="02", revision="1")),
    ("E-0505-B.dxf", "Proyectos/2009/PRY-006 Estación de Bombeo Norte/planos", "cad",
     dict(clase="plano", patron="letra", proyecto="PRY-006", disciplina="ELE", tipo_doc=None, numero="0505",
          hoja="01", revision="B", titulo=None)),
    ("S-0201-3-C.dxf", "Proyectos/2011/Ampliacion planta norte/planos", "cad",
     dict(clase="plano", patron="letra", proyecto=None, disciplina="EST", numero="0201", hoja="03", revision="C")),
    ("PRY-019-MC-003 Memoria de cálculo estructural RevB.docx", "x/Documentos", "ofimatica",
     dict(clase="documento", patron="documento", proyecto="PRY-019", tipo_doc="MC", numero="003", revision="B",
          titulo="Memoria de cálculo estructural")),
    # Marcas de copia: no deben impedir reconocer el nombre original.
    ("Copia de PRY017_ME_0402_1_B Detalles de montaje de bombas.pdf", "Copia de planos (2)", "pdf",
     dict(clase="plano", patron="guiones", proyecto="PRY-017", disciplina="MEC", numero="0402", revision="B")),
    ("PRY-021-CIV-PL-0101-H01-RevB (1).dwg", "", "cad", dict(patron="moderna", numero="0101", revision="B")),
    ("PRY-021-CIV-PL-0101-H01-RevB - copia.dwg", "", "cad", dict(patron="moderna", titulo=None)),
    # Nombres que no dicen nada: un CAD sigue siendo un plano; un PDF queda sin clase.
    ("Plano12.dxf", "Varios/sin clasificar", "cad", dict(clase="plano", patron=None, numero=None, proyecto=None)),
    ("para imprimir 3.pdf", "Varios", "pdf", dict(clase=None, patron=None)),
    ("Especificación técnica de bombas rev A.doc", "Proyectos/2009/PRY-006 x/Documentos", "ofimatica",
     dict(clase=None, patron=None, proyecto="PRY-006")),
    # Un código de proyecto que no existe en la taxonomía no se acepta.
    ("PRY-999-ELE-DU-0001-H01-RevA.dwg", "", "cad", dict(patron="moderna", proyecto=None)),
    # Una disciplina desconocida deja el campo vacío en vez de inventarlo.
    ("PRY-021-XXX-PL-0001-H01-RevA.dwg", "", "cad", dict(patron="moderna", disciplina=None)),
]


@pytest.mark.parametrize("nombre, carpeta, categoria, esperado", CASOS, ids=[caso[0] for caso in CASOS])
def test_clasifica_por_nombre(taxonomia, nombre, carpeta, categoria, esperado):
    campos = clasificar_nombre(nombre, extension_de(nombre), carpeta, categoria, taxonomia)
    assert {clave: campos[clave] for clave in esperado} == esperado


def test_un_plano_incompleto_queda_para_revision(taxonomia):
    completo = clasificar_nombre("PRY-021-ELE-DU-0045-H02-RevB.dwg", "dwg", "", "cad", taxonomia)
    sin_proyecto = clasificar_nombre("S-0201-C.dxf", "dxf", "Varios", "cad", taxonomia)
    sin_nada = clasificar_nombre("Plano1.dwg", "dwg", "Varios", "cad", taxonomia)
    assert faltantes(completo) == []
    assert faltantes(sin_proyecto) == ["proyecto"]
    assert faltantes(sin_nada) == ["proyecto", "disciplina", "numero", "revision"]


def test_un_patron_de_plano_no_se_aplica_a_un_documento_de_oficina(taxonomia):
    campos = clasificar_nombre("PRY-021-ELE-DU-0045-H02-RevB.xlsx", "xlsx", "", "ofimatica", taxonomia)
    assert campos["patron"] is None


@pytest.mark.parametrize("titulo, tipo", [
    ("Planta general de emplazamiento", "PG"),
    ("Planta de tuberías", "PL"),
    ("Detalles de anclaje en planta", "DE"),          # manda la palabra que aparece primero
    ("Diagrama unilineal centro de control de motores", "DU"),
    ("P&ID sistema de impulsión", "PI"),
    ("ELEVACIONES DE MUROS", "CE"),
    ("Lazos de control de nivel", "EC"),
    ("Isométrico línea de succión", "IS"),
    ("Listado de materiales", None),
])
def test_tipo_de_plano_por_titulo(taxonomia, titulo, tipo):
    assert taxonomia.tipo_por_titulo(titulo) == tipo


def test_orden_de_revisiones():
    revisiones = ["1", "B", "0", "A", "C", "10", "2"]
    assert sorted(revisiones, key=orden_revision) == ["A", "B", "C", "0", "1", "2", "10"]
    assert orden_revision(None) is None


def test_nombre_limpio_y_extension():
    assert nombre_limpio("Copia de Copia de E-0505-B (2).dxf", "dxf") == "E-0505-B"
    assert nombre_limpio("M-0401-A_final.dwg", "dwg") == "M-0401-A"
    assert extension_de("Informe visita 12.03.2015") == "2015"       # no es una extensión real
    assert extension_de("LEEME") == "" and extension_de(".oculto") == ""
    assert extension_de("Plano.DWG") == "dwg"


def test_tipo_real_por_firma(reglas):
    assert reglas.tipo_por_firma(b"AC1027\x00\x00") == "dwg"
    assert reglas.tipo_por_firma(b"%PDF-1.7\n") == "pdf"
    assert reglas.tipo_por_firma(b"  0\r\nSECTION\r\n  2\r\nHEADER") == "dxf"
    assert reglas.tipo_por_firma(b"texto cualquiera") is None
    assert reglas.normalizar("JPEG") == "jpg" and reglas.categoria("dwg") == "cad" and reglas.categoria("xyz") == "otro"
