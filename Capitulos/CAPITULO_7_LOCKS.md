# 📖 CAPÍTULO 7: Locks y Sincronización - Concurrencia Segura en EVCharging

## 🎯 Objetivo
Entender cómo tu proyecto usa `threading.Lock` para evitar condiciones de carrera y garantizar acceso seguro a recursos compartidos entre múltiples hilos.

---

## 1) ¿Qué es un Lock?
Un **Lock** (cerrojo) es un mecanismo de sincronización que garantiza que **solo un hilo a la vez** puede acceder a un recurso compartido.

**Analogía**: Es como un baño público con una puerta que se puede cerrar con llave. Solo una persona puede estar dentro a la vez. Los demás esperan en la cola.

**En programación**:
- **Sin Lock**: Múltiples hilos pueden modificar la misma variable → **condición de carrera** → datos corruptos
- **Con Lock**: Solo un hilo puede modificar → **acceso exclusivo** → datos seguros

---

## 2) Locks en tu Proyecto

### 2.1 Central (`EV_Central.py`)
```python
# Lock para proteger recursos compartidos
active_cp_lock = threading.Lock()

# Recursos protegidos:
active_cp_sockets = {}           # Diccionario de sockets activos
connected_drivers = set()        # Drivers conectados
current_sessions = {}           # Sesiones activas
```

### 2.2 Engine (`EV_CP_E.py`)
```python
# Lock para proteger el estado del Engine
status_lock = threading.Lock()

# Recurso protegido:
ENGINE_STATUS = {
    "health": "OK", 
    "is_charging": False, 
    "driver_id": None
}
```

### 2.3 Base de Datos (`database.py`)
```python
# Lock para proteger operaciones de BD
db_lock = threading.Lock()

# Recurso protegido: Archivo SQLite ev_charging.db
```

---

## 3) Uso del Lock: Patrón `with`

### 3.1 Sintaxis Básica
```python
with lock:
    # Código que modifica recursos compartidos
    # El lock se adquiere automáticamente
    # Se libera automáticamente al salir del bloque
```

### 3.2 Ejemplo en Central
```python
# Proteger acceso a connected_drivers
with active_cp_lock:
    connected_drivers.add(user_id)
    current_sessions[cp_id] = {'driver_id': user_id, 'status': 'authorized'}
```

### 3.3 Ejemplo en Engine
```python
# Proteger acceso a ENGINE_STATUS
with status_lock:
    ENGINE_STATUS['is_charging'] = True
    ENGINE_STATUS['driver_id'] = driver_id
```

---

## 4) Casos de Uso Reales en tu Código

### 4.1 Central: Gestión de Drivers Conectados
```python
def send_notification_to_driver(producer, driver_id, notification):
    # Verificar si el driver está conectado (con lock)
    with active_cp_lock:
        if driver_id not in connected_drivers:
            return False
    
    # Enviar notificación (sin lock, solo lectura)
    producer.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=notification)
```

### 4.2 Central: Panel de Monitorización
```python
def display_panel(central_messages, driver_requests):
    # Leer drivers conectados (con lock)
    with active_cp_lock:
        if connected_drivers:
            for driver_id in connected_drivers:
                # Verificar asignaciones...
```

### 4.3 Engine: Comandos del Monitor
```python
def handle_monitor_connection(monitor_socket, monitor_address):
    with status_lock:
        if data.startswith("PARAR"):
            ENGINE_STATUS['is_charging'] = False
            monitor_socket.sendall(b"ACK#PARAR")
        elif data.startswith("REANUDAR"):
            ENGINE_STATUS['health'] = 'OK'
            monitor_socket.sendall(b"ACK#REANUDAR")
```

### 4.4 Engine: Simulación de Carga
```python
def simulate_charging(cp_id, broker, driver_id):
    with status_lock:
        ENGINE_STATUS['is_charging'] = True
        ENGINE_STATUS['driver_id'] = driver_id
    
    while True:
        with status_lock:
            if not ENGINE_STATUS['is_charging']:
                break
            if ENGINE_STATUS['health'] != "OK":
                # Manejar avería...
                return
```

---

## 5) Base de Datos: Thread Safety

### 5.1 Operaciones Protegidas
```python
def update_cp_status(cp_id, status):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("UPDATE charging_points SET status = ? WHERE id = ?", (status, cp_id))
        conn.commit()
        conn.close()
```

### 5.2 Lecturas Protegidas
```python
def get_all_cps():
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM charging_points")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
```

---

## 6) ¿Por qué Necesitamos Locks?

### 6.1 Problema Sin Lock (Condición de Carrera)
```python
# HILO A: Lee ENGINE_STATUS['is_charging'] = False
# HILO B: Lee ENGINE_STATUS['is_charging'] = False (al mismo tiempo)
# HILO A: Escribe ENGINE_STATUS['is_charging'] = True
# HILO B: Escribe ENGINE_STATUS['is_charging'] = False (sobrescribe A)
# RESULTADO: Estado inconsistente
```

