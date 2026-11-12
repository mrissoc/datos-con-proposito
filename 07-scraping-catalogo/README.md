# Web scraping de un catálogo en línea hacia Excel

Script en Python que recorre el catálogo completo de una tienda en línea y lo entrega en Excel, listo para analizar precios por categoría.

El sitio de ejemplo es [books.toscrape.com](https://books.toscrape.com), creado para practicar scraping. La misma estructura se adapta a catálogos reales de productos, precios de la competencia o listados de propiedades.

## Qué hace

- Lee las 50 categorías del menú y sigue la paginación de cada una.
- Extrae de cada libro: título, categoría, precio, calificación, disponibilidad y enlace.
- Reintenta ante errores de red y hace una pausa entre solicitudes para no cargar el servidor.
- Genera `catalogo_libros.xlsx` con dos hojas: el detalle (1.000 libros) y un resumen por categoría con cantidad, precio promedio, mínimo, máximo y calificación promedio. También deja un CSV.

## Cómo ejecutarlo

```bash
pip install -r ../requirements.txt
python scraper.py                      # catálogo completo, cerca de 1 minuto
python scraper.py --max-categorias 3   # prueba rápida
```

## Buenas prácticas aplicadas

- Pausa entre solicitudes e identificación del cliente en el encabezado `User-Agent`.
- Antes de extraer datos de un sitio real se revisan sus términos de uso y su archivo `robots.txt`.

## Tecnologías

Python, Requests, BeautifulSoup, Pandas, openpyxl.
