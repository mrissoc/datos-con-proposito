-- Nodos de la red con su estado actual (operativo, fuera de servicio o sin suministro).
SELECT id, codigo, nombre, tipo, x, y, clientes, anio_servicio, id_externo, estado, averias_abiertas
FROM v_estado_nodo
ORDER BY id;
