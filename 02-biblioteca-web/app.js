// Biblioteca técnica: búsqueda por taxonomía y en lenguaje natural sobre el catálogo
// que genera el pipeline documental. Todo texto del catálogo se inserta como texto.

import { interpretar, sinAcentos } from './interprete.js';

const $ = (id) => document.getElementById(id);
const numero = new Intl.NumberFormat('es-CL');
const POR_PAGINA = 40;
const COLECCIONES = ['plano', 'documento', 'instalacion'];
const ROTULO_COLECCION = { plano: 'Planos', documento: 'Documentos', instalacion: 'Instalaciones' };
const TIPO_INSTALACION = { captacion: 'Captación', planta: 'Planta de tratamiento', bombeo: 'Estación de bombeo',
  estanque: 'Estanque', union: 'Nodo de unión' };
const FUENTE = { nombre: 'Nombre del archivo', ia: 'Cajetín leído por IA', 'nombre+ia': 'Nombre y cajetín leído por IA' };

const estado = {
  catalogo: null,
  nombres: {},              // código -> nombre, por cada lista de la taxonomía
  coleccion: 'plano',
  filtros: filtrosVacios(),
  seleccion: null,
  visibles: POR_PAGINA,
  conServidor: false,       // hay servidor con credenciales para interpretar con IA
  motor: null,              // quién interpretó la última consulta
  aviso: '',
};

function filtrosVacios() {
  return { disciplinas: new Set(), tipos: new Set(), proyectos: new Set(), instalaciones: new Set(),
    formatos: new Set(), fuentes: new Set(), anioDesde: null, anioHasta: null, texto: '' };
}

/** Crea un elemento; los hijos de tipo texto se insertan como texto. */
function h(etiqueta, atributos = {}, ...hijos) {
  const elemento = document.createElement(etiqueta);
  for (const [clave, valor] of Object.entries(atributos)) {
    if (valor == null || valor === false) continue;
    if (clave === 'class') elemento.className = valor;
    else if (clave.startsWith('on')) elemento.addEventListener(clave.slice(2), valor);
    else elemento.setAttribute(clave, valor === true ? '' : valor);
  }
  for (const hijo of hijos.flat(Infinity)) {
    if (hijo == null || hijo === false) continue;
    elemento.append(hijo instanceof Node ? hijo : document.createTextNode(String(hijo)));
  }
  return elemento;
}

function poner(contenedor, ...hijos) {
  contenedor.replaceChildren(...hijos.flat(Infinity).filter((hijo) => hijo != null && hijo !== false));
}

const plural = (cantidad, uno, varios) => `${numero.format(cantidad)} ${cantidad === 1 ? uno : varios}`;
const nombreDe = (lista, codigo) => estado.nombres[lista][codigo] || codigo;

// -------------------------------------------------------------------- Carga

async function iniciar() {
  try {
    const respuesta = await fetch('datos/catalogo.json');
    if (!respuesta.ok) throw new Error('no se encontró datos/catalogo.json');
    estado.catalogo = await respuesta.json();
  } catch (error) {
    poner($('lista'), h('li', { class: 'vacio-lista' }, `No se pudo cargar el catálogo: ${error.message}`));
    return;
  }
  const { taxonomia, instalaciones } = estado.catalogo;
  const mapa = (lista) => Object.fromEntries(lista.map((elemento) => [elemento.codigo, elemento.nombre]));
  estado.nombres = { disciplinas: mapa(taxonomia.disciplinas), tipos_plano: mapa(taxonomia.tipos_plano),
    tipos_documento: mapa(taxonomia.tipos_documento), proyectos: mapa(taxonomia.proyectos), instalaciones: mapa(instalaciones) };
  for (const item of estado.catalogo.items) {
    item.indice = sinAcentos([item.proyecto, nombreDe('proyectos', item.proyecto), item.disciplina, item.numero, item.titulo,
      nombreDe('disciplinas', item.disciplina), nombreDe(item.clase === 'plano' ? 'tipos_plano' : 'tipos_documento', item.tipo),
      item.instalacion, nombreDe('instalaciones', item.instalacion), codigoDe(item)].filter(Boolean).join(' '));
  }

  await detectarServidor();
  $('modo').hidden = false;
  $('modo').textContent = estado.conServidor ? 'Búsqueda interpretada por IA' : 'Búsqueda interpretada con reglas locales';

  $('buscador').addEventListener('submit', (evento) => { evento.preventDefault(); buscar($('consulta').value); });
  $('mas').addEventListener('click', () => { estado.visibles += POR_PAGINA; pintarLista(); });
  for (const coleccion of COLECCIONES) {
    $(`tab-${coleccion}`).addEventListener('click', () => cambiarColeccion(coleccion));
  }
  const r = estado.catalogo.resumen;
  $('resumen').textContent = `Catálogo construido a partir de ${numero.format(r.archivos)} archivos de origen, `
    + `con ${numero.format(r.copias)} copias exactas descartadas. `;
  pintar();
}

