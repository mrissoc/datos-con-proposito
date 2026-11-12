-- ¿Qué pasa si además falla este elemento? (análisis "qué pasaría si")
-- Recalcula el suministro sumando un elemento fuera de servicio hipotético a las
-- averías abiertas, y devuelve los nodos que quedarían sin suministro.
-- La columna "nuevo" vale 1 cuando el nodo hoy sí tiene suministro y lo perdería.
WITH RECURSIVE
fuera (tipo, id) AS (
    SELECT tipo, id FROM v_fuera_servicio
    UNION
    SELECT :elemento_tipo, :elemento_id
),
alcanzable (nodo_id) AS (
    SELECT n.id
    FROM nodo n
    WHERE n.tipo = 'captacion'
      AND n.id NOT IN (SELECT id FROM fuera WHERE tipo = 'nodo')
    UNION
    SELECT d.destino_id
    FROM alcanzable a
    JOIN v_arco_dirigido d ON d.origen_id = a.nodo_id
    WHERE d.arco_id    NOT IN (SELECT id FROM fuera WHERE tipo = 'arco')
      AND d.destino_id NOT IN (SELECT id FROM fuera WHERE tipo = 'nodo')
)
SELECT n.id, n.codigo, n.nombre, n.tipo, n.clientes,
       CASE WHEN n.id IN (SELECT nodo_id FROM v_nodo_abastecido) THEN 1 ELSE 0 END AS nuevo
FROM nodo n
WHERE n.id NOT IN (SELECT nodo_id FROM alcanzable)
ORDER BY n.clientes DESC, n.codigo;