### 6.2 Solución Con Lock
```python
# HILO A: Adquiere lock → Lee False → Escribe True → Libera lock
# HILO B: Espera lock → Adquiere lock → Lee True → Escribe False → Libera lock
# RESULTADO: Estado consistente
```

---

## 7) Tipos de Acceso

### 7.1 Lectura (Sin Modificación)
```python
# Múltiples hilos pueden leer simultáneamente
with status_lock:
    health = ENGINE_STATUS['health']  # Solo lectura
```

### 7.2 Escritura (Con Modificación)
```python
# Solo un hilo puede escribir a la vez
with status_lock:
    ENGINE_STATUS['health'] = 'KO'  # Modificación
```

---

## 8) Buenas Prácticas Aplicadas

### 8.1 Lock Granular
- **Central**: `active_cp_lock` protege múltiples estructuras relacionadas
- **Engine**: `status_lock` protege solo `ENGINE_STATUS`
- **BD**: `db_lock` protege todas las operaciones de BD

### 8.2 Duración Mínima
```python
# ✅ BUENO: Lock solo donde se necesita
with status_lock:
    ENGINE_STATUS['is_charging'] = True
# Lock liberado inmediatamente

# ❌ MALO: Lock innecesariamente largo
with status_lock:
    ENGINE_STATUS['is_charging'] = True
    time.sleep(5)  # ¡Bloquea otros hilos!
```

### 8.3 Evitar Deadlocks
```python
# ✅ BUENO: Orden consistente de locks
with lock1:
    with lock2:
        # Operaciones...

# ❌ MALO: Orden inconsistente puede causar deadlock
# Hilo A: lock1 → lock2
# Hilo B: lock2 → lock1  (¡Deadlock!)
```

---

## 9) Ejemplo Completo: Flujo de Autorización

### 9.1 Driver Solicita Carga
```python
# Hilo Kafka recibe solicitud
def process_kafka_requests():
    with active_cp_lock:
        connected_drivers.add(user_id)
        current_sessions[cp_id] = {'driver_id': user_id, 'status': 'authorized'}
```

### 9.2 Panel Muestra Estado
```python
# Hilo Panel lee estado
def display_panel():
    with active_cp_lock:
        for driver_id in connected_drivers:
            # Mostrar driver conectado
```

### 9.3 Limpieza de Drivers
```python
# Hilo Limpieza elimina drivers inactivos
def cleanup_disconnected_drivers():
    with active_cp_lock:
        drivers_to_remove = set()
        # Identificar drivers inactivos...
        for driver_id in drivers_to_remove:
            connected_drivers.discard(driver_id)
```

---

## 10) Rendimiento y Consideraciones

### 10.1 Overhead del Lock
- **Costo**: Adquirir/liberar lock tiene un pequeño costo
- **Beneficio**: Evita corrupción de datos (mucho más costoso)
- **Balance**: En tu proyecto, el beneficio supera ampliamente el costo

### 10.2 Contención
- **Baja contención**: Pocos hilos compiten por el mismo lock
- **Alta contención**: Muchos hilos esperan el mismo lock
- **Tu caso**: Baja contención (pocos hilos modifican cada recurso)

---

## 11) Debugging de Problemas de Concurrencia

### 11.1 Síntomas Sin Lock
- Estados inconsistentes
- Datos corruptos
- Comportamiento impredecible
- Crashes ocasionales

### 11.2 Síntomas Con Lock Mal Usado
- **Deadlock**: Sistema se cuelga
- **Livelock**: Sistema funciona pero muy lento
- **Starvation**: Algunos hilos nunca obtienen el lock

---

## 12) Alternativas a Locks

### 12.1 En tu Proyecto (No Usadas)
- **Semáforos**: Para controlar acceso a recursos limitados
- **Eventos**: Para señalizar entre hilos
- **Colas thread-safe**: Para comunicación entre hilos

### 12.2 Por qué Locks en tu Caso
- **Simplicidad**: Fácil de entender y usar
- **Eficiencia**: Bajo overhead
- **Adecuado**: Para proteger estructuras de datos compartidas

---

## 13) Resumen de Locks en tu Sistema

| Módulo | Lock | Protege | Usado por |
|--------|------|---------|-----------|
| **Central** | `active_cp_lock` | `active_cp_sockets`, `connected_drivers`, `current_sessions` | Kafka, Panel, Limpieza |
| **Engine** | `status_lock` | `ENGINE_STATUS` | Monitor, Simulación, Comandos |
| **BD** | `db_lock` | Archivo SQLite | Todas las operaciones de BD |

---

## 14) Próximo Capítulo
¿Seguimos con:
- **Capítulo 8**: Flujo Completo del Sistema (end-to-end)
- **Capítulo 9**: Troubleshooting y Debugging
- **Capítulo 10**: Mejoras y Optimizaciones

---

## 15) Resumen
- **Locks**: Garantizan acceso exclusivo a recursos compartidos
- **Patrón `with`**: Adquisición/liberación automática
- **3 locks principales**: Central, Engine, BD
- **Thread safety**: Evita condiciones de carrera
- **Buenas prácticas**: Granularidad, duración mínima, orden consistente

¿Con cuál capítulo seguimos? 🎓
