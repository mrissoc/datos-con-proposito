// Capa de datos de la aplicación.
//
// Hay dos formas de obtener los datos y ambas ofrecen las mismas operaciones:
//
//  - BackendApi: la aplicación está servida por la API (python -m averias) y
//    cada operación es una llamada HTTP.
//  - BackendNavegador: no hay servidor (por ejemplo, en GitHub Pages). La base
//    SQLite de demostración se abre dentro del navegador con sql.js y se
//    ejecutan sobre ella los mismos archivos .sql que usa la API.

const BASE_API = '../api/';
const BASE_SQL = '../sql/consultas/';
const RUTA_BD_DEMO = '../datos/averias_demo.db';
const SQLJS = 'https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.13.0/';

const cacheSql = new Map();

/** Texto de una consulta con nombre. También lo usa la vista "Ver consulta SQL". */
export async function textoSql(nombre) {
  if (!cacheSql.has(nombre)) {
    const respuesta = await fetch(`${BASE_SQL}${nombre}.sql`);
    if (!respuesta.ok) throw new Error(`No se pudo leer la consulta ${nombre}.sql`);
    cacheSql.set(nombre, await respuesta.text());
  }
  return cacheSql.get(nombre);
}

/** Elige el backend: la API si responde; si no, la base dentro del navegador. */
export async function crearBackend() {
  // En GitHub Pages nunca hay API: se va directo a la base en el navegador.
  if (location.hostname.endsWith('.github.io')) return BackendNavegador.crear();
  let salud = null;
  try {
    const respuesta = await fetch(`${BASE_API}salud`, { cache: 'no-store' });
    const tipo = respuesta.headers.get('content-type') || '';
    if (respuesta.ok && tipo.includes('json')) salud = await respuesta.json();
  } catch {
    // Sin servidor: se sigue con la base en el navegador.
  }
  if (salud && salud.estado === 'ok') {
    let obtenerToken = null;
    if (salud.auth === 'entra') {
      const { iniciarSesion } = await import('./auth.js');
      obtenerToken = await iniciarSesion(salud.entra);
    }
    return new BackendApi(obtenerToken);
  }
  return BackendNavegador.crear();
}

// ------------------------------------------------------------------ API

class BackendApi {
  constructor(obtenerToken) {
    this.modo = 'api';
    this.obtenerToken = obtenerToken;
  }

  async pedir(ruta, cuerpo) {
    const opciones = { headers: {} };
    if (cuerpo !== undefined) {
      opciones.method = 'POST';
      opciones.headers['Content-Type'] = 'application/json';
      opciones.body = JSON.stringify(cuerpo);
    }
    if (this.obtenerToken) opciones.headers.Authorization = `Bearer ${await this.obtenerToken()}`;
    const respuesta = await fetch(BASE_API + ruta, opciones);
    if (!respuesta.ok) throw new Error(await mensajeDeError(respuesta));
    return respuesta.json();
  }

  usuario() { return this.pedir('yo'); }
  red() { return this.pedir('red'); }
  tiposFalla() { return this.pedir('tipos-falla'); }
  averias(estado, limite = 100) { return this.pedir(`averias?estado=${estado}&limite=${limite}`); }
  averiasDeElemento(tipo, id) { return this.pedir(`elementos/${tipo}/${id}/averias`); }
  crearAveria(datos) { return this.pedir('averias', datos); }
  cambiarEstado(id, estado) { return this.pedir(`averias/${id}/estado`, { estado }); }
  simulacion(tipo, id) { return this.pedir(`simulacion?elemento_tipo=${tipo}&elemento_id=${id}`); }
  criticidad() { return this.pedir('criticidad'); }
  rutaSuministro(nodoId) { return this.pedir(`ruta-suministro/${nodoId}`); }
  estadisticas() { return this.pedir('estadisticas'); }
}

async function mensajeDeError(respuesta) {
  try {
    const { detail: detalle } = await respuesta.json();
    if (typeof detalle === 'string') return detalle;
    if (Array.isArray(detalle)) return detalle.map((d) => d.msg).join('. ');
  } catch {
    // La respuesta no era JSON.
  }
  return `Error ${respuesta.status} al consultar la API`;
}

// ------------------------------------------------- SQLite en el navegador

class BackendNavegador {
  constructor(bd) {
    this.modo = 'navegador';
    this.bd = bd;
  }

