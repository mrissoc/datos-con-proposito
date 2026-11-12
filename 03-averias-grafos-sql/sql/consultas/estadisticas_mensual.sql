-- Averías por mes en los últimos 12 meses, con tiempo promedio de reparación.
SELECT substr(reportada_en, 1, 7)                                   AS mes,
       COUNT(*)                                                     AS registradas,
       SUM(CASE WHEN estado = 'cerrada' THEN 1 ELSE 0 END)          AS cerradas,
       SUM(fuera_servicio)                                          AS con_corte,
       ROUND(AVG(CASE WHEN cerrada_en IS NOT NULL
                      THEN (julianday(cerrada_en) - julianday(reportada_en)) * 24 END), 1)
                                                                    AS horas_reparacion
FROM averia
WHERE reportada_en >= strftime('%Y-%m-01T00:00:00Z', 'now', '-11 months')
GROUP BY substr(reportada_en, 1, 7)
ORDER BY mes;
