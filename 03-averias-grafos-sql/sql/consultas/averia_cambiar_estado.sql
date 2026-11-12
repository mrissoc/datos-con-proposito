-- Cambia el estado de una avería. Al cerrarla se registra la fecha de cierre;
-- los triggers impiden modificar una avería ya cerrada y escriben la bitácora.
UPDATE averia
SET estado          = :estado,
    actualizada_por = :usuario,
    cerrada_en      = CASE WHEN :estado = 'cerrada'
                           THEN strftime('%Y-%m-%dT%H:%M:%SZ', 'now') END
WHERE id = :id;
