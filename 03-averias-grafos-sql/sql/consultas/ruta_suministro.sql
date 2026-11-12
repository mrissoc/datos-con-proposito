-- Ruta más corta por la que hoy llega el agua a un nodo, desde cualquier captación,
-- usando solo elementos en servicio. La recursión enumera caminos sin repetir
-- nodos (el camino recorrido se guarda como texto '/1/5/9/') y se queda con el
-- de menor longitud. Devuelve cero filas si el nodo no tiene suministro.
WITH RECURSIVE camino (nodo_id, longitud_m, saltos, ruta_nodos, ruta_arcos) AS (
    SELECT n.id, 0.0, 0, '/' || n.id || '/', '/'
    FROM nodo n
    WHERE n.tipo = 'captacion'
      AND n.id NOT IN (SELECT id FROM v_fuera_servicio WHERE tipo = 'nodo')
    UNION ALL
    SELECT d.destino_id,
           c.longitud_m + d.longitud_m,
           c.saltos + 1,
           c.ruta_nodos || d.destino_id || '/',
           c.ruta_arcos || d.arco_id || '/'
    FROM camino c
    JOIN v_arco_dirigido d ON d.origen_id = c.nodo_id
    WHERE instr(c.ruta_nodos, '/' || d.destino_id || '/') = 0
      AND d.arco_id    NOT IN (SELECT id FROM v_fuera_servicio WHERE tipo = 'arco')
      AND d.destino_id NOT IN (SELECT id FROM v_fuera_servicio WHERE tipo = 'nodo')
      AND c.saltos < 40
)
SELECT nodo_id, longitud_m, saltos, ruta_nodos, ruta_arcos
FROM camino
WHERE nodo_id = :nodo_id
ORDER BY longitud_m, saltos
LIMIT 1;
