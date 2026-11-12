// Dibujo del esquema de la red en SVG.
//
// El tipo de nodo se distingue por la forma; el estado, por un color reservado
// más un símbolo dentro del nodo (✕, –, !), para no depender solo del color.

const NS = 'http://www.w3.org/2000/svg';
const RADIO = { captacion: 13, planta: 12, bombeo: 12, estanque: 12, union: 6, sector: 12 };

/** Crea un elemento SVG. El texto se inserta siempre como nodo de texto. */
export function s(etiqueta, atributos = {}, ...hijos) {
  const elemento = document.createElementNS(NS, etiqueta);
  for (const [clave, valor] of Object.entries(atributos)) {
    if (valor == null || valor === false) continue;
    elemento.setAttribute(clave, valor);
  }
  for (const hijo of hijos.flat(Infinity)) {
    if (hijo == null || hijo === false) continue;
    elemento.append(hijo instanceof Node ? hijo : document.createTextNode(String(hijo)));
  }
  return elemento;
}

/** Forma de un nodo centrada en el origen. */
export function formaNodo(tipo, r, clase) {
  switch (tipo) {
    case 'captacion':
      return s('polygon', { class: clase, points: `0,${-r} ${r},${r * 0.8} ${-r},${r * 0.8}`, 'stroke-linejoin': 'round' });
    case 'planta':
      return s('rect', { class: clase, x: -r, y: -r, width: 2 * r, height: 2 * r, rx: 2 });
    case 'estanque':
      return s('rect', { class: clase, x: -r - 4, y: -r + 2, width: 2 * r + 8, height: 2 * r - 4, rx: r - 2 });
    case 'sector':
      return s('polygon', { class: clase, points: `0,${-r - 1} ${r + 1},0 0,${r + 1} ${-r - 1},0`, 'stroke-linejoin': 'round' });
    default:
      return s('circle', { class: clase, r });
  }
}

/** Símbolo que acompaña a cada estado, para dibujar dentro de un nodo o una insignia. */
export function simboloEstado(estado, extra = '') {
  const clase = `simbolo ${extra}`.trim();
  if (estado === 'fuera_servicio' || estado === 'simulado-origen') {
    return s('g', { class: clase }, s('line', { x1: -3.5, y1: -3.5, x2: 3.5, y2: 3.5 }), s('line', { x1: 3.5, y1: -3.5, x2: -3.5, y2: 3.5 }));
  }
  if (estado === 'sin_suministro') return s('g', { class: clase }, s('line', { x1: -4, y1: 0, x2: 4, y2: 0 }));
  if (estado === 'simulado') {
    return s('g', { class: clase }, s('line', { x1: 0, y1: -4.5, x2: 0, y2: 1 }), s('line', { x1: 0, y1: 4.4, x2: 0, y2: 4.5 }));
  }
  return null;
}

export class Mapa {
  /**
   * @param {HTMLElement} contenedor
   * @param {{alSeleccionar: Function, alApuntar: Function, alSalir: Function}} eventos
   */
  constructor(contenedor, eventos) {
    this.contenedor = contenedor;
    this.eventos = eventos;
  }

