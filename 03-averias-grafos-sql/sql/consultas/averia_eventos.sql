-- Bitácora de cambios de estado de una avería.
SELECT estado, usuario, ocurrido_en
FROM averia_evento
WHERE averia_id = :id
ORDER BY ocurrido_en, id;
