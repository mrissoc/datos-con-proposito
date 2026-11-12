// Aplicación de averías: estado, vistas y acciones.
// Los datos llegan por la capa de datos (API o SQLite en el navegador) y los
// textos que vienen de la base se insertan siempre como texto, nunca como HTML.

import { crearBackend, textoSql } from './datos.js';
import { Mapa, s, formaNodo, simboloEstado } from './mapa.js';

const $ = (id) => document.getElementById(id);
const numero = new Intl.NumberFormat('es-CL');
const decimal = new Intl.NumberFormat('es-CL', { maximumFractionDigits: 1 });
const fechaHora = new Intl.DateTimeFormat('es-CL', {
  day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
});
const mesLargo = new Intl.DateTimeFormat('es-CL', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const mesCorto = new Intl.DateTimeFormat('es-CL', { month: 'short', timeZone: 'UTC' });

const TIPO_NODO = {
  captacion: 'Captación', planta: 'Planta de tratamiento', bombeo: 'Estación de bombeo',
  estanque: 'Estanque', union: 'Nodo de unión', sector: 'Sector de distribución',
};
const TIPO_ARCO = {
  aduccion: 'Aducción', conduccion: 'Conducción', impulsion: 'Impulsión', matriz: 'Matriz',
  interconexion: 'Interconexión', distribucion: 'Distribución',
};
const ESTADO = {
  operativo: 'Operativo', fuera_servicio: 'Fuera de servicio', sin_suministro: 'Sin suministro',
  abierta: 'Abierta', en_reparacion: 'En reparación', cerrada: 'Cerrada',
};
const SEVERIDAD = { baja: 'baja', media: 'media', alta: 'alta' };
const PESTANAS = ['averias', 'elemento', 'criticidad', 'estadisticas'];

const estado = {
  backend: null,
  usuario: null,
  red: null,
  averias: [],
  tiposFalla: [],
  seleccion: null,        // { tipo: 'nodo' | 'arco', id }
  simulacion: null,       // { tipo, id, filas, nodos: Set }
  ruta: null,             // { nodoId, datos | null, nodos: [], arcos: [] }
  formulario: false,
  criticidad: null,
  estadisticas: null,
};

let mapa;

// ---------------------------------------------------------------- Utilidades

/** Crea un elemento HTML; los hijos de tipo texto se insertan como texto. */
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

/** Reemplaza el contenido de un contenedor, descartando los valores vacíos. */
function poner(contenedor, ...hijos) {
  contenedor.replaceChildren(...hijos.flat(Infinity).filter((hijo) => hijo != null && hijo !== false));
}

function duracion(horas) {
  if (horas < 1) return `${Math.max(1, Math.round(horas * 60))} min`;
  if (horas < 48) return `${decimal.format(horas)} h`;
  return `${numero.format(Math.round(horas / 24))} días`;
}

const plural = (cantidad, uno, varios) => `${numero.format(cantidad)} ${cantidad === 1 ? uno : varios}`;
const kilometros = (metros) => `${decimal.format(metros / 1000)} km`;
const fechaDe = (iso) => fechaHora.format(new Date(iso));
const porcentaje = (parte, total) => (total ? `${decimal.format((100 * parte) / total)} %` : '0 %');

function buscar(tipo, id) {
  const lista = tipo === 'nodo' ? estado.red.nodos : estado.red.arcos;
  return lista.find((elemento) => elemento.id === id);
}

/** Círculo de estado con su símbolo: el color nunca va solo. */
function icono(clave) {
  const color = {
    operativo: 'var(--bien)', cerrada: 'var(--bien)', fuera_servicio: 'var(--critico)', abierta: 'var(--critico)',
    sin_suministro: 'var(--serio)', en_reparacion: 'var(--alerta)', simulado: 'var(--alerta)',
  }[clave];
  const oscuro = clave === 'en_reparacion' || clave === 'simulado';
  const trazo = { class: `simbolo ${oscuro ? 'oscuro' : ''}`.trim() };
  let simbolo;
  if (clave === 'operativo' || clave === 'cerrada') simbolo = s('path', { ...trazo, d: 'M-3.6,0.2 L-1.1,2.8 L3.8,-2.6' });
  else if (clave === 'fuera_servicio') simbolo = simboloEstado('fuera_servicio');
  else if (clave === 'sin_suministro') simbolo = simboloEstado('sin_suministro');
  else if (clave === 'en_reparacion') simbolo = s('path', { ...trazo, d: 'M-3.6,0 h0.1 M0,0 h0.1 M3.5,0 h0.1' });
  else simbolo = simboloEstado('simulado', oscuro ? 'oscuro' : '');
  return s('svg', { viewBox: '-8 -8 16 16', width: 14, height: 14, 'aria-hidden': 'true' },
    s('circle', { r: 7.5, fill: color }), simbolo);
}

const chipEstado = (clave, texto) => h('span', { class: 'estado' }, icono(clave), texto || ESTADO[clave]);

/** Desplegable que muestra el archivo .sql con el que se calcula una vista. */
function verSql(nombre, rotulo = 'Ver consulta SQL') {
  const pre = h('pre', {}, 'Cargando…');
  const detalle = h('details', { class: 'plegable' }, h('summary', {}, rotulo), pre);
  detalle.addEventListener('toggle', async () => {
    if (!detalle.open || detalle.dataset.listo) return;
    detalle.dataset.listo = '1';
    try {
      pre.textContent = await textoSql(nombre);
    } catch (error) {
      pre.textContent = error.message;
    }
  });
  return detalle;
}

function tabla(columnas, filas, alElegir) {
  const cuerpo = filas.map((fila) => {
    const tr = h('tr', alElegir ? { class: 'clicable', tabindex: 0 } : {},
      columnas.map((columna) => h('td', { class: columna.num ? 'num' : columna.fijo ? 'fijo' : null }, columna.valor(fila))));
    if (alElegir) {
      tr.addEventListener('click', () => alElegir(fila));
      tr.addEventListener('keydown', (evento) => { if (evento.key === 'Enter') alElegir(fila); });
    }
    return tr;
  });
  return h('div', { class: 'tabla-contenedor' }, h('table', {},
    h('thead', {}, h('tr', {}, columnas.map((columna) => h('th', { class: columna.num ? 'num' : null }, columna.titulo)))),
    h('tbody', {}, cuerpo)));
}

function avisar(texto, esError = false) {
  const aviso = $('aviso');
  aviso.textContent = texto;
  aviso.className = esError ? 'aviso error' : 'aviso';
  aviso.hidden = !texto;
}

// ------------------------------------------------------------------ Tooltip

function mostrarTooltip(x, y, titulo, lineas) {
  const caja = $('tooltip');
  poner(caja, h('strong', {}, titulo), lineas.filter(Boolean).map((linea) => h('span', {}, linea)));
  caja.hidden = false;
  const ancho = caja.offsetWidth;
  const alto = caja.offsetHeight;
  const izquierda = Math.min(Math.max(8, x + 14), window.innerWidth - ancho - 8);
  const arriba = y + 16 + alto > window.innerHeight ? y - alto - 12 : y + 16;
  caja.style.left = `${izquierda}px`;
  caja.style.top = `${Math.max(8, arriba)}px`;
}

const ocultarTooltip = () => { $('tooltip').hidden = true; };

function apuntarElemento(tipo, elemento, x, y) {
  if (tipo === 'nodo') {
    mostrarTooltip(x, y, `${elemento.codigo} · ${elemento.nombre}`, [
      TIPO_NODO[elemento.tipo],
      ESTADO[elemento.estado],
      elemento.tipo === 'sector' && plural(elemento.clientes, 'cliente', 'clientes'),
      elemento.averias_abiertas > 0 && plural(elemento.averias_abiertas, 'avería abierta', 'averías abiertas'),
    ]);
  } else {
    mostrarTooltip(x, y, `${elemento.codigo} · ${TIPO_ARCO[elemento.tipo]}`, [
      elemento.nombre.replace(' → ', elemento.bidireccional ? ' ↔ ' : ' → '),
      `${kilometros(elemento.longitud_m)} · ${elemento.diametro_mm} mm · ${elemento.material}`,
      ESTADO[elemento.estado],
      elemento.averias_abiertas > 0 && plural(elemento.averias_abiertas, 'avería abierta', 'averías abiertas'),
    ]);
  }
}

// -------------------------------------------------------------------- Carga

async function iniciar() {
  try {
    estado.backend = await crearBackend();
    [estado.usuario, estado.tiposFalla] = await Promise.all([
      estado.backend.usuario(), estado.backend.tiposFalla()]);
  } catch (error) {
    avisar(`No se pudo iniciar la aplicación: ${error.message}`, true);
    return;
  }
  const modo = $('modo');
  modo.hidden = false;
  if (estado.backend.modo === 'api') {
    modo.textContent = 'Conectada a la API';
  } else {
    modo.textContent = 'Demostración sin servidor';
    avisar('Demostración sin servidor: la base SQLite y las consultas SQL se ejecutan en tu navegador. '
      + 'Puedes registrar y cerrar averías; los cambios se pierden al recargar la página.');
  }
  $('usuario').textContent = estado.usuario.nombre;

  mapa = new Mapa($('mapa'), { alSeleccionar: seleccionar, alApuntar: apuntarElemento, alSalir: ocultarTooltip });
  pintarLeyenda();
  conectarPestanas();
  $('quitar-resaltado').addEventListener('click', () => {
    estado.simulacion = null;
    estado.ruta = null;
    redibujar();
    pintarElemento();
  });
  await cargar();
}

async function cargar() {
  [estado.red, estado.averias] = await Promise.all([
    estado.backend.red(), estado.backend.averias('activas')]);
  estado.estadisticas = null;
  pintarIndicadores();
  redibujar();
  pintarTablaRed();
  pintarAverias();
  pintarElemento();
  if (!$('vista-estadisticas').hidden) pintarEstadisticas();
}

function redibujar() {
  mapa.dibujar(estado.red, {
    seleccion: estado.seleccion,
    simulacion: estado.simulacion,
    ruta: estado.ruta && estado.ruta.datos ? estado.ruta : null,
  });
  $('quitar-resaltado').hidden = !(estado.simulacion || estado.ruta);
}

async function intentar(accion) {
  try {
    await accion();
  } catch (error) {
    avisar(error.message, true);
  }
}

// --------------------------------------------------------------- Indicadores

function pintarIndicadores() {
  const r = estado.red.resumen;
  const sectores = estado.red.nodos.filter((nodo) => nodo.tipo === 'sector').length;
  const tarjeta = (rotulo, valor, nota) => h('div', { class: 'indicador' },
    h('div', { class: 'rotulo' }, rotulo), h('div', { class: 'valor' }, valor), h('div', { class: 'nota' }, nota));
  poner($('indicadores'), 
    tarjeta('Averías abiertas', numero.format(r.averias_abiertas),
      r.elementos_fuera_servicio
        ? [icono('fuera_servicio'), plural(r.elementos_fuera_servicio, 'elemento fuera de servicio', 'elementos fuera de servicio')]
        : 'Ningún elemento detenido'),
    tarjeta('Sectores sin suministro', `${r.sectores_sin_suministro} de ${sectores}`,
      r.sectores_sin_suministro ? [icono('sin_suministro'), 'Marcados en el esquema'] : [icono('operativo'), 'Todos abastecidos']),
    tarjeta('Clientes sin suministro', numero.format(r.clientes_sin_suministro),
      `${porcentaje(r.clientes_sin_suministro, r.clientes_totales)} de ${numero.format(r.clientes_totales)} clientes`),
    tarjeta('Tamaño de la red', `${r.nodos} nodos`, `${r.arcos} tramos de tubería`),
  );
}

// ------------------------------------------------------------------ Leyenda

function pintarLeyenda() {
  const muestra = (contenido) => s('svg', { viewBox: '-14 -10 28 20', width: 26, height: 18, 'aria-hidden': 'true' }, contenido);
  const nodo = (tipo, clase = '') => muestra(s('g', { class: `nodo ${tipo} ${clase}` },
    formaNodo(tipo, tipo === 'union' ? 5 : 7, 'nodo-forma'),
    clase && simboloEstado(clase, clase === 'simulado' ? 'oscuro' : clase === 'simulado-origen' ? 'critico' : '')));
  const linea = (clase, extra) => muestra([s('line', { class: clase, x1: -12, y1: 0, x2: 12, y2: 0 }), extra]);
  const item = (grafico, texto) => h('span', {}, grafico, texto);

  poner($('leyenda'), 
    h('span', { class: 'grupo' }, 'Elementos'),
    Object.keys(TIPO_NODO).map((tipo) => item(nodo(tipo), TIPO_NODO[tipo])),
    item(linea('arco', s('path', { class: 'sentido', d: 'M-2,-4 L3,0 L-2,4' })), 'Tramo y sentido del flujo'),
    h('span', { class: 'grupo' }, 'Estado'),
    item(nodo('bombeo', 'fuera_servicio'), 'Fuera de servicio'),
    item(nodo('bombeo', 'sin_suministro'), 'Sin suministro'),
    item(linea('arco sin_suministro'), 'Tramo sin flujo'),
    item(nodo('bombeo', 'simulado-origen'), 'Falla simulada'),
    item(nodo('bombeo', 'simulado'), 'Perdería el suministro (simulación)'),
    item(linea('arco-ruta'), 'Ruta de suministro'),
  );
}

// ---------------------------------------------------------------- Pestañas

function conectarPestanas() {
  for (const nombre of PESTANAS) {
    const boton = $(`tab-${nombre}`);
    boton.addEventListener('click', () => abrirPestana(nombre));
    boton.addEventListener('keydown', (evento) => {
      const paso = { ArrowRight: 1, ArrowLeft: -1 }[evento.key];
      if (!paso) return;
      const siguiente = PESTANAS[(PESTANAS.indexOf(nombre) + paso + PESTANAS.length) % PESTANAS.length];
      abrirPestana(siguiente);
      $(`tab-${siguiente}`).focus();
    });
  }
}

function abrirPestana(nombre) {
  for (const otra of PESTANAS) {
    $(`tab-${otra}`).setAttribute('aria-selected', String(otra === nombre));
    $(`tab-${otra}`).tabIndex = otra === nombre ? 0 : -1;
    $(`vista-${otra}`).hidden = otra !== nombre;
  }
  if (nombre === 'criticidad') intentar(pintarCriticidad);
  if (nombre === 'estadisticas') intentar(pintarEstadisticas);
}

// ----------------------------------------------------------- Averías activas

function pintarAverias() {
  const vista = $('vista-averias');
  const puedeEscribir = estado.usuario.puede_escribir;
  const items = estado.averias.map((averia) => h('li', {},
    h('div', { class: 'fila-titulo' },
      h('strong', {}, `${averia.codigo} · ${averia.tipo_falla_nombre}`), chipEstado(averia.estado)),
    h('div', { class: 'fila-detalle' }, `${averia.elemento_codigo} · ${averia.elemento_nombre}`),
    h('div', { class: 'fila-detalle' },
      `Severidad ${SEVERIDAD[averia.severidad]} · `,
      averia.fuera_servicio ? 'detiene el elemento' : 'el elemento sigue operando',
      ` · abierta hace ${duracion(averia.horas_abierta)}`),
    averia.descripcion && h('div', { class: 'fila-detalle' }, averia.descripcion),
    h('div', { class: 'fila-acciones' },
      h('button', { class: 'boton secundario chico', onclick: () => seleccionar(averia.elemento_tipo, averia.elemento_id) }, 'Ver elemento'),
      puedeEscribir && averia.estado === 'abierta' && h('button', {
        class: 'boton secundario chico', onclick: () => intentar(() => cambiarEstado(averia.id, 'en_reparacion')),
      }, 'Pasar a reparación'),
      puedeEscribir && h('button', {
        class: 'boton chico', onclick: () => intentar(() => cambiarEstado(averia.id, 'cerrada')),
      }, 'Cerrar avería'))));

  const cerradas = h('div', {});
  const historial = h('details', { class: 'plegable' }, h('summary', {}, 'Ver las últimas averías cerradas'), cerradas);
  historial.addEventListener('toggle', () => intentar(async () => {
    if (!historial.open) return;
    const filas = await estado.backend.averias('cerrada', 10);
    poner(cerradas, tabla([
      { titulo: 'Avería', fijo: true, valor: (f) => f.codigo },
      { titulo: 'Elemento', valor: (f) => f.elemento_codigo },
      { titulo: 'Falla', valor: (f) => f.tipo_falla_nombre },
      { titulo: 'Cerrada', fijo: true, valor: (f) => fechaDe(f.cerrada_en) },
      { titulo: 'Duración', num: true, valor: (f) => duracion(f.horas_abierta) },
    ], filas, (fila) => seleccionar(fila.elemento_tipo, fila.elemento_id)));
  }));

  poner(vista, 
    items.length ? h('ul', { class: 'lista' }, items) : h('p', { class: 'vacio' }, 'No hay averías abiertas.'),
    historial,
    verSql('averias_listar'));
}

async function cambiarEstado(id, nuevo) {
  await estado.backend.cambiarEstado(id, nuevo);
  estado.simulacion = null;
  estado.ruta = null;
  avisar('');
  await cargar();
}

// ------------------------------------------------------------------ Elemento

async function seleccionar(tipo, id) {
  const mismo = estado.seleccion && estado.seleccion.tipo === tipo && estado.seleccion.id === id;
  estado.seleccion = { tipo, id };
  if (!mismo) {
    estado.simulacion = null;
    estado.ruta = null;
    estado.formulario = false;
  }
  abrirPestana('elemento');
  redibujar();
  await intentar(pintarElemento);
}

async function pintarElemento() {
  const vista = $('vista-elemento');
  if (!estado.seleccion) {
    poner(vista, h('p', { class: 'vacio' }, 'Selecciona un nodo o un tramo en el esquema para ver su detalle.'));
    return;
  }
  const { tipo, id } = estado.seleccion;
  const elemento = buscar(tipo, id);
  const esNodo = tipo === 'nodo';
  const historial = await estado.backend.averiasDeElemento(tipo, id);
  if (!estado.seleccion || estado.seleccion.tipo !== tipo || estado.seleccion.id !== id) return;

  const ficha = esNodo
    ? [['Tipo', TIPO_NODO[elemento.tipo]],
       elemento.tipo === 'sector' && ['Clientes', numero.format(elemento.clientes)],
       ['En servicio desde', elemento.anio_servicio],
       elemento.id_externo && ['Equipo en el ERP', elemento.id_externo]]
    : [['Tipo', TIPO_ARCO[elemento.tipo]],
       ['Recorrido', elemento.nombre.replace(' → ', elemento.bidireccional ? ' ↔ ' : ' → ')],
       ['Sentido', elemento.bidireccional ? 'Bidireccional' : 'Un solo sentido'],
       ['Longitud', kilometros(elemento.longitud_m)],
       ['Diámetro', `${elemento.diametro_mm} mm`],
       ['Material', elemento.material],
       ['Instalación', elemento.anio_instalacion],
       elemento.id_externo && ['Equipo en el ERP', elemento.id_externo]];

  const detenido = elemento.estado === 'fuera_servicio';
  const acciones = h('div', { class: 'acciones' },
    h('button', {
      class: 'boton secundario', disabled: detenido,
      title: detenido ? 'El elemento ya está fuera de servicio' : null,
      onclick: () => intentar(simular),
    }, 'Simular su falla'),
    esNodo && h('button', { class: 'boton secundario', onclick: () => intentar(verRuta) }, 'Ver ruta de suministro'),
    estado.usuario.puede_escribir && h('button', {
      class: 'boton', onclick: () => { estado.formulario = !estado.formulario; pintarElemento(); },
    }, 'Registrar avería'));

  poner(vista, 
    h('div', { class: 'fila-titulo' }, h('h2', {}, `${elemento.codigo} · ${esNodo ? elemento.nombre : TIPO_ARCO[elemento.tipo]}`),
      chipEstado(elemento.estado)),
    h('dl', { class: 'ficha' }, ficha.filter(Boolean).map(([rotulo, valor]) => [h('dt', {}, rotulo), h('dd', {}, valor)])),
    acciones,
    !estado.usuario.puede_escribir && h('p', { class: 'ayuda' }, 'Tu usuario tiene permiso de lectura: no puede registrar averías.'),
    estado.formulario && formularioAveria(tipo, elemento),
    resultadoSimulacion(elemento),
    resultadoRuta(elemento),
    h('div', { class: 'seccion' },
      h('h3', {}, 'Historial de averías'),
      historial.length
        ? tabla([
            { titulo: 'Avería', fijo: true, valor: (f) => f.codigo },
            { titulo: 'Falla', valor: (f) => f.tipo_falla_nombre },
            { titulo: 'Estado', valor: (f) => chipEstado(f.estado) },
            { titulo: 'Reportada', fijo: true, valor: (f) => fechaDe(f.reportada_en) },
            { titulo: 'Duración', num: true, valor: (f) => duracion(f.horas_abierta) },
          ], historial)
        : h('p', { class: 'ayuda' }, 'Este elemento no registra averías.')),
    verSql('averias_por_elemento'));
}

function formularioAveria(tipo, elemento) {
  const tipos = estado.tiposFalla.filter((falla) => falla.aplica_a === tipo);
  const error = h('p', { class: 'error-formulario', role: 'alert' });
  const falla = h('select', { name: 'tipo_falla' }, tipos.map((t) => h('option', { value: t.codigo }, t.nombre)));
  const severidad = h('select', { name: 'severidad' },
    h('option', { value: 'baja' }, 'Baja'), h('option', { value: 'media', selected: true }, 'Media'), h('option', { value: 'alta' }, 'Alta'));
  const detiene = h('input', { type: 'checkbox', name: 'fuera_servicio' });
  const descripcion = h('textarea', { name: 'descripcion', maxlength: 500, placeholder: 'Qué se observó y qué se está haciendo' });

  return h('form', {
    class: 'formulario',
    onsubmit: async (evento) => {
      evento.preventDefault();
      try {
        await estado.backend.crearAveria({
          elemento_tipo: tipo, elemento_id: elemento.id, tipo_falla: falla.value,
          severidad: severidad.value, fuera_servicio: detiene.checked, descripcion: descripcion.value,
        });
        estado.formulario = false;
        estado.simulacion = null;
        estado.ruta = null;
        avisar('');
        await cargar();
      } catch (fallo) {
        error.textContent = fallo.message;
      }
    },
  },
    h('h3', {}, `Nueva avería en ${elemento.codigo}`),
    h('div', { class: 'dos' }, h('label', {}, 'Tipo de falla', falla), h('label', {}, 'Severidad', severidad)),
    h('label', { class: 'casilla' }, detiene, 'Deja el elemento fuera de servicio'),
    h('label', {}, 'Descripción', descripcion),
    error,
    h('div', { class: 'fila-acciones' },
      h('button', { class: 'boton', type: 'submit' }, 'Guardar avería'),
      h('button', { class: 'boton secundario', type: 'button', onclick: () => { estado.formulario = false; pintarElemento(); } }, 'Cancelar')));
}

async function simular() {
  const { tipo, id } = estado.seleccion;
  const filas = await estado.backend.simulacion(tipo, id);
  estado.simulacion = { tipo, id, filas, nodos: new Set(filas.map((fila) => fila.id)) };
  estado.ruta = null;
  redibujar();
  await pintarElemento();
}

function resultadoSimulacion(elemento) {
  const simulacion = estado.simulacion;
  if (!simulacion || simulacion.id !== elemento.id || simulacion.tipo !== estado.seleccion.tipo) return null;
  const nuevos = simulacion.filas.filter((fila) => fila.tipo === 'sector' && fila.nuevo);
  const clientes = nuevos.reduce((suma, fila) => suma + fila.clientes, 0);
  const total = estado.red.resumen.clientes_totales;
  const detenidos = estado.red.resumen.elementos_fuera_servicio;
  return h('div', { class: 'resultado' },
    h('h3', {}, `Si ${elemento.codigo} quedara fuera de servicio`),
    detenidos > 0 && h('p', { class: 'fila-detalle' },
      `Se suma a ${plural(detenidos, 'elemento que ya está detenido', 'elementos que ya están detenidos')} por averías abiertas.`),
    nuevos.length
      ? [h('p', {}, chipEstado('simulado', `${plural(nuevos.length, 'sector perdería', 'sectores perderían')} el suministro: `
          + `${numero.format(clientes)} clientes (${porcentaje(clientes, total)})`)),
         h('ul', { class: 'lista' }, nuevos.map((fila) => h('li', {},
           h('div', { class: 'fila-titulo' }, h('span', {}, `${fila.codigo} · ${fila.nombre}`),
             h('span', {}, plural(fila.clientes, 'cliente', 'clientes'))))))]
      : h('p', {}, chipEstado('operativo', 'Ningún sector perdería el suministro: la red tiene una ruta alternativa.')),
    verSql('impacto_simulado'));
}

async function verRuta() {
  const { id } = estado.seleccion;
  const datos = await estado.backend.rutaSuministro(id);
  const ids = (texto) => texto.split('/').filter(Boolean).map(Number);
  estado.ruta = { nodoId: id, datos, nodos: datos ? ids(datos.ruta_nodos) : [], arcos: datos ? ids(datos.ruta_arcos) : [] };
  estado.simulacion = null;
  redibujar();
  await pintarElemento();
}

function resultadoRuta(elemento) {
  const ruta = estado.ruta;
  if (!ruta || estado.seleccion.tipo !== 'nodo' || ruta.nodoId !== elemento.id) return null;
  if (!ruta.datos) {
    return h('div', { class: 'resultado' }, h('h3', {}, 'Ruta de suministro'),
      h('p', {}, chipEstado('sin_suministro', 'Hoy no llega agua a este nodo por ninguna ruta.')), verSql('ruta_suministro'));
  }
  const codigos = ruta.nodos.map((id) => buscar('nodo', id).codigo);
  return h('div', { class: 'resultado' },
    h('h3', {}, 'Ruta de suministro más corta'),
    h('p', {}, `${plural(ruta.datos.saltos, 'tramo', 'tramos')}, ${kilometros(ruta.datos.longitud_m)} desde ${codigos[0]}.`),
    h('p', { class: 'fila-detalle' }, codigos.join(' → ')),
    verSql('ruta_suministro'));
}

// ---------------------------------------------------------------- Criticidad

async function pintarCriticidad() {
  const vista = $('vista-criticidad');
  if (!estado.criticidad) {
    poner(vista, h('p', { class: 'vacio' }, 'Calculando…'));
    estado.criticidad = await estado.backend.criticidad();
  }
  const filas = estado.criticidad.filter((fila) => fila.clientes_afectados > 0);
  const cubiertos = estado.criticidad.length - filas.length;
  const maximo = Math.max(...filas.map((fila) => fila.clientes_afectados), 1);
  poner(vista, 
    h('p', { class: 'ayuda' }, 'Análisis N-1: clientes que quedarían sin suministro si cada elemento fallara por sí solo. '
      + 'Elige una fila para ver el efecto en el esquema.'),
    tabla([
      { titulo: 'N.º', num: true, valor: (f) => f.posicion },
      { titulo: 'Elemento', valor: (f) => [h('strong', {}, f.codigo), h('div', { class: 'fila-detalle' }, f.nombre)] },
      { titulo: 'Sectores', num: true, valor: (f) => f.sectores_afectados },
      { titulo: 'Clientes afectados', valor: (f) => h('div', { class: 'barra-valor' },
          h('i', { style: `width:${Math.round((90 * f.clientes_afectados) / maximo)}px` }),
          h('span', {}, `${numero.format(f.clientes_afectados)} (${decimal.format(f.pct_clientes)} %)`)) },
    ], filas, async (fila) => {
      await seleccionar(fila.elemento_tipo, fila.elemento_id);
      if (buscar(fila.elemento_tipo, fila.elemento_id).estado !== 'fuera_servicio') await intentar(simular);
    }),
    h('p', { class: 'ayuda' }, `Otros ${cubiertos} elementos no dejarían a nadie sin suministro: la red los cubre con rutas alternativas.`),
    verSql('criticidad'));
}

// -------------------------------------------------------------- Estadísticas

async function pintarEstadisticas() {
  const vista = $('vista-estadisticas');
  if (!estado.estadisticas) {
    estado.estadisticas = await estado.backend.estadisticas();
  }
  const { mensual, elementos } = estado.estadisticas;
  const fechaMes = (mes) => new Date(`${mes}-01T00:00:00Z`);
  poner(vista, 
    h('h3', {}, 'Averías registradas por mes'),
    h('p', { class: 'ayuda' }, 'Últimos 12 meses.'),
    graficoMensual(mensual, fechaMes),
    h('details', { class: 'plegable' }, h('summary', {}, 'Ver como tabla'), tabla([
      { titulo: 'Mes', valor: (f) => mesLargo.format(fechaMes(f.mes)) },
      { titulo: 'Registradas', num: true, valor: (f) => f.registradas },
      { titulo: 'Cerradas', num: true, valor: (f) => f.cerradas },
      { titulo: 'Con corte', num: true, valor: (f) => f.con_corte },
      { titulo: 'Reparación promedio', num: true, valor: (f) => (f.horas_reparacion == null ? '–' : duracion(f.horas_reparacion)) },
    ], mensual)),
    verSql('estadisticas_mensual'),
    h('div', { class: 'seccion' },
      h('h3', {}, 'Elementos con más averías'),
      h('p', { class: 'ayuda' }, 'Reincidencia en los últimos 12 meses.'),
      tabla([
        { titulo: 'N.º', num: true, valor: (f) => f.posicion },
        { titulo: 'Elemento', valor: (f) => [h('strong', {}, f.elemento_codigo), h('div', { class: 'fila-detalle' }, f.elemento_nombre)] },
        { titulo: 'Averías', num: true, valor: (f) => f.averias },
        { titulo: 'Con corte', num: true, valor: (f) => f.con_corte },
        { titulo: 'Reparación promedio', num: true, valor: (f) => (f.horas_reparacion == null ? '–' : duracion(f.horas_reparacion)) },
      ], elementos, (fila) => seleccionar(fila.elemento_tipo, fila.elemento_id))),
    verSql('estadisticas_elementos'));
}

/** Columnas de una sola serie: un color, extremo redondeado y base recta. */
function graficoMensual(filas, fechaMes) {
  const ancho = 560;
  const alto = 200;
  const margen = { arriba: 18, derecha: 8, abajo: 36, izquierda: 26 };
  const interiorAncho = ancho - margen.izquierda - margen.derecha;
  const interiorAlto = alto - margen.arriba - margen.abajo;
  const maximo = Math.max(...filas.map((fila) => fila.registradas), 1);
  const paso = [1, 2, 5, 10, 20, 50].find((candidato) => maximo / candidato <= 4) || 100;
  const tope = Math.ceil(maximo / paso) * paso;
  const y = (valor) => margen.arriba + interiorAlto - (valor / tope) * interiorAlto;
  const banda = interiorAncho / Math.max(filas.length, 1);
  const grosor = Math.min(24, banda * 0.62);
  const base = margen.arriba + interiorAlto;

  const svg = s('svg', { viewBox: `0 0 ${ancho} ${alto}`, role: 'img',
    'aria-label': 'Columnas con la cantidad de averías registradas en cada uno de los últimos 12 meses' });
  for (let marca = 0; marca <= tope; marca += paso) {
    svg.append(s('line', { class: marca === 0 ? 'base' : 'cuadricula', x1: margen.izquierda, x2: ancho - margen.derecha, y1: y(marca), y2: y(marca) }),
      s('text', { x: margen.izquierda - 6, y: y(marca) + 4, 'text-anchor': 'end' }, marca));
  }

  const zonas = s('g');
  const indiceMaximo = filas.findIndex((fila) => fila.registradas === maximo);
  filas.forEach((fila, indice) => {
    const centro = margen.izquierda + banda * (indice + 0.5);
    const izquierda = centro - grosor / 2;
    const arriba = y(fila.registradas);
    const radio = Math.min(4, base - arriba);
    const fecha = fechaMes(fila.mes);
    const columna = s('path', { class: 'columna', d: fila.registradas
      ? `M${izquierda},${base} V${arriba + radio} Q${izquierda},${arriba} ${izquierda + radio},${arriba} `
        + `H${izquierda + grosor - radio} Q${izquierda + grosor},${arriba} ${izquierda + grosor},${arriba + radio} V${base} Z`
      : '' });
    const detalle = [
      mesLargo.format(fecha),
      `${plural(fila.cerradas, 'cerrada', 'cerradas')} · ${fila.con_corte} con corte de servicio`,
      fila.horas_reparacion != null && `Reparación promedio: ${duracion(fila.horas_reparacion)}`,
    ];
    const titulo = plural(fila.registradas, 'avería', 'averías');
    const zona = s('rect', { class: 'columna-zona', x: centro - banda / 2, y: margen.arriba, width: banda, height: interiorAlto,
      tabindex: 0, role: 'img', 'aria-label': `${mesLargo.format(fecha)}: ${titulo}` });
    const soltar = () => { columna.classList.remove('activa'); ocultarTooltip(); };
    zona.addEventListener('pointermove', (evento) => {
      columna.classList.add('activa');
      mostrarTooltip(evento.clientX, evento.clientY, titulo, detalle);
    });
    zona.addEventListener('pointerleave', soltar);
    zona.addEventListener('focus', () => {
      const caja = zona.getBoundingClientRect();
      columna.classList.add('activa');
      mostrarTooltip(caja.left + caja.width / 2, caja.top + caja.height / 2, titulo, detalle);
    });
    zona.addEventListener('blur', soltar);
    svg.append(columna);
    zonas.append(zona);           // las zonas de puntero van encima de todo el dibujo

    // Solo se rotulan el máximo y el último mes; el resto está en el tooltip y en la tabla.
    if (indice === indiceMaximo || indice === filas.length - 1) {
      svg.append(s('text', { class: 'valor', x: centro, y: arriba - 5 }, fila.registradas));
    }
    svg.append(s('text', { x: centro, y: base + 14, 'text-anchor': 'middle' }, mesCorto.format(fecha).replace('.', '')));
    if (indice === 0 || fecha.getUTCMonth() === 0) {
      svg.append(s('text', { x: centro, y: base + 28, 'text-anchor': 'middle' }, fecha.getUTCFullYear()));
    }
  });
  svg.append(zonas);
  return h('div', { class: 'grafico' }, svg);
}

// ----------------------------------------------------------- Red como tabla

function pintarTablaRed() {
  const estadoDe = (fila) => chipEstado(fila.estado);
  poner($('tabla-red'), 
    h('h3', { style: 'margin:12px 0 4px' }, 'Nodos'),
    tabla([
      { titulo: 'Código', valor: (f) => f.codigo },
      { titulo: 'Nombre', valor: (f) => f.nombre },
      { titulo: 'Tipo', valor: (f) => TIPO_NODO[f.tipo] },
      { titulo: 'Clientes', num: true, valor: (f) => (f.clientes ? numero.format(f.clientes) : '') },
      { titulo: 'Estado', valor: estadoDe },
    ], estado.red.nodos, (fila) => seleccionar('nodo', fila.id)),
    h('h3', { style: 'margin:16px 0 4px' }, 'Tramos'),
    tabla([
      { titulo: 'Código', valor: (f) => f.codigo },
      { titulo: 'Recorrido', valor: (f) => f.nombre.replace(' → ', f.bidireccional ? ' ↔ ' : ' → ') },
      { titulo: 'Tipo', valor: (f) => TIPO_ARCO[f.tipo] },
      { titulo: 'Longitud', num: true, valor: (f) => kilometros(f.longitud_m) },
      { titulo: 'Estado', valor: estadoDe },
    ], estado.red.arcos, (fila) => seleccionar('arco', fila.id)));
}

iniciar();
