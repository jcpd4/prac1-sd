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

def register_cp(cp_id, location, price_per_kwh=None):
    """Registra o actualiza un CP. Ahora acepta precio por kWh opcional."""
    with db_lock:
        if cp_id not in CP_DATA:
            CP_DATA[cp_id] = {'id': cp_id, 'location': location, 'status': 'DESCONECTADO', 'driver_id': None}
        # Actualiza/guarda el precio si se proporciona
        if price_per_kwh is not None:
            CP_DATA[cp_id]['price'] = float(price_per_kwh)
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
    """Actualiza la telemetría del CP (se marca SUMINISTRANDO)."""
    with db_lock:
        if cp_id in CP_DATA:
            # Forzar el estado a SUMINISTRANDO al recibir consumo
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
            # Marcar disponible
            CP_DATA[cp_id]['status'] = 'ACTIVADO'
    print(f"[DB] Campos de consumo de CP {cp_id} limpiados. Estado -> ACTIVADO")


def get_cp_price(cp_id):
    """Devuelve el precio €/kWh almacenado para el CP, o None si no existe."""
    with db_lock:
        return CP_DATA.get(cp_id, {}).get('price', None)