function cambiarColeccion(coleccion) {
  if (coleccion !== estado.coleccion) {
    // Los tipos son distintos en cada colección, y los documentos no tienen disciplina.
    estado.filtros.tipos.clear();
    if (coleccion !== 'plano') {
      estado.filtros.disciplinas.clear();
      estado.filtros.fuentes.clear();
    }
  }
  estado.coleccion = coleccion;
  estado.seleccion = null;
  estado.visibles = POR_PAGINA;
  pintar();
}

async function detectarServidor() {
  if (location.hostname.endsWith('.github.io')) return;
  try {
    const respuesta = await fetch('api/salud', { cache: 'no-store' });
    if (respuesta.ok && (respuesta.headers.get('content-type') || '').includes('json')) {
      estado.conServidor = Boolean((await respuesta.json()).ia);
    }
  } catch {
    // Sin servidor: se interpretan las consultas con las reglas locales.
  }
}

// ----------------------------------------------------------------- Búsqueda

async function buscar(consulta) {
  consulta = consulta.trim();
  estado.aviso = '';
  let resultado = null;
  if (consulta && estado.conServidor) {
    try {
      const respuesta = await fetch('api/interpretar', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ consulta }) });
      if (!respuesta.ok) throw new Error((await respuesta.json()).detail || `error ${respuesta.status}`);
      resultado = await respuesta.json();
      estado.motor = 'Claude';
    } catch (error) {
      estado.aviso = `La interpretación por IA no respondió (${error.message}); se usaron las reglas locales.`;
    }
  }
  if (!resultado) {
    resultado = interpretar(consulta, { proyectos: estado.catalogo.taxonomia.proyectos, instalaciones: estado.catalogo.instalaciones });
    estado.motor = consulta ? 'reglas locales' : null;
  }
  const filtros = filtrosVacios();
  filtros.disciplinas = new Set(resultado.disciplinas);
  filtros.tipos = new Set(resultado.tipos);
  filtros.proyectos = new Set(resultado.proyectos);
  filtros.instalaciones = new Set(resultado.instalaciones);
  filtros.formatos = new Set(resultado.formatos);
  filtros.anioDesde = resultado.anio_desde;
  filtros.anioHasta = resultado.anio_hasta;
  filtros.texto = resultado.texto || '';
  estado.filtros = filtros;
  if (resultado.clase) estado.coleccion = resultado.clase;
  else if (estado.coleccion === 'instalacion') estado.coleccion = 'plano';
  estado.seleccion = null;
  estado.visibles = POR_PAGINA;
  pintar();
}

/** ¿El ítem cumple los filtros? `omitir` deja fuera una faceta, para contar sus opciones. */
function coincide(item, omitir) {
  const f = estado.filtros;
  const dentro = (conjunto, valor, clave) => clave === omitir || conjunto.size === 0 || conjunto.has(valor);
  if (item.clase !== estado.coleccion) return false;
  if (!dentro(f.disciplinas, item.disciplina, 'disciplinas')) return false;
  if (!dentro(f.tipos, item.tipo, 'tipos')) return false;
  if (!dentro(f.proyectos, item.proyecto, 'proyectos')) return false;
  if (!dentro(f.instalaciones, item.instalacion, 'instalaciones')) return false;
  if (!dentro(f.fuentes, item.fuente === 'nombre' ? 'nombre' : 'ia', 'fuentes')) return false;
  if (omitir !== 'formatos' && f.formatos.size && !item.formatos.some((formato) => f.formatos.has(formato))) return false;
  if (f.anioDesde && item.anio < f.anioDesde) return false;
  if (f.anioHasta && item.anio > f.anioHasta) return false;
  return f.texto.split(' ').filter(Boolean).every((palabra) => item.indice.includes(palabra));
}

