-- Análisis N-1: clientes que quedarían sin suministro si cada elemento fallara
-- por sí solo, sobre la red completa (sin considerar las averías abiertas).
-- Una sola consulta recursiva evalúa todos los escenarios a la vez: la recursión
-- arrastra el escenario (elemento excluido) junto con cada nodo alcanzado.
WITH RECURSIVE
escenario (tipo, id) AS (
    SELECT 'arco', id FROM arco
    UNION ALL
    SELECT 'nodo', id FROM nodo WHERE tipo <> 'sector'
),
alcanzable (esc_tipo, esc_id, nodo_id) AS (
    SELECT e.tipo, e.id, n.id
    FROM escenario e
    JOIN nodo n ON n.tipo = 'captacion'
    WHERE NOT (e.tipo = 'nodo' AND e.id = n.id)
    UNION
    SELECT a.esc_tipo, a.esc_id, d.destino_id
    FROM alcanzable a
    JOIN v_arco_dirigido d ON d.origen_id = a.nodo_id
    WHERE NOT (a.esc_tipo = 'arco' AND a.esc_id = d.arco_id)
      AND NOT (a.esc_tipo = 'nodo' AND a.esc_id = d.destino_id)
),
afectado AS (
    SELECT e.tipo, e.id, s.id AS sector_id, s.clientes
    FROM escenario e
    JOIN nodo s ON s.tipo = 'sector'
    LEFT JOIN alcanzable a
           ON a.esc_tipo = e.tipo AND a.esc_id = e.id AND a.nodo_id = s.id
    WHERE a.nodo_id IS NULL
)
SELECT e.tipo                                              AS elemento_tipo,
       e.id                                                AS elemento_id,
       COALESCE(n.codigo, ar.codigo)                       AS codigo,
       COALESCE(n.nombre, o.nombre || ' → ' || d.nombre)   AS nombre,
       COUNT(f.sector_id)                                  AS sectores_afectados,
       COALESCE(SUM(f.clientes), 0)                        AS clientes_afectados,
       ROUND(100.0 * COALESCE(SUM(f.clientes), 0)
             / (SELECT SUM(clientes) FROM nodo), 1)        AS pct_clientes,
       DENSE_RANK() OVER (ORDER BY COALESCE(SUM(f.clientes), 0) DESC) AS posicion
FROM escenario e
LEFT JOIN afectado f ON f.tipo = e.tipo AND f.id = e.id
LEFT JOIN nodo n     ON e.tipo = 'nodo' AND n.id = e.id
LEFT JOIN arco ar    ON e.tipo = 'arco' AND ar.id = e.id
LEFT JOIN nodo o     ON o.id = ar.origen_id
LEFT JOIN nodo d     ON d.id = ar.destino_id
GROUP BY e.tipo, e.id, n.codigo, ar.codigo, n.nombre, o.nombre, d.nombre
ORDER BY clientes_afectados DESC, codigo;
