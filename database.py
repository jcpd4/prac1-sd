# JUANKY A SEVA --> CUIDADO CON LA DATABASE QUE LA ESTAMOS HACIENDO DE DOS FORMAS DISTINTAS!!!
# Fichero: database.py (MEJORADO)
# Base de datos mejorada con persistencia y más funcionalidades
# Soporte para SQLite y funcionalidades avanzadas
import threading
import json
import os
import sqlite3
import time
from datetime import datetime
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Diccionario para almacenar los CPs. Clave: ID_CP (1)
import sqlite3
DB_FILE = "ev_charging.db"
USE_SQLITE = True
db_lock = threading.Lock()

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


# Configuración de base de datos
DB_FILE = "ev_charging.db"
USE_SQLITE = True  # Cambiar a False para usar solo diccionarios en memoria

def setup_database():
    """Inicializa la base de datos (SQLite o memoria)."""
    if USE_SQLITE:
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            
            # Crear tablas si no existen
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS charging_points (
                    id TEXT PRIMARY KEY,
                    location TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DESCONECTADO',
                    price REAL,
                    driver_id TEXT,
                    kwh REAL DEFAULT 0.0,
                    importe REAL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS drivers (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    email TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cp_id TEXT NOT NULL,
                    driver_id TEXT NOT NULL,
                    kwh REAL NOT NULL,
                    importe REAL NOT NULL,
                    start_time TIMESTAMP,
                    end_time TIMESTAMP,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (cp_id) REFERENCES charging_points (id)
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info(f"[DB] Base de datos SQLite inicializada: {DB_FILE}")
            
        except Exception as e:
            logger.error(f"[DB] Error inicializando SQLite: {e}")
            logger.info("[DB] Fallback a diccionario en memoria")
    else:
        logger.info("[DB] Base de datos inicializada (Diccionario en memoria)")

# --- MODIFICACIÓN 2: Reemplazar la función register_cp ---(1)
def register_cp(cp_id, location, price_per_kwh=0.25):
    """Registra un nuevo CP o actualiza uno existente en la BD SQLite."""
    if not USE_SQLITE: return # Si no usamos SQLite, no hacemos nada
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            cursor = conn.cursor()
            # Intenta insertar; si el CP ya existe, no hace nada.
            cursor.execute(
                "INSERT OR IGNORE INTO charging_points (id, location, status, price) VALUES (?, ?, 'DESCONECTADO', ?)",
                (cp_id, location, price_per_kwh)
            )
            # Siempre actualiza la ubicación y el precio por si cambian.
            cursor.execute(
                "UPDATE charging_points SET location = ?, price = ? WHERE id = ?",
                (location, price_per_kwh, cp_id)
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[DB] ERROR al registrar CP {cp_id} en SQLite: {e}")
# --- FIN MODIFICACIÓN 2 ---
# --- MODIFICACIÓN 4: Reemplazar la función update_cp_status ---(1)
def update_cp_status(cp_id, status):
    """Actualiza el estado de un CP en la BD SQLite."""
    if not USE_SQLITE: return
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("UPDATE charging_points SET status = ? WHERE id = ?", (status, cp_id))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[DB] ERROR al actualizar estado de {cp_id} en SQLite: {e}")
# --- FIN MODIFICACIÓN 4 ---

def get_cp_status(cp_id):
    if not USE_SQLITE: return 'NO_EXISTE'
    status = 'NO_EXISTE'
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM charging_points WHERE id = ?", (cp_id,))
            result = cursor.fetchone()
            if result:
                status = result[0]
            conn.close()
    except Exception as e:
        print(f"[DB] ERROR al obtener estado de {cp_id} en SQLite: {e}")
    return status

# --- MODIFICACIÓN 3: Reemplazar la función get_all_cps ---(1)
def get_all_cps():
    """Obtiene todos los CPs de la BD SQLite y los devuelve como una lista de diccionarios."""
    if not USE_SQLITE: return []
    cps = []
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            conn.row_factory = sqlite3.Row  # Esto permite obtener resultados como diccionarios
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM charging_points")
            rows = cursor.fetchall()
            cps = [dict(row) for row in rows]
            conn.close()
    except Exception as e:
        print(f"[DB] ERROR al obtener todos los CPs desde SQLite: {e}")
    return cps
# --- FIN MODIFICACIÓN 3 ---
        
def update_cp_consumption(cp_id, kwh, importe, driver_id):
    if not USE_SQLITE: return
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE charging_points SET status = 'SUMINISTRANDO', kwh = ?, importe = ?, driver_id = ? WHERE id = ?",
                (kwh, importe, driver_id, cp_id)
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[DB] ERROR al actualizar consumo de {cp_id} en SQLite: {e}")

def clear_cp_consumption(cp_id):
    if not USE_SQLITE: return
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE charging_points SET status = 'ACTIVADO', kwh = NULL, importe = NULL, driver_id = NULL WHERE id = ?",
                (cp_id,)
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[DB] ERROR al limpiar consumo de {cp_id} en SQLite: {e}")

def get_cp_price(cp_id):
    if not USE_SQLITE: return None
    price = None
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT price FROM charging_points WHERE id = ?", (cp_id,))
            result = cursor.fetchone()
            if result:
                price = result[0]
            conn.close()
    except Exception as e:
        print(f"[DB] ERROR al obtener precio de {cp_id} en SQLite: {e}")
    return price

def clear_cp_telemetry_only(cp_id):
    if not USE_SQLITE: return
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE charging_points SET kwh = NULL, importe = NULL, driver_id = NULL WHERE id = ?",
                (cp_id,)
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[DB] ERROR al limpiar telemetría de {cp_id} en SQLite: {e}")

# --- NUEVAS FUNCIONES MEJORADAS---

def register_driver(driver_id, name=None, email=None):
    """Registra un nuevo conductor."""
    with db_lock:
        DRIVER_DATA[driver_id] = {
            'id': driver_id,
            'name': name or f"Driver-{driver_id}",
            'email': email,
            'created_at': datetime.now().isoformat()
        }
    logger.info(f"[DB] Driver {driver_id} registrado")

def get_driver(driver_id):
    """Obtiene información de un conductor."""
    with db_lock:
        return DRIVER_DATA.get(driver_id, None)

def start_transaction(cp_id, driver_id):
    """Inicia una nueva transacción de recarga."""
    with db_lock:
        transaction = {
            'id': len(TRANSACTION_HISTORY) + 1,
            'cp_id': cp_id,
            'driver_id': driver_id,
            'start_time': datetime.now().isoformat(),
            'status': 'INICIADA'
        }
        TRANSACTION_HISTORY.append(transaction)
        logger.info(f"[DB] Transacción iniciada: CP {cp_id} -> Driver {driver_id}")
        return transaction['id']

def end_transaction(transaction_id, kwh, importe, status='COMPLETADA'):
    """Finaliza una transacción."""
    with db_lock:
        for transaction in TRANSACTION_HISTORY:
            if transaction['id'] == transaction_id:
                transaction['kwh'] = kwh
                transaction['importe'] = importe
                transaction['end_time'] = datetime.now().isoformat()
                transaction['status'] = status
                logger.info(f"[DB] Transacción {transaction_id} finalizada: {kwh} kWh, {importe} €")
                return True
        return False

def get_transaction_history(driver_id=None, cp_id=None, limit=100):
    """Obtiene el historial de transacciones."""
    with db_lock:
        filtered_transactions = []
        for transaction in TRANSACTION_HISTORY:
            if driver_id and transaction.get('driver_id') != driver_id:
                continue
            if cp_id and transaction.get('cp_id') != cp_id:
                continue
            filtered_transactions.append(transaction)
        
        # Ordenar por fecha de creación (más recientes primero)
        filtered_transactions.sort(key=lambda x: x.get('start_time', ''), reverse=True)
        return filtered_transactions[:limit]

def get_cp_statistics(cp_id):
    """Obtiene estadísticas de un CP."""
    with db_lock:
        cp_transactions = [t for t in TRANSACTION_HISTORY if t.get('cp_id') == cp_id]
        
        if not cp_transactions:
            return {
                'total_transactions': 0,
                'total_kwh': 0.0,
                'total_revenue': 0.0,
                'avg_session_duration': 0.0
            }
        
        total_kwh = sum(t.get('kwh', 0) for t in cp_transactions)
        total_revenue = sum(t.get('importe', 0) for t in cp_transactions)
        
        # Calcular duración promedio de sesiones
        durations = []
        for t in cp_transactions:
            if t.get('start_time') and t.get('end_time'):
                try:
                    start = datetime.fromisoformat(t['start_time'])
                    end = datetime.fromisoformat(t['end_time'])
                    duration = (end - start).total_seconds() / 60  # minutos
                    durations.append(duration)
                except:
                    pass
        
        avg_duration = sum(durations) / len(durations) if durations else 0
        
        return {
            'total_transactions': len(cp_transactions),
            'total_kwh': total_kwh,
            'total_revenue': total_revenue,
            'avg_session_duration': avg_duration
        }

def get_system_statistics():
    """Obtiene estadísticas generales del sistema."""
    with db_lock:
        total_cps = len(CP_DATA)
        active_cps = len([cp for cp in CP_DATA.values() if cp.get('status') == 'ACTIVADO'])
        charging_cps = len([cp for cp in CP_DATA.values() if cp.get('status') == 'SUMINISTRANDO'])
        
        total_drivers = len(DRIVER_DATA)
        total_transactions = len(TRANSACTION_HISTORY)
        
        return {
            'total_cps': total_cps,
            'active_cps': active_cps,
            'charging_cps': charging_cps,
            'total_drivers': total_drivers,
            'total_transactions': total_transactions
        }

def backup_database():
    """Crea una copia de seguridad de la base de datos."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"backup_{timestamp}.json"
        
        backup_data = {
            'cp_data': CP_DATA,
            'driver_data': DRIVER_DATA,
            'transaction_history': TRANSACTION_HISTORY,
            'backup_time': datetime.now().isoformat()
        }
        
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"[DB] Backup creado: {backup_file}")
        return backup_file
        
    except Exception as e:
        logger.error(f"[DB] Error creando backup: {e}")
        return None

def restore_database(backup_file):
    """Restaura la base de datos desde un backup."""
    try:
        with open(backup_file, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
        
        with db_lock:
            CP_DATA.clear()
            DRIVER_DATA.clear()
            TRANSACTION_HISTORY.clear()
            
            CP_DATA.update(backup_data.get('cp_data', {}))
            DRIVER_DATA.update(backup_data.get('driver_data', {}))
            TRANSACTION_HISTORY.extend(backup_data.get('transaction_history', []))
        
        logger.info(f"[DB] Base de datos restaurada desde: {backup_file}")
        return True
        
    except Exception as e:
        logger.error(f"[DB] Error restaurando backup: {e}")
        return False