const codigoDe = (item) => (item.clase === 'plano'
  ? `${item.proyecto}-${item.disciplina}-${item.numero}` : `${item.proyecto}-${item.tipo}-${item.numero}`);

// ------------------------------------------------------------------ Pintado

function pintar() {
  for (const coleccion of COLECCIONES) {
    const boton = $(`tab-${coleccion}`);
    const cantidad = coleccion === 'instalacion' ? estado.catalogo.instalaciones.length
      : estado.catalogo.items.filter((item) => item.clase === coleccion).length;
    boton.setAttribute('aria-selected', String(coleccion === estado.coleccion));
    poner(boton, ROTULO_COLECCION[coleccion], h('span', { class: 'cantidad' }, numero.format(cantidad)));
  }
  document.querySelector('.columnas').classList.toggle('sin-facetas', estado.coleccion === 'instalacion');
  pintarInterpretacion();
  if (estado.coleccion === 'instalacion') {
    pintarInstalaciones();
  } else {
    pintarFacetas();
    pintarLista();
  }
  pintarDetalle();
}

function pintarInterpretacion() {
  const f = estado.filtros;
  const activos = [];
  const agregar = (conjunto, lista, rotulo) => {
    for (const codigo of conjunto) {
      activos.push(h('span', { class: 'filtro-activo' }, `${rotulo}: ${lista ? nombreDe(lista, codigo) : codigo.toUpperCase()}`,
        h('button', { type: 'button', 'aria-label': `Quitar filtro ${rotulo}`, onclick: () => { conjunto.delete(codigo); refrescar(); } }, '×')));
    }
  };
  agregar(f.disciplinas, 'disciplinas', 'Disciplina');
  agregar(f.tipos, estado.coleccion === 'documento' ? 'tipos_documento' : 'tipos_plano', 'Tipo');
  agregar(f.proyectos, 'proyectos', 'Proyecto');
  agregar(f.instalaciones, 'instalaciones', 'Instalación');
  agregar(f.formatos, null, 'Formato');
  agregar(f.fuentes, null, 'Origen');
  if (f.anioDesde || f.anioHasta) {
    const texto = f.anioDesde && f.anioHasta ? (f.anioDesde === f.anioHasta ? `${f.anioDesde}` : `${f.anioDesde} a ${f.anioHasta}`)
      : f.anioDesde ? `desde ${f.anioDesde}` : `hasta ${f.anioHasta}`;
    activos.push(h('span', { class: 'filtro-activo' }, `Año del proyecto: ${texto}`,
      h('button', { type: 'button', 'aria-label': 'Quitar filtro de año', onclick: () => { f.anioDesde = f.anioHasta = null; refrescar(); } }, '×')));
  }
  if (f.texto) {
    activos.push(h('span', { class: 'filtro-activo' }, `Texto: ${f.texto}`,
      h('button', { type: 'button', 'aria-label': 'Quitar texto', onclick: () => { f.texto = ''; refrescar(); } }, '×')));
  }
  poner($('interpretacion'),
    estado.aviso && h('span', {}, estado.aviso),
    estado.motor && activos.length > 0 && h('span', {}, `Consulta interpretada por ${estado.motor}:`),
    activos,
    activos.length > 0 && h('button', { class: 'enlace', type: 'button', onclick: () => {
      estado.filtros = filtrosVacios(); estado.motor = null; $('consulta').value = ''; refrescar();
    } }, 'Limpiar filtros'));
}

function refrescar() {
  estado.visibles = POR_PAGINA;
  pintar();
}

function faceta(titulo, clave, opciones) {
  const conjunto = estado.filtros[clave];
  const base = estado.catalogo.items.filter((item) => coincide(item, clave));
  const filas = opciones.map(([valor, rotulo, cuenta]) => {
    const cantidad = base.filter(cuenta).length;
    if (cantidad === 0 && !conjunto.has(valor)) return null;
    const casilla = h('input', { type: 'checkbox', checked: conjunto.has(valor) });
    casilla.addEventListener('change', () => { if (casilla.checked) conjunto.add(valor); else conjunto.delete(valor); refrescar(); });
    return h('li', {}, h('label', { class: cantidad === 0 ? 'vacio' : null }, casilla, h('span', {}, rotulo), h('small', {}, numero.format(cantidad))));
  }).filter(Boolean);
  return filas.length ? h('div', { class: 'faceta' }, h('h3', {}, titulo), h('ul', {}, filas)) : null;
}

