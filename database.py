# Fichero: database.py (CORREGIDO)
# Simulación de una base de datos con un diccionario simple
# En un entorno real, usarías SQLite o MySQL/MongoDB.
import threading
# Diccionario para almacenar los CPs. Clave: ID_CP
CP_DATA = {}
db_lock = threading.Lock()

def setup_database():
    """Inicializa la "base de datos"."""
    print("[DB] Base de datos inicializada (Diccionario en memoria).")

def register_cp(cp_id, location):
    """Registra o actualiza un CP."""
    with db_lock:
        if cp_id not in CP_DATA:
            CP_DATA[cp_id] = {'id': cp_id, 'location': location, 'status': 'DESCONECTADO', 'driver_id': None}
    print(f"[DB] CP {cp_id} registrado/actualizado.")

def update_cp_status(cp_id, status):
    """Actualiza solo el estado del CP."""
    with db_lock:
        if cp_id in CP_DATA:
            CP_DATA[cp_id]['status'] = status
    print(f"[DB] Estado de CP {cp_id} actualizado a {status}.")
    

    
def get_cp_status(cp_id):
    """Obtiene el estado actual de un CP."""
    with db_lock:    
        return CP_DATA.get(cp_id, {}).get('status', 'NO_EXISTE')

def get_all_cps():
    """Devuelve la lista de todos los CPs registrados."""
    with db_lock:
        return list(CP_DATA.values())

def update_cp_consumption(cp_id, kwh, importe, driver_id):
    """Actualiza la telemetría del CP."""
    with db_lock:    
        if cp_id in CP_DATA:
            # AÑADIDO: Forzar el estado a SUMINISTRANDO en la BD de la Central
            # cuando se recibe el primer mensaje de consumo.
            CP_DATA[cp_id]['status'] = 'SUMINISTRANDO' 
            CP_DATA[cp_id]['kwh'] = kwh
            CP_DATA[cp_id]['importe'] = importe
            CP_DATA[cp_id]['driver_id'] = driver_id