  static async crear() {
    await cargarScript(`${SQLJS}sql-wasm.js`);
    const SQL = await window.initSqlJs({ locateFile: (archivo) => SQLJS + archivo });
    const respuesta = await fetch(RUTA_BD_DEMO);
    if (!respuesta.ok) throw new Error('No se pudo descargar la base de demostración');
    const bd = new SQL.Database(new Uint8Array(await respuesta.arrayBuffer()));
    bd.run('PRAGMA foreign_keys = ON');
    ponerFechasAlDia(bd);
    return new BackendNavegador(bd);
  }

  /** Ejecuta un archivo .sql con parámetros con nombre y devuelve las filas. */
  async filas(nombre, parametros = {}) {
    const sentencia = this.bd.prepare(await textoSql(nombre));
    try {
      const enlazados = {};
      for (const [clave, valor] of Object.entries(parametros)) enlazados[`:${clave}`] = valor;
      sentencia.bind(enlazados);
      const filas = [];
      while (sentencia.step()) filas.push(sentencia.getAsObject());
      return filas;
    } finally {
      sentencia.free();
    }
  }

  async usuario() {
    return { nombre: 'demo@ejemplo.com', roles: [], puede_escribir: true };
  }

  async red() {
    const [nodos, arcos, resumen] = await Promise.all([
      this.filas('red_nodos'), this.filas('red_arcos'), this.filas('resumen')]);
    return { nodos, arcos, resumen: resumen[0] };
  }

  tiposFalla() { return this.filas('tipos_falla'); }
  averias(estado, limite = 100) { return this.filas('averias_listar', { estado, limite }); }

  averiasDeElemento(tipo, id) {
    return this.filas('averias_por_elemento', { elemento_tipo: tipo, elemento_id: id });
  }

  async crearAveria(datos) {
    await this.filas('averia_crear', {
      elemento_tipo: datos.elemento_tipo,
      elemento_id: datos.elemento_id,
      tipo_falla: datos.tipo_falla,
      severidad: datos.severidad,
      fuera_servicio: datos.fuera_servicio ? 1 : 0,
      descripcion: (datos.descripcion || '').trim(),
      usuario: 'demo@ejemplo.com',
    });
    const id = this.bd.exec('SELECT last_insert_rowid()')[0].values[0][0];
    return (await this.filas('averia_por_id', { id }))[0];
  }

  async cambiarEstado(id, estado) {
    await this.filas('averia_cambiar_estado', { id, estado, usuario: 'demo@ejemplo.com' });
    return (await this.filas('averia_por_id', { id }))[0];
  }

  simulacion(tipo, id) {
    return this.filas('impacto_simulado', { elemento_tipo: tipo, elemento_id: id });
  }

  criticidad() { return this.filas('criticidad'); }

  async rutaSuministro(nodoId) {
    return (await this.filas('ruta_suministro', { nodo_id: nodoId }))[0] ?? null;
  }

  async estadisticas() {
    const [mensual, elementos] = await Promise.all([
      this.filas('estadisticas_mensual'), this.filas('estadisticas_elementos')]);
    return { mensual, elementos };
  }
}

/** La base de demostración se generó en una fecha fija. Para que no envejezca,
 *  al abrirla se desplazan todas las fechas hasta el día de hoy. */
function ponerFechasAlDia(bd) {
  const meta = bd.exec("SELECT valor FROM meta WHERE clave = 'construida_en'");
  if (!meta.length) return;
  const dias = (Date.now() - Date.parse(meta[0].values[0][0])) / 86400000;
  if (!(dias > 0.01)) return;
  const mover = (columna) => `strftime('%Y-%m-%dT%H:%M:%SZ', julianday(${columna}) + $dias)`;
  bd.run(`UPDATE averia SET reportada_en = ${mover('reportada_en')},
                 cerrada_en = CASE WHEN cerrada_en IS NOT NULL THEN ${mover('cerrada_en')} END`, { $dias: dias });
  bd.run(`UPDATE averia_evento SET ocurrido_en = ${mover('ocurrido_en')}`, { $dias: dias });
}

function cargarScript(url) {
  return new Promise((resolver, rechazar) => {
    const script = document.createElement('script');
    script.src = url;
    script.onload = resolver;
    script.onerror = () => rechazar(new Error(`No se pudo cargar ${url}`));
    document.head.append(script);
  });
}
