-- Elementos con más averías en los últimos 12 meses (reincidencia), con su
-- posición en el ranking y el tiempo promedio de reparación.
SELECT elemento_tipo, elemento_id, elemento_codigo, elemento_nombre,
       COUNT(*)                                                         AS averias,
       SUM(fuera_servicio)                                              AS con_corte,
       ROUND(AVG(CASE WHEN cerrada_en IS NOT NULL THEN horas_abierta END), 1) AS horas_reparacion,
       MAX(reportada_en)                                                AS ultima_averia,
       DENSE_RANK() OVER (ORDER BY COUNT(*) DESC)                       AS posicion
FROM v_averia
WHERE reportada_en >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-12 months')
GROUP BY elemento_tipo, elemento_id, elemento_codigo, elemento_nombre
ORDER BY averias DESC, elemento_codigo
LIMIT 10;
