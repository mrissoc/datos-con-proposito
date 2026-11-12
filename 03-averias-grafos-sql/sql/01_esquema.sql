-- Esquema de la red de activos como grafo (nodos y arcos) y registro de averías.
-- Escrito para SQLite; las notas de portabilidad a SQL Server y PostgreSQL
-- están en el README.

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------------ Grafo

CREATE TABLE nodo (
    id            INTEGER PRIMARY KEY,
    codigo        TEXT    NOT NULL UNIQUE,
    nombre        TEXT    NOT NULL,
    tipo          TEXT    NOT NULL
                  CHECK (tipo IN ('captacion', 'planta', 'bombeo', 'estanque', 'union', 'sector')),
    x             REAL    NOT NULL,            -- posición en el esquema de la red
    y             REAL    NOT NULL,
    clientes      INTEGER NOT NULL DEFAULT 0 CHECK (clientes >= 0),
    anio_servicio INTEGER,
    id_externo    TEXT                         -- equipo en el ERP, cuando está sincronizado
);

CREATE TABLE arco (
    id               INTEGER PRIMARY KEY,
    codigo           TEXT    NOT NULL UNIQUE,
    origen_id        INTEGER NOT NULL REFERENCES nodo (id),
    destino_id       INTEGER NOT NULL REFERENCES nodo (id),
    tipo             TEXT    NOT NULL
                     CHECK (tipo IN ('aduccion', 'conduccion', 'impulsion', 'matriz',
                                     'interconexion', 'distribucion')),
    longitud_m       REAL    NOT NULL CHECK (longitud_m > 0),
    diametro_mm      INTEGER NOT NULL CHECK (diametro_mm > 0),
    material         TEXT    NOT NULL,
    anio_instalacion INTEGER,
    bidireccional    INTEGER NOT NULL DEFAULT 0 CHECK (bidireccional IN (0, 1)),
    id_externo       TEXT,                     -- equipo en el ERP, cuando está sincronizado
    CHECK (origen_id <> destino_id)
);

CREATE INDEX ix_arco_origen  ON arco (origen_id);
CREATE INDEX ix_arco_destino ON arco (destino_id);

-- ---------------------------------------------------------------- Averías

CREATE TABLE tipo_falla (
    codigo   TEXT PRIMARY KEY,
    nombre   TEXT NOT NULL,
    aplica_a TEXT NOT NULL CHECK (aplica_a IN ('nodo', 'arco'))
);

CREATE TABLE averia (
    id              INTEGER PRIMARY KEY,
    codigo          TEXT    NOT NULL UNIQUE,
    nodo_id         INTEGER REFERENCES nodo (id),
    arco_id         INTEGER REFERENCES arco (id),
    tipo_falla      TEXT    NOT NULL REFERENCES tipo_falla (codigo),
    severidad       TEXT    NOT NULL CHECK (severidad IN ('baja', 'media', 'alta')),
    fuera_servicio  INTEGER NOT NULL DEFAULT 0 CHECK (fuera_servicio IN (0, 1)),
    estado          TEXT    NOT NULL DEFAULT 'abierta'
                    CHECK (estado IN ('abierta', 'en_reparacion', 'cerrada')),
    descripcion     TEXT    NOT NULL DEFAULT '',
    reportada_por   TEXT    NOT NULL,
    reportada_en    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    actualizada_por TEXT,
    cerrada_en      TEXT,
    id_externo      TEXT,                      -- aviso de mantenimiento en el ERP
    -- Una avería afecta a exactamente un elemento: un nodo o un arco.
    CHECK ((nodo_id IS NULL) <> (arco_id IS NULL)),
    CHECK ((estado = 'cerrada') = (cerrada_en IS NOT NULL))
);

CREATE INDEX ix_averia_nodo    ON averia (nodo_id);
CREATE INDEX ix_averia_arco    ON averia (arco_id);
CREATE INDEX ix_averia_abierta ON averia (estado) WHERE estado <> 'cerrada';

-- Bitácora: cada cambio de estado queda registrado por un trigger.
CREATE TABLE averia_evento (
    id          INTEGER PRIMARY KEY,
    averia_id   INTEGER NOT NULL REFERENCES averia (id),
    estado      TEXT    NOT NULL,
    usuario     TEXT    NOT NULL,
    ocurrido_en TEXT    NOT NULL
);

CREATE TRIGGER tr_averia_alta
AFTER INSERT ON averia
BEGIN
    INSERT INTO averia_evento (averia_id, estado, usuario, ocurrido_en)
    VALUES (NEW.id, NEW.estado, NEW.reportada_por, NEW.reportada_en);
END;

CREATE TRIGGER tr_averia_cerrada_no_cambia
BEFORE UPDATE OF estado ON averia
WHEN OLD.estado = 'cerrada'
BEGIN
    SELECT RAISE(ABORT, 'Una avería cerrada no se puede modificar');
END;

