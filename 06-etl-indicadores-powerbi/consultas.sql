-- Consultas de ejemplo sobre indicadores.db (SQLite).
-- Ejecutar con: sqlite3 indicadores.db < consultas.sql

-- 1. Último valor disponible de cada indicador
SELECT d.nombre, f.fecha, f.valor, d.unidad_medida
FROM fact_valor f
JOIN dim_indicador d ON d.codigo = f.codigo
WHERE f.fecha = (SELECT MAX(fecha) FROM fact_valor
                 WHERE codigo = f.codigo AND fecha <= date('now', 'localtime'))
ORDER BY d.nombre;

-- 2. Dólar: promedio mensual y variación en 12 meses del último año
SELECT mes, ROUND(promedio, 2) AS promedio, var_mensual_pct, var_12m_pct
FROM vw_mensual
WHERE codigo = 'dolar'
ORDER BY mes DESC
LIMIT 12;

-- 3. Los 5 días con mayor alza diaria del dólar (función de ventana)
WITH diario AS (
    SELECT fecha,
           valor,
           valor - LAG(valor) OVER (ORDER BY fecha) AS alza
    FROM fact_valor
    WHERE codigo = 'dolar'
)
SELECT fecha, valor, ROUND(alza, 2) AS alza
FROM diario
WHERE alza IS NOT NULL
ORDER BY alza DESC
LIMIT 5;

-- 4. Promedio anual, mínimo y máximo por indicador
SELECT d.nombre,
       substr(f.fecha, 1, 4)  AS anio,
       ROUND(AVG(f.valor), 2) AS promedio,
       MIN(f.valor)           AS minimo,
       MAX(f.valor)           AS maximo
FROM fact_valor f
JOIN dim_indicador d ON d.codigo = f.codigo
GROUP BY d.nombre, anio
ORDER BY d.nombre, anio;

-- 5. Control de calidad: meses sin observaciones por indicador de frecuencia mensual
WITH RECURSIVE meses (mes) AS (
    SELECT (SELECT MIN(substr(fecha, 1, 7)) FROM fact_valor) || '-01'
    UNION ALL
    SELECT date(mes, '+1 month') FROM meses
    WHERE mes < date('now', 'localtime', 'start of month')
)
SELECT d.codigo, m.mes
FROM dim_indicador d
CROSS JOIN meses m
LEFT JOIN vw_mensual v ON v.codigo = d.codigo AND v.mes = m.mes
WHERE v.codigo IS NULL
ORDER BY d.codigo, m.mes;

-- 6. Historial de ejecuciones del ETL
SELECT * FROM etl_ejecucion ORDER BY id DESC LIMIT 10;
