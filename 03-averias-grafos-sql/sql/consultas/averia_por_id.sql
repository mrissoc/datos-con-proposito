-- Una avería con el elemento afectado resuelto.
SELECT id, codigo, estado, severidad, fuera_servicio, descripcion, reportada_por, reportada_en,
       cerrada_en, id_externo, tipo_falla, tipo_falla_nombre, elemento_tipo, elemento_id,
       elemento_codigo, elemento_nombre, horas_abierta
FROM v_averia
WHERE id = :id;