CREATE TRIGGER tr_averia_cambio_estado
AFTER UPDATE OF estado ON averia
WHEN OLD.estado <> NEW.estado
BEGIN
    INSERT INTO averia_evento (averia_id, estado, usuario, ocurrido_en)
    VALUES (NEW.id, NEW.estado, COALESCE(NEW.actualizada_por, NEW.reportada_por),
            COALESCE(NEW.cerrada_en, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')));
END;

CREATE TRIGGER tr_averia_tipo_coherente
BEFORE INSERT ON averia
WHEN (SELECT aplica_a FROM tipo_falla WHERE codigo = NEW.tipo_falla)
     <> CASE WHEN NEW.nodo_id IS NOT NULL THEN 'nodo' ELSE 'arco' END
BEGIN
    SELECT RAISE(ABORT, 'El tipo de falla no corresponde al tipo de elemento');
END;

-- Datos sobre la propia base (por ejemplo, cuándo se generó).
CREATE TABLE meta (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

-- ----------------------------------------------------------------- Vistas

-- Un arco bidireccional se guarda una sola vez y se recorre en ambos sentidos.
CREATE VIEW v_arco_dirigido AS
SELECT id AS arco_id, origen_id, destino_id, longitud_m FROM arco
UNION ALL
SELECT id, destino_id, origen_id, longitud_m FROM arco WHERE bidireccional = 1;

-- Elementos fuera de servicio: los que tienen una avería abierta que los detiene.
-- La avería es la única fuente de verdad; no hay una columna de estado que mantener.
CREATE VIEW v_fuera_servicio AS
SELECT 'nodo' AS tipo, nodo_id AS id
FROM averia
WHERE estado <> 'cerrada' AND fuera_servicio = 1 AND nodo_id IS NOT NULL
UNION
SELECT 'arco', arco_id
FROM averia
WHERE estado <> 'cerrada' AND fuera_servicio = 1 AND arco_id IS NOT NULL;

-- Nodos que hoy reciben suministro: alcanzables desde alguna captación
-- recorriendo solo arcos y nodos en servicio.
CREATE VIEW v_nodo_abastecido AS
WITH RECURSIVE alcanzable (nodo_id) AS (
    SELECT n.id
    FROM nodo n
    WHERE n.tipo = 'captacion'
      AND n.id NOT IN (SELECT id FROM v_fuera_servicio WHERE tipo = 'nodo')
    UNION
    SELECT d.destino_id
    FROM alcanzable a
    JOIN v_arco_dirigido d ON d.origen_id = a.nodo_id
    WHERE d.arco_id    NOT IN (SELECT id FROM v_fuera_servicio WHERE tipo = 'arco')
      AND d.destino_id NOT IN (SELECT id FROM v_fuera_servicio WHERE tipo = 'nodo')
)
SELECT nodo_id FROM alcanzable;

CREATE VIEW v_estado_nodo AS
SELECT n.id, n.codigo, n.nombre, n.tipo, n.x, n.y, n.clientes, n.anio_servicio, n.id_externo,
       CASE
           WHEN n.id IN (SELECT id FROM v_fuera_servicio WHERE tipo = 'nodo') THEN 'fuera_servicio'
           WHEN n.id NOT IN (SELECT nodo_id FROM v_nodo_abastecido)           THEN 'sin_suministro'
           ELSE 'operativo'
       END AS estado,
       (SELECT COUNT(*) FROM averia a
         WHERE a.nodo_id = n.id AND a.estado <> 'cerrada') AS averias_abiertas
FROM nodo n;

-- Un arco lleva agua si está en servicio y alguno de los extremos desde los
-- que puede recibirla está abastecido.
CREATE VIEW v_estado_arco AS
SELECT a.id, a.codigo, a.origen_id, a.destino_id, a.tipo, a.longitud_m, a.diametro_mm,
       a.material, a.anio_instalacion, a.bidireccional, a.id_externo,
       CASE
           WHEN a.id IN (SELECT id FROM v_fuera_servicio WHERE tipo = 'arco') THEN 'fuera_servicio'
           WHEN a.origen_id IN (SELECT nodo_id FROM v_nodo_abastecido)        THEN 'operativo'
           WHEN a.bidireccional = 1
                AND a.destino_id IN (SELECT nodo_id FROM v_nodo_abastecido)   THEN 'operativo'
           ELSE 'sin_suministro'
       END AS estado,
       (SELECT COUNT(*) FROM averia v
         WHERE v.arco_id = a.id AND v.estado <> 'cerrada') AS averias_abiertas
FROM arco a;

-- Averías con el elemento afectado ya resuelto, para listados y reportes.
CREATE VIEW v_averia AS
SELECT v.id, v.codigo, v.estado, v.severidad, v.fuera_servicio, v.descripcion,
       v.reportada_por, v.reportada_en, v.cerrada_en, v.id_externo,
       v.tipo_falla, t.nombre AS tipo_falla_nombre,
       CASE WHEN v.nodo_id IS NOT NULL THEN 'nodo' ELSE 'arco' END AS elemento_tipo,
       COALESCE(v.nodo_id, v.arco_id)                             AS elemento_id,
       COALESCE(n.codigo, a.codigo)                               AS elemento_codigo,
       COALESCE(n.nombre, no.nombre || ' → ' || nd.nombre)        AS elemento_nombre,
       ROUND((julianday(COALESCE(v.cerrada_en, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))
              - julianday(v.reportada_en)) * 24, 1)               AS horas_abierta
FROM averia v
JOIN tipo_falla t  ON t.codigo = v.tipo_falla
LEFT JOIN nodo n   ON n.id = v.nodo_id
LEFT JOIN arco a   ON a.id = v.arco_id
LEFT JOIN nodo no  ON no.id = a.origen_id
LEFT JOIN nodo nd  ON nd.id = a.destino_id;