function pintarFacetas() {
  const { taxonomia } = estado.catalogo;
  const f = estado.filtros;
  const esPlano = estado.coleccion === 'plano';
  const tipos = esPlano ? taxonomia.tipos_plano : taxonomia.tipos_documento;
  const formatos = [...new Set(estado.catalogo.items.filter((i) => i.clase === estado.coleccion).flatMap((i) => i.formatos))].sort();
  const campoAnio = (clave, rotulo) => {
    const entrada = h('input', { type: 'number', min: 1990, max: 2100, value: f[clave] || '', 'aria-label': rotulo, placeholder: rotulo });
    entrada.addEventListener('change', () => { f[clave] = Number(entrada.value) || null; refrescar(); });
    return entrada;
  };
  poner($('facetas'),
    esPlano && faceta('Disciplina', 'disciplinas', taxonomia.disciplinas.map((d) => [d.codigo, d.nombre, (i) => i.disciplina === d.codigo])),
    faceta(esPlano ? 'Tipo de plano' : 'Tipo de documento', 'tipos', tipos.map((t) => [t.codigo, t.nombre, (i) => i.tipo === t.codigo])),
    faceta('Proyecto', 'proyectos', taxonomia.proyectos.map((p) => [p.codigo, `${p.codigo} ${p.nombre}`, (i) => i.proyecto === p.codigo])),
    h('div', { class: 'faceta' }, h('h3', {}, 'Año del proyecto'),
      h('div', { class: 'rango' }, campoAnio('anioDesde', 'Desde'), 'a', campoAnio('anioHasta', 'Hasta'))),
    faceta('Formato', 'formatos', formatos.map((formato) => [formato, formato.toUpperCase(), (i) => i.formatos.includes(formato)])),
    esPlano && faceta('Origen de los datos', 'fuentes', [
      ['nombre', FUENTE.nombre, (i) => i.fuente === 'nombre'],
      ['ia', FUENTE.ia, (i) => i.fuente !== 'nombre']]));
}

function pintarLista() {
  const resultados = estado.catalogo.items.filter((item) => coincide(item));
  const total = estado.catalogo.items.filter((item) => item.clase === estado.coleccion).length;
  $('conteo').textContent = resultados.length === total
    ? plural(total, estado.coleccion, `${estado.coleccion}s`)
    : `${plural(resultados.length, 'resultado', 'resultados')} de ${numero.format(total)}`;
  const tipos = estado.coleccion === 'plano' ? 'tipos_plano' : 'tipos_documento';
  poner($('lista'), resultados.length === 0
    ? h('li', { class: 'vacio-lista' }, 'Ningún resultado con estos filtros. Quita alguno o prueba con otras palabras.')
    : resultados.slice(0, estado.visibles).map((item) => h('li', {}, h('button', {
      class: 'item', type: 'button', 'aria-current': estado.seleccion === item.id ? 'true' : null,
      onclick: () => { estado.seleccion = item.id; pintarLista(); pintarDetalle(); },
    },
      h('div', { class: 'item-codigo' }, codigoDe(item), item.clase === 'plano' && ` · hoja ${Number(item.hoja)}`),
      h('div', { class: item.titulo ? 'item-titulo' : 'item-titulo pendiente' },
        item.titulo || 'Sin título: el nombre del archivo no lo trae y el cajetín aún no se lee'),
      h('div', { class: 'etiquetas' },
        item.clase === 'plano' && h('span', { class: 'etiqueta' }, nombreDe('disciplinas', item.disciplina)),
        item.tipo && h('span', { class: 'etiqueta' }, nombreDe(tipos, item.tipo)),
        h('span', { class: 'etiqueta fuerte' }, `Rev. ${item.revision}`),
        h('span', { class: 'etiqueta' }, item.anio),
        h('span', { class: 'etiqueta' }, item.formatos.map((formato) => formato.toUpperCase()).join(' · ')),
        item.revisiones.length > 1 && h('span', { class: 'etiqueta' }, plural(item.revisiones.length, 'revisión', 'revisiones')))))));
  $('mas').hidden = resultados.length <= estado.visibles;
}

