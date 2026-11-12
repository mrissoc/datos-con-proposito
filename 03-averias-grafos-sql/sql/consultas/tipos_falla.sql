-- Catálogo de tipos de falla, según el tipo de elemento al que aplican.
SELECT codigo, nombre, aplica_a
FROM tipo_falla
ORDER BY aplica_a, nombre;
