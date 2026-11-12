-- Indicadores del estado actual de la red.
SELECT
    (SELECT COUNT(*) FROM averia WHERE estado <> 'cerrada')                         AS averias_abiertas,
    (SELECT COUNT(*) FROM v_fuera_servicio)                                         AS elementos_fuera_servicio,
    (SELECT COUNT(*) FROM v_estado_nodo
      WHERE tipo = 'sector' AND estado <> 'operativo')                              AS sectores_sin_suministro,
    (SELECT COALESCE(SUM(clientes), 0) FROM v_estado_nodo
      WHERE tipo = 'sector' AND estado <> 'operativo')                              AS clientes_sin_suministro,
    (SELECT SUM(clientes) FROM nodo)                                                AS clientes_totales,
    (SELECT COUNT(*) FROM nodo)                                                     AS nodos,
    (SELECT COUNT(*) FROM arco)                                                     AS arcos;
