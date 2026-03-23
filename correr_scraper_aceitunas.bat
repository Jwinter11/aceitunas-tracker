@echo off
cd /d "C:\Users\Julian\OneDrive\Escritorio\Aceite_tracker"

REM ── 1. Correr scraper de aceitunas ────────────────────────────────────────
"C:\Users\Julian\AppData\Local\Python\pythoncore-3.14-64\python.exe" scraper_aceitunas.py >> logs_scraper_aceitunas.txt 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: scraper_aceitunas fallo, no se pushea a GitHub >> logs_scraper_aceitunas.txt
    exit /b 1
)

REM ── 2. Copiar precios.db al repo de aceitunas ─────────────────────────────
copy /Y "C:\Users\Julian\OneDrive\Escritorio\Aceite_tracker\precios.db" "C:\Users\Julian\OneDrive\Escritorio\aceitunas-tracker\precios.db" >> logs_scraper_aceitunas.txt 2>&1

REM ── 3. Escribir timestamp para forzar redeploy en Streamlit Cloud ─────────
echo %date% %time% > "C:\Users\Julian\OneDrive\Escritorio\aceitunas-tracker\last_update.txt"

REM ── 4. Pushear a GitHub (actualiza dashboard online) ──────────────────────
cd /d "C:\Users\Julian\OneDrive\Escritorio\aceitunas-tracker"
git add precios.db last_update.txt >> logs_scraper_aceitunas.txt 2>&1
git commit -m "Scrape automatico aceitunas %date%" >> logs_scraper_aceitunas.txt 2>&1
git push origin main >> logs_scraper_aceitunas.txt 2>&1
if errorlevel 1 (
    echo [%date% %time%] AVISO: git push fallo (sin internet?) >> logs_scraper_aceitunas.txt
) else (
    echo [%date% %time%] Datos aceitunas pusheados a GitHub OK >> logs_scraper_aceitunas.txt
)
