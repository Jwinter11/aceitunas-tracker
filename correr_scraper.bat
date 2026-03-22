@echo off
cd /d "C:\Users\Julian\OneDrive\Escritorio\Aceite_tracker"

REM ── 1. Correr scraper ─────────────────────────────────────────────────────
"C:\Users\Julian\AppData\Local\Python\pythoncore-3.14-64\python.exe" scraper.py >> logs_scraper.txt 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: scraper falló, no se pushea a GitHub >> logs_scraper.txt
    exit /b 1
)

REM ── 2. Pushear precios.db a GitHub (actualiza dashboard online) ───────────
git add precios.db historial_precios.json >> logs_scraper.txt 2>&1
git commit -m "Scrape automatico %date%" >> logs_scraper.txt 2>&1
git push origin main >> logs_scraper.txt 2>&1
if errorlevel 1 (
    echo [%date% %time%] AVISO: git push falló (sin internet?) >> logs_scraper.txt
) else (
    echo [%date% %time%] Datos pusheados a GitHub OK >> logs_scraper.txt
)
