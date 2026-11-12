-- Averías, de la más reciente a la más antigua.
-- :estado admite un estado exacto, 'activas' (todas las no cerradas) o NULL (todas).
SELECT id, codigo, estado, severidad, fuera_servicio, descripcion, reportada_por, reportada_en,
       cerrada_en, id_externo, tipo_falla, tipo_falla_nombre, elemento_tipo, elemento_id,
       elemento_codigo, elemento_nombre, horas_abierta
FROM v_averia
WHERE :estado IS NULL
   OR estado = :estado
   OR (:estado = 'activas' AND estado <> 'cerrada')
ORDER BY reportada_en DESC, id DESC
LIMIT :limite;
