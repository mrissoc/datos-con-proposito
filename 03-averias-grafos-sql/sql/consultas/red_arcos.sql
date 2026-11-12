-- Arcos de la red con su estado actual y los códigos de sus extremos.
SELECT a.id, a.codigo, a.origen_id, a.destino_id, o.codigo AS origen_codigo, d.codigo AS destino_codigo,
       o.nombre || ' → ' || d.nombre AS nombre,
       a.tipo, a.longitud_m, a.diametro_mm, a.material, a.anio_instalacion, a.bidireccional,
       a.id_externo, a.estado, a.averias_abiertas
FROM v_estado_arco a
JOIN nodo o ON o.id = a.origen_id
JOIN nodo d ON d.id = a.destino_id
ORDER BY a.id;