  /**
   * @param {{nodos: object[], arcos: object[]}} red
   * @param {{seleccion?: {tipo: string, id: number}, simulacion?: {tipo: string, id: number, nodos: Set<number>},
   *          ruta?: {nodos: number[], arcos: number[]}}} resaltado
   */
  dibujar(red, resaltado = {}) {
    const enfocado = this.contenedor.contains(document.activeElement)
      ? document.activeElement.getAttribute('data-clave') : null;
    const nodos = new Map(red.nodos.map((n) => [n.id, n]));
    const vecinos = new Map(red.nodos.map((n) => [n.id, []]));
    for (const arco of red.arcos) {
      vecinos.get(arco.origen_id).push(nodos.get(arco.destino_id));
      vecinos.get(arco.destino_id).push(nodos.get(arco.origen_id));
    }
    const { seleccion, simulacion, ruta } = resaltado;
    const rutaArcos = new Set(ruta ? ruta.arcos : []);
    const rutaNodos = new Set(ruta ? ruta.nodos : []);

    const capaFondo = s('g');
    const capaArcos = s('g');
    const capaNodos = s('g');

    for (const arco of red.arcos) {
      const origen = nodos.get(arco.origen_id);
      const destino = nodos.get(arco.destino_id);
      const estado = estadoVisualArco(arco, simulacion);
      const tramo = recortar(origen, destino, radioDe(origen, simulacion), radioDe(destino, simulacion));
      const medio = { x: (tramo.x1 + tramo.x2) / 2, y: (tramo.y1 + tramo.y2) / 2 };
      const angulo = Math.atan2(tramo.y2 - tramo.y1, tramo.x2 - tramo.x1) * 180 / Math.PI;

      if (seleccion && seleccion.tipo === 'arco' && seleccion.id === arco.id) {
        capaFondo.append(s('line', { class: 'arco-seleccion', ...tramo }));
      }
      if (rutaArcos.has(arco.id)) capaFondo.append(s('line', { class: 'arco-ruta', ...tramo }));

      const grupo = s('g', {
        class: 'arco-grupo', tabindex: 0, role: 'button', 'data-clave': `arco-${arco.id}`,
        'aria-label': `Tramo ${arco.codigo}, ${arco.nombre}, ${textoEstado(estado)}`,
      });
      grupo.append(s('line', { class: `arco ${estado}`, ...tramo }));
      if (estado === 'fuera_servicio' || estado === 'simulado-origen') {
        grupo.append(s('g', { transform: `translate(${medio.x} ${medio.y})` },
          s('circle', { class: estado === 'fuera_servicio' ? 'insignia' : 'insignia simulada', r: 8 }),
          simboloEstado(estado, estado === 'simulado-origen' ? 'critico' : '')));
      } else {
        grupo.append(s('g', { transform: `translate(${medio.x} ${medio.y}) rotate(${angulo})` }, flechas(arco, estado)));
      }
      grupo.append(s('line', { class: 'zona-toque', ...tramo }));
      this.conectar(grupo, 'arco', arco);
      capaArcos.append(grupo);
    }

    for (const nodo of red.nodos) {
      const estado = estadoVisualNodo(nodo, simulacion);
      const r = radioDe(nodo, simulacion);
      const grupo = s('g', {
        class: `nodo ${nodo.tipo} ${estado}`, transform: `translate(${nodo.x} ${nodo.y})`,
        tabindex: 0, role: 'button', 'data-clave': `nodo-${nodo.id}`,
        'aria-label': `${nodo.codigo}, ${nodo.nombre}, ${textoEstado(estado)}`,
      });
      if (rutaNodos.has(nodo.id)) grupo.append(s('circle', { class: 'nodo-ruta', r: r + 7 }));
      if (seleccion && seleccion.tipo === 'nodo' && seleccion.id === nodo.id) {
        grupo.append(s('circle', { class: 'nodo-anillo', r: r + 8 }));
      }
      grupo.append(formaNodo(nodo.tipo, r, 'nodo-halo'), formaNodo(nodo.tipo, r, 'nodo-forma'));
      const simbolo = simboloEstado(estado, estado === 'simulado' ? 'oscuro' : estado === 'simulado-origen' ? 'critico' : '');
      if (simbolo) grupo.append(simbolo);
      const rotulo = posicionRotulo(nodo, vecinos.get(nodo.id), r);
      grupo.append(s('text', { class: 'rotulo', x: rotulo.x, y: rotulo.y, 'text-anchor': rotulo.ancla }, nodo.codigo));
      grupo.append(s('circle', { class: 'zona-toque', r: Math.max(r + 6, 15) }));
      this.conectar(grupo, 'nodo', nodo);
      capaNodos.append(grupo);
    }

    const svg = s('svg', { viewBox: '24 0 936 626', role: 'group', 'aria-label': 'Esquema de la red de nodos y tramos' },
      capaFondo, capaArcos, capaNodos);
    this.contenedor.replaceChildren(svg);
    if (enfocado) {
      const destino = this.contenedor.querySelector(`[data-clave="${enfocado}"]`);
      if (destino) destino.focus();
    }
  }