function pintarInstalaciones() {
  const cuenta = (codigo, clase) => estado.catalogo.items.filter((i) => i.instalacion === codigo && i.clase === clase).length;
  $('conteo').textContent = plural(estado.catalogo.instalaciones.length, 'instalación', 'instalaciones');
  $('mas').hidden = true;
  const conCuentas = estado.catalogo.instalaciones
    .map((instalacion) => ({ instalacion, planos: cuenta(instalacion.codigo, 'plano'), documentos: cuenta(instalacion.codigo, 'documento') }))
    .sort((a, b) => (b.planos + b.documentos) - (a.planos + a.documentos));
  poner($('lista'), conCuentas.map(({ instalacion, planos, documentos }) => {
    return h('li', {}, h('button', {
      class: 'item', type: 'button', disabled: planos + documentos === 0,
      onclick: () => {
        estado.filtros = filtrosVacios();
        estado.filtros.instalaciones.add(instalacion.codigo);
        estado.coleccion = planos ? 'plano' : 'documento';
        estado.motor = null;
        refrescar();
      },
    },
      h('div', { class: 'item-codigo' }, `${instalacion.codigo} · ${TIPO_INSTALACION[instalacion.tipo] || instalacion.tipo}`),
      h('div', { class: 'item-titulo' }, instalacion.nombre),
      h('div', { class: 'etiquetas' },
        h('span', { class: 'etiqueta' }, plural(planos, 'plano', 'planos')),
        h('span', { class: 'etiqueta' }, plural(documentos, 'documento', 'documentos')))));
  }));
}

function pintarDetalle() {
  const item = estado.catalogo.items.find((candidato) => candidato.id === estado.seleccion);
  if (!item) {
    poner($('detalle'), h('p', { class: 'nota' }, estado.coleccion === 'instalacion'
      ? 'Elige una instalación para ver sus planos y documentos.'
      : 'Elige un resultado para ver su ficha, sus revisiones y su ubicación en la biblioteca.'));
    return;
  }
  const esPlano = item.clase === 'plano';
  const estadoRevision = (vigente) => h('span', { class: 'estado' },
    h('i', { style: `background: var(${vigente ? '--bien' : '--tenue'})` }), vigente ? 'Vigente' : 'Superada');
  poner($('detalle'),
    h('div', { class: 'codigo' }, codigoDe(item), esPlano && ` · hoja ${Number(item.hoja)}`),
    h('h2', {}, item.titulo || 'Sin título'),
    esPlano && (item.vista
      ? h('img', { class: 'vista', src: `datos/${item.vista}`, alt: `Vista previa del plano ${codigoDe(item)}`, loading: 'lazy' })
      : h('div', { class: 'sin-vista' }, 'Vista previa no disponible en esta demostración')),
    h('dl', { class: 'ficha' },
      h('dt', {}, 'Proyecto'), h('dd', {}, `${item.proyecto} · ${nombreDe('proyectos', item.proyecto)} (${item.anio})`),
      h('dt', {}, 'Instalación'), h('dd', {}, `${item.instalacion} · ${nombreDe('instalaciones', item.instalacion)}`),
      esPlano && [h('dt', {}, 'Disciplina'), h('dd', {}, nombreDe('disciplinas', item.disciplina))],
      h('dt', {}, 'Tipo'), h('dd', {}, item.tipo ? nombreDe(esPlano ? 'tipos_plano' : 'tipos_documento', item.tipo) : 'Sin clasificar'),
      h('dt', {}, 'Revisión vigente'), h('dd', {}, `${item.revision}, del ${item.fecha.split('-').reverse().join('-')}`),
      h('dt', {}, 'Origen de los datos'), h('dd', {}, FUENTE[item.fuente] || item.fuente),
      item.copias > 0 && [h('dt', {}, 'Copias'), h('dd', {}, `${plural(item.copias, 'copia exacta encontrada', 'copias exactas encontradas')} en el origen`)]),
    h('div', { class: 'seccion' },
      h('h3', {}, 'Revisiones'),
      h('table', {},
        h('thead', {}, h('tr', {}, h('th', {}, 'Rev.'), h('th', {}, 'Fecha'), h('th', {}, 'Estado'), h('th', {}, 'Ubicación en la biblioteca'))),
        h('tbody', {}, item.revisiones.map((revision) => h('tr', {},
          h('td', {}, revision.revision), h('td', {}, revision.fecha.split('-').reverse().join('-')),
          h('td', {}, estadoRevision(revision.vigente)),
          h('td', { class: 'ruta' }, revision.archivos.map((archivo) => h('div', {}, archivo.destino)))))))));
}

iniciar();
