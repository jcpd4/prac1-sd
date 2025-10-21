# Fichero: database.py (MODIFICADO PARA PERSISTENCIA)
import threading
import json
import os # Necesario para comprobar si el fichero existe

# Diccionario para almacenar los CPs en memoria. Clave: ID_CP
CP_DATA = {}
db_lock = threading.Lock()
DB_FILE = 'cps_data.json' # Nombre del fichero que usaremos como base de datos

def _save_to_disk():
    """Función interna para guardar el diccionario CP_DATA en el fichero JSON."""
    with db_lock:
        # Usamos 'indent=4' para que el fichero JSON sea legible para los humanos
        with open(DB_FILE, 'w') as f:
            json.dump(CP_DATA, f, indent=4)
    print(f"[DB] Base de datos guardada en {DB_FILE}.")

def _load_from_disk():
    """Función interna para cargar los datos desde el fichero JSON si existe."""
    global CP_DATA
    with db_lock:
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, 'r') as f:
                    CP_DATA = json.load(f)
                print(f"[DB] Base de datos cargada desde {DB_FILE}.")
            except json.JSONDecodeError:
                print(f"[DB] AVISO: El fichero {DB_FILE} está corrupto o vacío. Empezando con una BD limpia.")
                CP_DATA = {}
        else:
            print(f"[DB] No se encontró {DB_FILE}. Se creará uno nuevo al primer registro.")


def setup_database():
    """Inicializa la "base de datos" cargando los datos del disco."""
    _load_from_disk()
    print("[DB] Base de datos lista.")

def register_cp(cp_id, location, price_per_kwh=None):
    """Registra o actualiza un CP y guarda los cambios en el disco."""
    with db_lock:
        first_time_register = cp_id not in CP_DATA
        if first_time_register:
            CP_DATA[cp_id] = {'id': cp_id, 'location': location, 'status': 'DESCONECTADO', 'driver_id': None}
        
        # Actualiza la ubicación por si cambia
        CP_DATA[cp_id]['location'] = location

        # Actualiza/guarda el precio si se proporciona
        if price_per_kwh is not None:
            CP_DATA[cp_id]['price'] = float(price_per_kwh)

    # Solo guardamos si es un registro nuevo o se actualiza algo importante
    _save_to_disk()
    print(f"[DB] CP {cp_id} registrado/actualizado.")

def update_cp_status(cp_id, status):
    """Actualiza solo el estado del CP. No es necesario guardar en disco cada cambio de estado volátil."""
    with db_lock:
        if cp_id in CP_DATA:
            CP_DATA[cp_id]['status'] = status
    # No llamamos a _save_to_disk() aquí para no escribir en el disco constantemente.
    # El estado se considera volátil y se restablecerá a DESCONECTADO al reiniciar.
    print(f"[DB] Estado de CP {cp_id} actualizado a {status}.")

def get_cp_status(cp_id):
    """Obtiene el estado actual de un CP."""
    with db_lock:
        return CP_DATA.get(cp_id, {}).get('status', 'NO_EXISTE')

def get_all_cps():
    """Devuelve la lista de todos los CPs registrados."""
    with db_lock:
        return list(CP_DATA.values())
        
# --- Las funciones de consumo no necesitan cambios ---
def update_cp_consumption(cp_id, kwh, importe, driver_id):
    """Actualiza la telemetría del CP (se marca SUMINISTRANDO)."""
    with db_lock:
        if cp_id in CP_DATA:
            CP_DATA[cp_id]['status'] = 'SUMINISTRANDO'
            CP_DATA[cp_id]['kwh'] = float(kwh)
            CP_DATA[cp_id]['importe'] = float(importe)
            CP_DATA[cp_id]['driver_id'] = driver_id
    print(f"[DB] Consumo CP {cp_id} -> {kwh} kWh, {importe} €, driver {driver_id}")

def clear_cp_consumption(cp_id):
    """Limpia los campos de consumo tras SUPPLY_END y deja el CP en ACTIVO (ACTIVADO)."""
    with db_lock:
        if cp_id in CP_DATA:
            CP_DATA[cp_id].pop('kwh', None)
            CP_DATA[cp_id].pop('importe', None)
            CP_DATA[cp_id].pop('driver_id', None)
            CP_DATA[cp_id]['status'] = 'ACTIVADO'
    print(f"[DB] Campos de consumo de CP {cp_id} limpiados. Estado -> ACTIVADO")

def get_cp_price(cp_id):
    """Devuelve el precio €/kWh almacenado para el CP, o None si no existe."""
    with db_lock:
        return CP_DATA.get(cp_id, {}).get('price', None)