  conectar(grupo, tipo, elemento) {
    const { alSeleccionar, alApuntar, alSalir } = this.eventos;
    grupo.addEventListener('click', () => alSeleccionar(tipo, elemento.id));
    grupo.addEventListener('keydown', (evento) => {
      if (evento.key === 'Enter' || evento.key === ' ') {
        evento.preventDefault();
        alSeleccionar(tipo, elemento.id);
      }
    });
    grupo.addEventListener('pointermove', (evento) => alApuntar(tipo, elemento, evento.clientX, evento.clientY));
    grupo.addEventListener('pointerleave', alSalir);
    grupo.addEventListener('focus', () => {
      const caja = grupo.getBoundingClientRect();
      alApuntar(tipo, elemento, caja.left + caja.width / 2, caja.bottom);
    });
    grupo.addEventListener('blur', alSalir);
  }
}

function estadoVisualNodo(nodo, simulacion) {
  if (simulacion) {
    if (simulacion.tipo === 'nodo' && simulacion.id === nodo.id) return 'simulado-origen';
    if (simulacion.nodos.has(nodo.id) && nodo.estado === 'operativo') return 'simulado';
  }
  return nodo.estado;
}

function estadoVisualArco(arco, simulacion) {
  if (simulacion && arco.estado === 'operativo') {
    if (simulacion.tipo === 'arco' && simulacion.id === arco.id) return 'simulado-origen';
    // Un tramo deja de llevar agua cuando se quedan sin suministro los extremos que lo alimentan.
    const sinOrigen = simulacion.nodos.has(arco.origen_id);
    const sinDestino = simulacion.nodos.has(arco.destino_id);
    if (arco.bidireccional ? sinOrigen && sinDestino : sinOrigen) return 'simulado';
  }
  return arco.estado;
}

function radioDe(nodo, simulacion) {
  // Los nodos de unión son pequeños: crecen cuando deben mostrar un símbolo de estado.
  if (nodo.tipo === 'union' && estadoVisualNodo(nodo, simulacion) !== 'operativo') return 10;
  return RADIO[nodo.tipo];
}

/** Ubica el rótulo debajo del nodo si ese lado está libre; si no, en el mayor
 *  hueco angular entre los tramos que llegan al nodo. */
function posicionRotulo(nodo, adyacentes, r) {
  const angulos = adyacentes.map((otro) => Math.atan2(otro.y - nodo.y, otro.x - nodo.x)).sort((a, b) => a - b);
  const abajo = Math.PI / 2;
  const separacion = (a, b) => Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b)));
  let direccion = abajo;
  if (angulos.some((angulo) => separacion(angulo, abajo) < 0.7)) {
    let mayor = -1;
    angulos.forEach((angulo, indice) => {
      const siguiente = indice + 1 < angulos.length ? angulos[indice + 1] : angulos[0] + 2 * Math.PI;
      if (siguiente - angulo > mayor) {
        mayor = siguiente - angulo;
        direccion = angulo + mayor / 2;
      }
    });
  }
  const cos = Math.cos(direccion);
  const sin = Math.sin(direccion);
  const distancia = r + 6 + (nodo.tipo === 'estanque' ? Math.abs(cos) * 4 : 0);
  return {
    x: cos * distancia,
    y: sin * distancia + (sin > 0.4 ? 10 : sin < -0.4 ? -2 : 4.5),
    ancla: cos > 0.4 ? 'start' : cos < -0.4 ? 'end' : 'middle',
  };
}

function recortar(origen, destino, radioOrigen, radioDestino) {
  const dx = destino.x - origen.x;
  const dy = destino.y - origen.y;
  const largo = Math.hypot(dx, dy) || 1;
  const a = (radioOrigen + 5) / largo;
  const b = (radioDestino + 5) / largo;
  return { x1: origen.x + dx * a, y1: origen.y + dy * a, x2: destino.x - dx * b, y2: destino.y - dy * b };
}

function flechas(arco, estado) {
  const clase = `sentido ${estado === 'sin_suministro' ? 'sin_suministro' : ''}`.trim();
  if (arco.bidireccional) {
    return [s('path', { class: clase, d: 'M-5,-4 L-9,0 L-5,4' }), s('path', { class: clase, d: 'M5,-4 L9,0 L5,4' })];
  }
  return [s('path', { class: clase, d: 'M-2,-4 L3,0 L-2,4' })];
}

export function textoEstado(estado) {
  return {
    operativo: 'operativo',
    fuera_servicio: 'fuera de servicio',
    sin_suministro: 'sin suministro',
    simulado: 'perdería el suministro en la simulación',
    'simulado-origen': 'elemento de la simulación',
  }[estado] || estado;
}
