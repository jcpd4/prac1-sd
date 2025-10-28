# 📖 CAPÍTULO 5: Base de Datos SQLite - Persistencia de Datos en EVCharging

## 🎯 Objetivo
Entender cómo tu proyecto usa SQLite para almacenar y gestionar datos de CPs, drivers y transacciones de forma persistente.

---

## 1) ¿Qué es SQLite?
- Una base de datos **embebida** (no necesita servidor separado)
- Se almacena en un **archivo único** (`ev_charging.db`)
- **ACID compliant**: transacciones seguras
- **Thread-safe**: múltiples hilos pueden acceder simultáneamente
- **Sin configuración**: funciona "out of the box"

Analogía:
- SQLite = Un archivador muy organizado en tu escritorio
- Tablas = Categorías de documentos (CPs, Drivers, Transacciones)
- Filas = Documentos individuales
- Columnas = Campos de cada documento

---

## 2) Estructura de tu Base de Datos

### 2.1 Tabla `charging_points` (Puntos de Recarga)
```sql
CREATE TABLE charging_points (
    id TEXT PRIMARY KEY,           -- MAD-01, BCN-02, etc.
    location TEXT NOT NULL,        -- "C/ Serrano 10"
    status TEXT NOT NULL DEFAULT 'DESCONECTADO', -- Estados del CP
    price REAL,                    -- Precio por kWh (€)
    driver_id TEXT,                -- Driver actualmente asignado
    kwh REAL DEFAULT 0.0,          -- Consumo actual (kWh)
    importe REAL DEFAULT 0.0,      -- Coste actual (€)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### 2.2 Tabla `drivers` (Conductores)
```sql
CREATE TABLE drivers (
    id TEXT PRIMARY KEY,           -- 101, 202, etc.
    name TEXT,                     -- Nombre del conductor
    email TEXT,                    -- Email de contacto
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### 2.3 Tabla `transactions` (Transacciones)
```sql
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cp_id TEXT NOT NULL,           -- CP usado
    driver_id TEXT NOT NULL,       -- Driver que cargó
    kwh REAL NOT NULL,            -- kWh consumidos
    importe REAL NOT NULL,         -- Coste final
    start_time TIMESTAMP,          -- Inicio de carga
    end_time TIMESTAMP,            -- Fin de carga
    status TEXT NOT NULL,          -- COMPLETADA, INTERRUMPIDA, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cp_id) REFERENCES charging_points (id)
)
```

---

## 3) Configuración y Seguridad

### 3.1 Variables Globales
```python
DB_FILE = "ev_charging.db"         # Archivo de la BD
USE_SQLITE = True                  # Activar/desactivar SQLite
db_lock = threading.Lock()         # Protección contra concurrencia
```

### 3.2 Thread Safety
- **`db_lock`**: Lock global que protege todas las operaciones
- **`check_same_thread=False`**: Permite acceso desde múltiples hilos
- **Conexiones temporales**: Se abren y cierran en cada operación

---

## 4) Funciones Principales de la BD

### 4.1 Inicialización
```python
def setup_database():
    """Crea las tablas si no existen."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Crear tabla charging_points
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
    
    conn.commit()
    conn.close()
```

### 4.2 Registro de CPs
```python
def register_cp(cp_id, location, price_per_kwh=0.25):
    """Registra o actualiza un CP."""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        
        # Insertar si no existe
        cursor.execute(
            "INSERT OR IGNORE INTO charging_points (id, location, status, price) VALUES (?, ?, 'DESCONECTADO', ?)",
            (cp_id, location, price_per_kwh)
        )
        
        # Actualizar siempre ubicación y precio
        cursor.execute(
            "UPDATE charging_points SET location = ?, price = ? WHERE id = ?",
            (location, price_per_kwh, cp_id)
        )
        
        conn.commit()
        conn.close()
```

### 4.3 Gestión de Estados
```python
def update_cp_status(cp_id, status):
    """Actualiza el estado de un CP."""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("UPDATE charging_points SET status = ? WHERE id = ?", (status, cp_id))
        conn.commit()
        conn.close()

def get_cp_status(cp_id):
    """Obtiene el estado de un CP."""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM charging_points WHERE id = ?", (cp_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 'NO_EXISTE'
```

### 4.4 Gestión de Consumo
```python
def update_cp_consumption(cp_id, kwh, importe, driver_id):
    """Actualiza consumo durante la carga."""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE charging_points SET status = 'SUMINISTRANDO', kwh = ?, importe = ?, driver_id = ? WHERE id = ?",
            (kwh, importe, driver_id, cp_id)
        )
        conn.commit()
        conn.close()

def clear_cp_consumption(cp_id):
    """Limpia consumo al finalizar carga."""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE charging_points SET status = 'ACTIVADO', kwh = NULL, importe = NULL, driver_id = NULL WHERE id = ?",
            (cp_id,)
        )
        conn.commit()
        conn.close()
```

### 4.5 Consultas Complejas
```python
def get_all_cps():
    """Obtiene todos los CPs como lista de diccionarios."""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # Convierte filas en diccionarios
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM charging_points")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
```

---

## 5) Flujo de Datos en tu Sistema

### 5.1 Registro de CP (Monitor → Central)
1. **Monitor** envía: `REGISTER#MAD-01#C/ Serrano 10#0.25`
2. **Central** llama: `database.register_cp("MAD-01", "C/ Serrano 10", 0.25)`
3. **BD** ejecuta: `INSERT OR IGNORE` + `UPDATE`
4. **Estado inicial**: `DESCONECTADO`

### 5.2 Autorización de Carga (Driver → Central)
1. **Driver** solicita carga en `MAD-01`
2. **Central** verifica: `database.get_cp_status("MAD-01")` → `ACTIVADO`
3. **Central** reserva: `database.update_cp_status("MAD-01", "RESERVADO")`
4. **Central** autoriza al driver

### 5.3 Consumo Durante Carga (Engine → Central)
1. **Engine** envía telemetría cada segundo: `{"type": "CONSUMO", "kwh": 0.3, "importe": 0.06}`
2. **Central** actualiza: `database.update_cp_consumption("MAD-01", 0.3, 0.06, "101")`
3. **Estado**: `SUMINISTRANDO` con datos actualizados

### 5.4 Finalización de Carga
1. **Engine** envía: `{"type": "SUPPLY_END", "kwh": 2.5, "importe": 0.50}`
2. **Central** limpia: `database.clear_cp_consumption("MAD-01")`
3. **Estado**: `ACTIVADO` (disponible para siguiente carga)

---

## 6) Estados de CPs en la BD

| Estado | Descripción | Color Panel | Cuándo se usa |
|--------|-------------|-------------|---------------|
| `DESCONECTADO` | CP no conectado | GRIS | Inicio, desconexión |
| `ACTIVADO` | CP disponible | VERDE | CP libre y operativo |
| `RESERVADO` | CP asignado | AZUL | Driver autorizado |
| `SUMINISTRANDO` | CP cargando | VERDE | Durante la carga |
| `AVERIADO` | CP con fallo | ROJO | Avería detectada |
| `FUERA_DE_SERVICIO` | CP parado | NARANJA | Comando PARAR |

---

## 7) Manejo de Errores y Robustez

### 7.1 Fallback a Diccionarios
```python
if not USE_SQLITE: 
    return  # Si SQLite falla, usar diccionarios en memoria
```

### 7.2 Manejo de Excepciones
```python
try:
    # Operación de BD
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    # ... operaciones ...
    conn.commit()
    conn.close()
except Exception as e:
    print(f"[DB] ERROR: {e}")
    # Sistema continúa funcionando
```

### 7.3 Logging
```python
logger.info(f"[DB] Base de datos SQLite inicializada: {DB_FILE}")
logger.error(f"[DB] Error inicializando SQLite: {e}")
```

---

## 8) Ventajas de SQLite en tu Proyecto

### 8.1 Simplicidad
- **Sin servidor**: No necesitas instalar MySQL/PostgreSQL
- **Archivo único**: Fácil backup y portabilidad
- **Sin configuración**: Funciona inmediatamente

### 8.2 Rendimiento
- **ACID**: Transacciones seguras
- **Thread-safe**: Múltiples hilos simultáneos
- **Índices**: Búsquedas rápidas por ID

### 8.3 Persistencia
- **Datos permanentes**: Sobrevive a reinicios
- **Historial**: Transacciones completas
- **Backup**: Copiar archivo `.db`

---

## 9) Consultas SQL Ejemplo

### 9.1 CPs Activos
```sql
SELECT id, location, status FROM charging_points 
WHERE status = 'ACTIVADO';
```

### 9.2 Consumo por Driver
```sql
SELECT driver_id, SUM(kwh) as total_kwh, SUM(importe) as total_cost
FROM charging_points 
WHERE driver_id IS NOT NULL
GROUP BY driver_id;
```

### 9.3 CPs con Mayor Uso
```sql
SELECT cp_id, COUNT(*) as num_transactions, SUM(kwh) as total_kwh
FROM transactions 
GROUP BY cp_id 
ORDER BY num_transactions DESC;
```

---

## 10) Integración con el Panel de Central

El panel de Central lee datos directamente de la BD:

```python
def display_panel(central_messages, driver_requests):
    # Obtener todos los CPs de la BD
    all_cps = database.get_all_cps()
    
    for cp in all_cps:
        price = database.get_cp_price(cp['id'])
        colored_status = get_status_color(cp['status'])
        
        # Mostrar en panel con colores
        print(f"{cp['id']:<10} | {cp['location']:<25} | {price:.2f} €/kWh | {colored_status}")
        
        # Si está suministrando, mostrar consumo
        if cp['status'] == 'SUMINISTRANDO':
            print(f"    -> SUMINISTRANDO: {cp['kwh']:.3f} kWh | {cp['importe']:.2f} € | driver: {cp['driver_id']}")
```

---

## 11) Próximo Capítulo
¿Seguimos con:
- **Capítulo 6**: Panel de Monitorización (display_panel, colores, actualizaciones)
- **Capítulo 7**: Locks y Sincronización (threading.Lock, concurrencia)

---

## 12) Resumen
- **SQLite**: BD embebida en archivo único
- **3 tablas**: charging_points, drivers, transactions
- **Thread-safe**: Lock global para concurrencia
- **Estados**: DESCONECTADO → ACTIVADO → RESERVADO → SUMINISTRANDO
- **Persistencia**: Datos sobreviven a reinicios
- **Integración**: Panel lee directamente de BD

¿Con cuál capítulo seguimos? 🎓
