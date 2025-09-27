# Fichero: database.py
import sqlite3

DB_NAME = "central_station.db"

def setup_database():
    """Crea la tabla de charging points si no existe."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Usamos 'IF NOT EXISTS' para no borrar la tabla cada vez que se inicia
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS charging_points (
        id TEXT PRIMARY KEY,
        location TEXT NOT NULL,
        status TEXT NOT NULL,
        last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()
    print("Base de datos configurada correctamente.")

def register_cp(cp_id, location):
    """Registra un nuevo CP o actualiza su ubicación si ya existe."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # 'INSERT OR REPLACE' es útil: si el ID ya existe, actualiza la fila; si no, la crea.
    # El estado inicial al registrarse es 'DESCONECTADO'. Se cambiará a 'ACTIVADO'
    # en cuanto el servidor confirme la conexión.
    cursor.execute(
        "INSERT OR REPLACE INTO charging_points (id, location, status) VALUES (?, ?, ?)",
        (cp_id, location, 'DESCONECTADO')
    )
    conn.commit()
    conn.close()

def update_cp_status(cp_id, status):
    """Actualiza el estado de un CP (ej: ACTIVADO, AVERIADO, etc.)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE charging_points SET status = ?, last_update = CURRENT_TIMESTAMP WHERE id = ?",
        (status, cp_id)
    )
    conn.commit()
    conn.close()

def get_all_cps():
    """Obtiene todos los CPs de la base de datos para mostrarlos en el panel."""
    conn = sqlite3.connect(DB_NAME)
    # Devolvemos las filas como diccionarios para un acceso más fácil (ej: row['status'])
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, location, status FROM charging_points ORDER BY id")
    cps = cursor.fetchall()
    conn.close()
    return cps