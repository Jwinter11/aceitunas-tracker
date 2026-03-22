# 🫒 Tracker de Aceite de Oliva Extra Virgen

## ¿Qué hace esto?

Scrapea los sitios de Carrefour, Jumbo, Disco, Vea, Chango Más, Coto y La Anónima
buscando aceites de oliva extra virgen, y genera un Excel con historial de precios.

## Instalación (una sola vez)

```bash
pip install requests beautifulsoup4 openpyxl
```

## Uso semanal

```bash
python scraper.py
```

Esto genera/actualiza `aceites_oliva_tracker.xlsx` y `historial_precios.json`.

## Automatización semanal (Linux/Mac)

Agregar al cron para que corra todos los lunes a las 8am:

```bash
crontab -e
# Agregar esta línea:
0 8 * * 1 cd /ruta/a/la/carpeta && python scraper.py
```

## Automatización semanal (Windows)

Usar el Programador de tareas de Windows para ejecutar `python scraper.py`
cada lunes a las 8am.

## Hojas del Excel

| Hoja | Contenido |
|------|-----------|
| **Última Semana** | Todos los productos de la semana más reciente |
| **Historial Completo** | Todos los registros de todas las semanas |
| **Evolución por Producto** | Tabla comparativa semana a semana por producto |

## Notas importantes

- Los precios en oferta se marcan en verde
- El precio por litro se calcula automáticamente para comparar entre tamaños
- El historial se guarda en `historial_precios.json` — no borrar ese archivo
- Si un supermercado cambia su sitio web, el scraper de esa cadena puede fallar;
  los otros seguirán funcionando normalmente

## Supermercados y método de scraping

| Supermercado | Método |
|---|---|
| Carrefour | API JSON (VTEX) |
| Jumbo | API JSON (VTEX) |
| Disco | API JSON (VTEX) |
| Vea | API JSON (VTEX) |
| Chango Más | API JSON (VTEX) |
| Coto | HTML scraping |
| La Anónima | HTML scraping |
