# Dashboard de indicadores económicos en Power BI

Tablero que muestra la evolución de los principales indicadores económicos de Chile. Los datos vienen del [ETL de indicadores](..), que actualiza los archivos de la carpeta `datos/`.

<!-- Agregar aquí las capturas una vez construido el tablero:
![Resumen](capturas/resumen.png)
![Detalle](capturas/detalle.png)
-->

## Datos

| Archivo | Contenido |
|---|---|
| `datos/dim_indicador.csv` | Código, nombre y unidad de cada indicador |
| `datos/fact_valor_diario.csv` | Valor por indicador y fecha |
| `datos/fact_valor_mensual.csv` | Promedio, mínimo y máximo mensual, con variación mensual y en 12 meses |

## Modelo

- `dim_indicador[codigo]` 1 → * `fact_valor_diario[codigo]`
- `dim_indicador[codigo]` 1 → * `fact_valor_mensual[codigo]`
- `Calendario[Date]` 1 → * `fact_valor_diario[fecha]`
- `Calendario[Date]` 1 → * `fact_valor_mensual[mes]`

Tabla de calendario:

```dax
Calendario =
ADDCOLUMNS (
    CALENDAR ( MIN ( fact_valor_diario[fecha] ), MAX ( fact_valor_diario[fecha] ) ),
    "Año", YEAR ( [Date] ),
    "Mes", FORMAT ( [Date], "YYYY-MM" )
)
```

## Medidas

```dax
Último valor =
VAR UltimaFecha =
    CALCULATE ( MAX ( fact_valor_diario[fecha] ), fact_valor_diario[fecha] <= TODAY () )
RETURN
    CALCULATE ( MAX ( fact_valor_diario[valor] ), fact_valor_diario[fecha] = UltimaFecha )

Promedio del período = AVERAGE ( fact_valor_diario[valor] )

Valor hace 12 meses =
VAR UltimaFecha =
    CALCULATE ( MAX ( fact_valor_diario[fecha] ), fact_valor_diario[fecha] <= TODAY () )
VAR FechaAnterior =
    CALCULATE (
        MAX ( fact_valor_diario[fecha] ),
        ALL ( Calendario ),
        fact_valor_diario[fecha] <= EDATE ( UltimaFecha, -12 )
    )
RETURN
    CALCULATE (
        MAX ( fact_valor_diario[valor] ),
        ALL ( Calendario ),
        fact_valor_diario[fecha] = FechaAnterior
    )

Variación 12 meses % = DIVIDE ( [Último valor] - [Valor hace 12 meses], [Valor hace 12 meses] )
```

Las medidas suponen un solo indicador seleccionado. La segmentación de indicador debe ser de selección única.

## Páginas

**1. Resumen**

- Segmentación de selección única por `dim_indicador[nombre]`.
- Tres tarjetas: Último valor, Variación 12 meses %, Promedio del período.
- Gráfico de líneas: `Calendario[Date]` contra `fact_valor_diario[valor]`.
- Tabla con los últimos 12 meses desde `fact_valor_mensual`: mes, promedio, mínimo, máximo y variación.

**2. Comparación anual**

- Gráfico de columnas: promedio por `Calendario[Año]`.
- Matriz: año en filas, meses en columnas, promedio mensual como valor, con formato condicional.

## Actualización

Al ejecutar `python etl.py` se regeneran los CSV. En Power BI Desktop basta con presionar **Actualizar**.
