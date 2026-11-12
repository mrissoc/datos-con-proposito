-- Registra una avería sobre un nodo o un arco. El código se genera con el año
-- y un correlativo; la bitácora la escribe el trigger tr_averia_alta.
INSERT INTO averia (codigo, nodo_id, arco_id, tipo_falla, severidad, fuera_servicio,
                    descripcion, reportada_por)
VALUES (
    'AV-' || strftime('%Y', 'now') || '-'
          || printf('%04d', COALESCE((SELECT MAX(id) FROM averia), 0) + 1),
    CASE WHEN :elemento_tipo = 'nodo' THEN :elemento_id END,
    CASE WHEN :elemento_tipo = 'arco' THEN :elemento_id END,
    :tipo_falla,
    :severidad,
    :fuera_servicio,
    :descripcion,
    :usuario
);
