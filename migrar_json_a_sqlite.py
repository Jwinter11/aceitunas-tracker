"""
Migración one-time: historial_precios.json → precios.db (SQLite)
Ejecutar una sola vez: python migrar_json_a_sqlite.py
"""
import json, sqlite3
from pathlib import Path

DIRECTORIO = Path(__file__).parent
json_path  = DIRECTORIO / "historial_precios.json"
db_path    = DIRECTORIO / "precios.db"

def crear_tabla(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS precios (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha          TEXT    NOT NULL,
            supermercado   TEXT    NOT NULL,
            nombre         TEXT    NOT NULL,
            ml             INTEGER,
            precio         REAL    NOT NULL,
            precio_sin_dto REAL,
            en_oferta      INTEGER NOT NULL DEFAULT 0,
            marca          TEXT,
            precio_litro   INTEGER,
            producto_id    TEXT,
            UNIQUE(fecha, supermercado, nombre)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fecha ON precios(fecha)")

def migrar():
    if not json_path.exists():
        print(f"ERROR: no se encontró {json_path}")
        return

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    semanas = data.get("semanas", [])
    print(f"Semanas a migrar: {len(semanas)}")

    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    crear_tabla(cur)

    total = 0
    for sem in semanas:
        fecha     = sem["fecha"]
        productos = sem.get("productos", [])
        rows = [
            (
                fecha,
                p.get("supermercado", ""),
                p.get("nombre", ""),
                p.get("ml"),
                p.get("precio", 0),
                p.get("precio_sin_dto"),
                1 if p.get("en_oferta") else 0,
                p.get("marca"),
                p.get("precio_litro"),
                p.get("producto_id"),
            )
            for p in productos
        ]
        cur.executemany("""
            INSERT OR IGNORE INTO precios
              (fecha, supermercado, nombre, ml, precio, precio_sin_dto,
               en_oferta, marca, precio_litro, producto_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, rows)
        total += len(rows)
        print(f"  {fecha}: {len(rows)} productos")

    conn.commit()
    conn.close()

    size_kb = db_path.stat().st_size / 1024
    print(f"\nMigracion completa: {total} registros -> precios.db ({size_kb:.1f} KB)")

if __name__ == "__main__":
    migrar()
