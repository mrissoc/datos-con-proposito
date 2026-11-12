-- Historial de averías de un nodo o de un arco.
SELECT id, codigo, estado, severidad, fuera_servicio, descripcion, reportada_por, reportada_en,
       cerrada_en, tipo_falla_nombre, horas_abierta
FROM v_averia
WHERE elemento_tipo = :elemento_tipo
  AND elemento_id = :elemento_id
ORDER BY reportada_en DESC, id DESC
LIMIT 20;
