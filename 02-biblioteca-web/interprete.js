// Intérprete local de consultas: convierte una frase en filtros usando reglas.
//
// Es el respaldo de la búsqueda asistida por IA. Cuando la página se sirve sin
// servidor (por ejemplo en GitHub Pages) no hay dónde guardar una credencial de
// la API, así que las consultas se interpretan aquí, con sinónimos y expresiones
// regulares. Con el servidor de este proyecto y credenciales de Anthropic, la
// interpretación la hace Claude y este módulo solo actúa si esa llamada falla.

export const sinAcentos = (texto) => texto.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');

const DISCIPLINAS = {
  ELE: ['electric'], MEC: ['mecanic'], CIV: ['civil', 'civiles'], EST: ['estructur'],
  HID: ['hidraulic', 'tuberia', 'piping'], INS: ['instrument'], ARQ: ['arquitect'], TOP: ['topograf'],
};
const TIPOS_PLANO = [
  ['PG', ['planta general', 'plantas generales', 'emplazamiento', 'disposicion general']],
  ['DU', ['unilineal', 'unilineales']],
  ['PI', ['p&id', 'pid', 'tuberias e instrumentacion']],
  ['IS', ['isometrico', 'isometricos', 'isometrica']],
  ['EC', ['esquema de control', 'esquemas de control', 'lazo', 'lazos', 'conexionado']],
  ['DE', ['detalle', 'detalles']],
  ['CE', ['corte', 'cortes', 'elevacion', 'elevaciones', 'perfil', 'perfiles']],
  ['PL', ['planta', 'plantas']],
];
const TIPOS_DOCUMENTO = [
  ['ET', ['especificacion', 'especificaciones']], ['MC', ['memoria', 'memorias']], ['INF', ['informe', 'informes']],
  ['MAN', ['manual', 'manuales']], ['PRO', ['procedimiento', 'procedimientos']], ['ACT', ['acta', 'actas']],
];
const FORMATOS = ['pdf', 'dwg', 'dxf', 'docx', 'xlsx', 'doc', 'xls'];
const VACIAS = new Set(['de', 'del', 'la', 'las', 'el', 'los', 'en', 'y', 'o', 'a', 'con', 'para', 'por', 'que', 'un', 'una',
  'unos', 'unas', 'al', 'plano', 'planos', 'documento', 'documentos', 'archivo', 'archivos', 'vigente', 'vigentes',
  'busca', 'buscar', 'muestra', 'mostrar', 'dame', 'quiero', 'necesito', 'ver', 'todos', 'todas', 'sobre', 'tipo',
  'ano', 'anos', 'proyecto', 'proyectos', 'formato', 'tecnica', 'tecnicas', 'calculo', 'reunion', 'recepcion']);

const frontera = (expresion) => new RegExp(`(?<![a-z0-9&])${expresion}(?![a-z0-9])`, 'g');
const escapar = (texto) => texto.replace(/[.*+?^${}()|[\]\\&]/g, '\\$&');

/**
 * @param {string} consulta
 * @param {{proyectos: object[], instalaciones: object[]}} catalogo
 * @returns {object} filtros con la misma forma que devuelve el servidor
 */
export function interpretar(consulta, catalogo) {
  let texto = ` ${sinAcentos(consulta)} `;
  const filtros = { clase: null, disciplinas: [], tipos: [], proyectos: [], instalaciones: [], formatos: [],
    anio_desde: null, anio_hasta: null, texto: '' };
  const quitar = (expresion) => {
    const hubo = expresion.test(texto);
    expresion.lastIndex = 0;
    texto = texto.replace(expresion, ' ');
    return hubo;
  };

  // Años: rangos y límites.
  let rango = texto.match(/entre\s+(?:el\s+)?(\d{4})\s+y\s+(?:el\s+)?(\d{4})/);
  if (rango) {
    [filtros.anio_desde, filtros.anio_hasta] = [Number(rango[1]), Number(rango[2])].sort();
    texto = texto.replace(rango[0], ' ');
  }
  rango = texto.match(/(?:desde|despues de|posterior(?:es)? a|a partir de)\s+(?:el\s+)?(\d{4})/);
  if (rango) { filtros.anio_desde = Number(rango[1]); texto = texto.replace(rango[0], ' '); }
  rango = texto.match(/(?:hasta|antes de|anterior(?:es)? a)\s+(?:el\s+)?(\d{4})/);
  if (rango) { filtros.anio_hasta = Number(rango[1]); texto = texto.replace(rango[0], ' '); }
  rango = texto.match(/(?:de|del|en|ano)\s+(19\d{2}|20\d{2})(?!\d)/);
  if (rango && filtros.anio_desde === null && filtros.anio_hasta === null) {
    filtros.anio_desde = filtros.anio_hasta = Number(rango[1]);
    texto = texto.replace(rango[0], ' ');
  }

  // Proyectos e instalaciones, por código o por nombre. Van antes que los tipos de
  // plano porque sus nombres contienen palabras como "planta".
  for (const proyecto of catalogo.proyectos) {
    const porCodigo = quitar(frontera(escapar(sinAcentos(proyecto.codigo)).replace('\\-', '[- ]?')));
    const porNombre = quitar(frontera(escapar(sinAcentos(proyecto.nombre))));
    if (porCodigo || porNombre) filtros.proyectos.push(proyecto.codigo);
  }
  const porLargo = [...catalogo.instalaciones].sort((a, b) => b.nombre.length - a.nombre.length);
  for (const instalacion of porLargo) {
    const porCodigo = quitar(frontera(escapar(sinAcentos(instalacion.codigo))));
    const porNombre = quitar(frontera(escapar(sinAcentos(instalacion.nombre))));
    if (porCodigo || porNombre) filtros.instalaciones.push(instalacion.codigo);
  }

  for (const [codigo, palabras] of TIPOS_DOCUMENTO) {
    if (palabras.some((palabra) => quitar(frontera(escapar(palabra))))) {
      filtros.tipos.push(codigo);
      filtros.clase = 'documento';
    }
  }
  if (filtros.clase === null) {
    for (const [codigo, palabras] of TIPOS_PLANO) {
      if (palabras.some((palabra) => quitar(frontera(escapar(palabra))))) filtros.tipos.push(codigo);
    }
    if (/\bdocumentos?\b/.test(texto)) filtros.clase = 'documento';
    else if (filtros.tipos.length || /\bplanos?\b/.test(texto)) filtros.clase = 'plano';
  }

  for (const [codigo, raices] of Object.entries(DISCIPLINAS)) {
    if (raices.some((raiz) => quitar(new RegExp(`(?<![a-z])${raiz}[a-z]*`, 'g')))) filtros.disciplinas.push(codigo);
  }
  for (const formato of FORMATOS) {
    if (quitar(frontera(formato))) filtros.formatos.push(formato);
  }

  filtros.texto = texto.split(/[^a-z0-9&-]+/).filter((palabra) => palabra && !VACIAS.has(palabra)).join(' ');
  return filtros;
}
