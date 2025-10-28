# 📖 CAPÍTULO 9: Engine - Motor del Punto de Recarga en EVCharging

## 🎯 Objetivo
Entender cómo funciona el Engine (`EV_CP_E.py`), sus problemas identificados, arquitectura, comunicación con Monitor y Central, y simulación de recarga.

---

## 1) ¿Qué es el Engine?
El **Engine** es el motor físico del punto de recarga que:
- **Simula la recarga real** de vehículos eléctricos
- **Responde a comandos** del Monitor (PARAR, REANUDAR, HEALTH_CHECK)
- **Envía telemetría** a Central vía Kafka cada segundo
- **Maneja autorizaciones** de drivers para iniciar recarga
- **Simula averías** y recuperaciones

**Analogía**: Es como el motor de un coche, pero para cargar otros coches.

---

## 2) Arquitectura del Engine

### 2.1 Hilos Concurrentes
```python
# 3 hilos principales:
1. health_server_thread → Escucha comandos del Monitor
2. display_thread       → Muestra estado (PROBLEMA: duplicado)
3. main_thread         → Procesa comandos del usuario
```

### 2.2 Comunicación
- **Sockets**: Recibe comandos del Monitor (síncrono)
- **Kafka Producer**: Envía telemetría a Central (asíncrono)
- **Socket Helper**: Consulta sesiones a Central (síncrono)

---

## 3) Variables Globales y Estado

### 3.1 Estado del Engine
```python
ENGINE_STATUS = {
    "health": "OK",        # OK o KO
    "is_charging": False,  # Si está cargando
    "driver_id": None      # ID del driver autorizado
}
status_lock = threading.Lock()  # Protege ENGINE_STATUS
```

### 3.2 Configuración Kafka
```python
KAFKA_PRODUCER = None           # Producer global
KAFKA_TOPIC_TELEMETRY = "cp_telemetry"  # Topic destino
CP_ID = ""                      # ID del CP (ej: "MAD-01")
BROKER = None                    # Broker Kafka
```

---

## 4) Problemas Identificados ❌

### 4.1 Funciones Duplicadas
```python
# PROBLEMA: Dos funciones que hacen lo mismo
def display_status():        # Líneas 113-125 (una vez)
def display_status_loop():   # Líneas 92-111 (bucle infinito)
```

### 4.2 Panel Duplicado
```python
# PROBLEMA: El panel se imprime DOS VECES
# 1. display_status_loop() → cada segundo en hilo separado
# 2. display_status() → después de cada comando
```

### 4.3 Prompt Fantasma
```python
# PROBLEMA: En display_status_loop() línea 107
print("\n> ", end="", flush=True)  # Crea prompt confuso
```

### 4.4 Funciones No Utilizadas
```python
# PROBLEMA: Funciones que no se usan
def stop_charging():           # Línea 302 - NO USADA
def simulate_fault():         # Línea 306 - NO USADA  
def simulate_connection_lost(): # Línea 321 - NO USADA
```

---

## 5) Hilo 1: Servidor de Salud

### 5.1 Función: `start_health_server()`
```python
def start_health_server(host, port):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((host, port))
    server_socket.listen(1)  # Solo una conexión (Monitor)
    
    while True:
        monitor_socket, address = server_socket.accept()
        monitor_thread = threading.Thread(
            target=handle_monitor_connection,
            args=(monitor_socket, address)
        )
        monitor_thread.daemon = True
        monitor_thread.start()
```

### 5.2 Función: `handle_monitor_connection()`
```python
def handle_monitor_connection(monitor_socket, monitor_address):
    data = monitor_socket.recv(1024).decode('utf-8').strip()
    
    with status_lock:
        if data.startswith("HEALTH_CHECK"):
            response = ENGINE_STATUS['health']  # OK o KO
            monitor_socket.sendall(response.encode('utf-8'))
            
        elif data.startswith("PARAR"):
            ENGINE_STATUS['is_charging'] = False
            monitor_socket.sendall(b"ACK#PARAR")
            
        elif data.startswith("REANUDAR"):
            ENGINE_STATUS['health'] = 'OK'
            monitor_socket.sendall(b"ACK#REANUDAR")
            
        elif data.startswith("AUTORIZAR_SUMINISTRO"):
            parts = data.split('#')
            driver_id = parts[1]
            ENGINE_STATUS['driver_id'] = driver_id
            monitor_socket.sendall(b"ACK#AUTORIZAR_SUMINISTRO")
```

---

## 6) Hilo 2: Panel Visual (PROBLEMÁTICO)

### 6.1 Función Duplicada: `display_status_loop()`
```python
def display_status_loop():
    while True:
        with status_lock:
            health = ENGINE_STATUS.get('health', 'N/A')
            cp = CP_ID
        clear_screen()
        print(f"--- EV CHARGING POINT ENGINE: {cp} ---")
        print("="*50)
        print(f"  ESTADO DE SALUD: {health}")
        print("  Información de suministro: disponible en la CENTRAL")
        print("="*50)
        print("Comandos: [F]AIL | [R]ECOVER | [I]NIT | [E]ND")
        print("-" * 50)
        print("\n> ", end="", flush=True)  # ❌ PROMPT FANTASMA
        time.sleep(1)
```

### 6.2 Función Duplicada: `display_status()`
```python
def display_status():
    clear_screen()
    print(f"--- EV CHARGING POINT ENGINE: {CP_ID} ---")
    print("="*50)
    print(f"  ESTADO DE SALUD: {ENGINE_STATUS['health']}")
    print("  Información de suministro: disponible en la CENTRAL")
    print("="*50)
    print("Comandos: [F]AIL para simular AVERÍA | [R]ECOVER para recuperar")
    print("          [I]NIT para simular ENCHUFAR vehículo")
    print("          [E]ND para simular DESENCHUFAR vehículo")
    print("-" * 50)
```

**Problema**: Se ejecutan ambas, creando duplicación visual.

---

## 7) Hilo 3: Lógica de Usuario

### 7.1 Función: `process_user_input()`
```python
def process_user_input():
    while True:
        command = input("\n> ").strip().upper()
        
        if command == 'F' or command == 'FAIL':
            with status_lock:
                ENGINE_STATUS['health'] = 'KO'
            print("\n[Engine] *** ESTADO INTERNO: AVERÍA (KO) ***")
            display_status()  # ❌ DUPLICACIÓN
            
        elif command == 'R' or command == 'RECOVER':
            with status_lock:
                ENGINE_STATUS['health'] = 'OK'
            print("\n[Engine] *** ESTADO INTERNO: RECUPERADO (OK) ***")
            display_status()  # ❌ DUPLICACIÓN
            
        elif command == 'I' or command == 'INIT':
            # Consultar sesión activa
            helper_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            helper_socket.connect(('127.0.0.1', ENGINE_PORT + 1000))
            helper_socket.sendall(f"CHECK_SESSION#{CP_ID}".encode('utf-8'))
            resp = helper_socket.recv(1024).decode('utf-8').strip()
            helper_socket.close()
            
            if resp != "NO_SESSION":
                driver_id = resp
                with status_lock:
                    ENGINE_STATUS['is_charging'] = True
                    ENGINE_STATUS['driver_id'] = driver_id
                
                # Enviar SESSION_STARTED
                send_telemetry_message({
                    "type": "SESSION_STARTED",
                    "cp_id": CP_ID,
                    "user_id": driver_id
                })
                
                # Iniciar simulación de carga
                threading.Thread(
                    target=simulate_charging,
                    args=(CP_ID, BROKER, driver_id),
                    daemon=True
                ).start()
            display_status()  # ❌ DUPLICACIÓN
            
        elif command == 'E' or command == 'END':
            with status_lock:
                ENGINE_STATUS['is_charging'] = False
            print(f"[ENGINE] *** SUMINISTRO FINALIZADO ***")
            display_status()  # ❌ DUPLICACIÓN
```

---

## 8) Simulación de Recarga

### 8.1 Función: `simulate_charging()`
```python
def simulate_charging(cp_id, broker, driver_id, price_per_kwh=0.20, step_kwh=0.1):
    init_kafka_producer(broker)
    total_kwh = 0.0
    total_importe = 0.0
    aborted_due_to_fault = False

    with status_lock:
        ENGINE_STATUS['is_charging'] = True
        ENGINE_STATUS['driver_id'] = driver_id

    try:
        while True:
            with status_lock:
                if not ENGINE_STATUS['is_charging']:
                    break
                if ENGINE_STATUS['health'] != "OK":
                    # Avería durante carga
                    payload_fault = {
                        "type": "AVERIADO",
                        "cp_id": cp_id,
                        "user_id": driver_id,
                        "kwh": round(total_kwh, 3),
                        "importe": round(total_importe, 2)
                    }
                    send_telemetry_message(payload_fault)
                    aborted_due_to_fault = True
                    return

            # Incrementar consumo cada segundo
            total_kwh += step_kwh
            total_importe = total_kwh * price_per_kwh

            payload = {
                "type": "CONSUMO",
                "cp_id": cp_id,
                "user_id": driver_id,
                "kwh": round(total_kwh, 3),
                "importe": round(total_importe, 2)
            }
            send_telemetry_message(payload)
            time.sleep(1)
            
    finally:
        if not aborted_due_to_fault:
            payload_end = {
                "type": "SUPPLY_END",
                "cp_id": cp_id,
                "user_id": driver_id,
                "kwh": round(total_kwh, 3),
                "importe": round(total_importe, 2)
            }
            send_telemetry_message(payload_end)

        with status_lock:
            ENGINE_STATUS['is_charging'] = False
            ENGINE_STATUS['driver_id'] = None
```

---

## 9) Comunicación Kafka

### 9.1 Función: `init_kafka_producer()`
```python
def init_kafka_producer(broker):
    global KAFKA_PRODUCER
    if KAFKA_PRODUCER is None:
        try:
            KAFKA_PRODUCER = KafkaProducer(
                bootstrap_servers=[broker],
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
            )
            print(f"[ENGINE] Kafka producer conectado a {broker}")
        except NoBrokersAvailable:
            # Lógica de reconexión automática
            def _reconnect_loop(broker, interval=5):
                global KAFKA_PRODUCER
                while KAFKA_PRODUCER is None:
                    try:
                        tmp = KafkaProducer(
                            bootstrap_servers=[broker],
                            value_serializer=lambda v: json.dumps(v).encode('utf-8')
                        )
                        KAFKA_PRODUCER = tmp
                        print(f"[ENGINE] Reconectado a Kafka broker {broker}")
                        break
                    except Exception as e:
                        print(f"[ENGINE] Reconexión fallida: {e}")
                        time.sleep(interval)
            threading.Thread(target=_reconnect_loop, args=(broker,), daemon=True).start()
```

### 9.2 Función: `send_telemetry_message()`
```python
def send_telemetry_message(payload):
    global KAFKA_PRODUCER
    
    # Validar mensajes de consumo
    msg_type = payload.get('type', '').upper()
    if msg_type in ['AVERIADO', 'CONEXION_PERDIDA', 'FAULT', 'SESSION_STARTED', 'SUPPLY_END']:
        pass  # Estos pueden enviarse sin carga activa
    else:
        # Para CONSUMO, validar que hay driver activo
        with status_lock:
            if not ENGINE_STATUS.get('is_charging') or not ENGINE_STATUS.get('driver_id'):
                print(f"[ENGINE] AVISO: Telemetría de consumo bloqueada")
                return
    
    print(f"[ENGINE] Enviando telemetry -> {payload}")
    if KAFKA_PRODUCER is None:
        print(f"[ENGINE] AVISO: Kafka producer no inicializado")
        return
        
    try:
        KAFKA_PRODUCER.send(KAFKA_TOPIC_TELEMETRY, value=payload)
        KAFKA_PRODUCER.flush()
    except Exception as e:
        print(f"[ENGINE] Error enviando telemetry: {e}")
```

---

## 10) Inicialización del Engine

### 10.1 Argumentos de Línea de Comandos
```python
if len(sys.argv) != 4:
    print("Uso: py ev_cp_e.py <PUERTO_ESCUCHA_ENGINE> <KAFKA_BROKER_IP:PORT> <ID_CP>")
    print("Ejemplo: py ev_cp_e.py 8001 localhost:9092 MAD-01")
    sys.exit(1)

ENGINE_PORT = int(sys.argv[1])    # 8001
KAFKA_BROKER = sys.argv[2]       # localhost:9092
CP_ID = sys.argv[3]              # MAD-01
ENGINE_HOST = '0.0.0.0'          # Escucha en todas las IPs
```

### 10.2 Inicio de Hilos
```python
# Guardar broker global
BROKER = KAFKA_BROKER

# Inicializar Kafka Producer
init_kafka_producer(KAFKA_BROKER)

# 1. Servidor de salud (Monitor)
threading.Thread(target=start_health_server, args=(ENGINE_HOST, ENGINE_PORT), daemon=True).start()

# 2. Panel visual (PROBLEMÁTICO: duplicado)
threading.Thread(target=display_status_loop, daemon=True).start()

# 3. Hilo principal: entrada de usuario
process_user_input()
```

---

## 11) Flujo Completo de una Recarga

### 11.1 Autorización
1. **Central** → Monitor: `AUTORIZAR_SUMINISTRO#driver_id`
2. **Monitor** → Engine: `AUTORIZAR_SUMINISTRO#driver_id`
3. **Engine** → Monitor: `ACK#AUTORIZAR_SUMINISTRO`
4. **Monitor** → Central: `ACK#AUTORIZAR_SUMINISTRO`

### 11.2 Inicio de Recarga
1. **Usuario** escribe `INIT` en Engine
2. **Engine** consulta sesión activa a Central
3. **Si autorizado**: Engine inicia `simulate_charging()`
4. **Engine** envía `SESSION_STARTED` a Central

### 11.3 Durante la Recarga
1. **Engine** envía `CONSUMO` cada segundo
2. **Central** recibe y actualiza BD
3. **Central** reenvía a Driver

### 11.4 Fin de Recarga
1. **Usuario** escribe `END` en Engine
2. **Engine** envía `SUPPLY_END` a Central
3. **Central** genera ticket y notifica a Driver

---

## 12) Problemas y Soluciones

### 12.1 Problema: Panel Duplicado
**Solución**: Eliminar `display_status_loop()` y usar solo `display_status()`

### 12.2 Problema: Prompt Fantasma
**Solución**: Eliminar `print("\n> ", end="", flush=True)` de `display_status_loop()`

### 12.3 Problema: Funciones No Utilizadas
**Solución**: Eliminar `stop_charging()`, `simulate_fault()`, `simulate_connection_lost()`

### 12.4 Problema: Duplicación Visual
**Solución**: Usar solo un panel que se actualice cuando sea necesario

---

## 13) Código Optimizado Propuesto

### 13.1 Panel Único
```python
def display_status():
    """Muestra el estado actual del Engine."""
    clear_screen()
    print(f"--- EV CHARGING POINT ENGINE: {CP_ID} ---")
    print("="*50)
    print(f"  ESTADO DE SALUD: {ENGINE_STATUS['health']}")
    print(f"  CARGANDO: {'SÍ' if ENGINE_STATUS['is_charging'] else 'NO'}")
    if ENGINE_STATUS['driver_id']:
        print(f"  DRIVER: {ENGINE_STATUS['driver_id']}")
    print("="*50)
    print("Comandos: [F]AIL | [R]ECOVER | [I]NIT | [E]ND")
    print("-" * 50)
```

### 13.2 Eliminar Hilo de Display
```python
# ❌ ELIMINAR ESTA LÍNEA:
# threading.Thread(target=display_status_loop, daemon=True).start()

# ✅ USAR SOLO:
# display_status() se llama cuando sea necesario
```

---

## 14) Integración con el Sistema

### 14.1 Con Monitor
- **Recibe**: HEALTH_CHECK, PARAR, REANUDAR, AUTORIZAR_SUMINISTRO
- **Envía**: OK/KO, ACK/NACK

### 14.2 Con Central
- **Envía**: CONSUMO, SESSION_STARTED, SUPPLY_END, AVERIADO
- **Recibe**: Consultas de sesión activa

### 14.3 Con Kafka
- **Topic**: `cp_telemetry`
- **Mensajes**: Telemetría en tiempo real

---

## 15) Ejemplo de Uso

### 15.1 Inicio del Engine
```bash
py ev_cp_e.py 8001 localhost:9092 MAD-01
```

### 15.2 Panel Mostrado
```
--- EV CHARGING POINT ENGINE: MAD-01 ---
==================================================
  ESTADO DE SALUD: OK
  CARGANDO: NO
==================================================
Comandos: [F]AIL | [R]ECOVER | [I]NIT | [E]ND
--------------------------------------------------
```

### 15.3 Durante la Recarga
```
--- EV CHARGING POINT ENGINE: MAD-01 ---
==================================================
  ESTADO DE SALUD: OK
  CARGANDO: SÍ
  DRIVER: 101
==================================================
Comandos: [F]AIL | [R]ECOVER | [I]NIT | [E]ND
--------------------------------------------------
```

---

## 16) Próximo Capítulo
¿Seguimos con:
- **Capítulo 10**: Flujo Completo del Sistema (end-to-end)
- **Capítulo 11**: Troubleshooting y Debugging
- **Capítulo 12**: Mejoras y Optimizaciones

---

## 17) Resumen
- **3 hilos**: Salud, Display (problemático), Usuario
- **Problemas**: Panel duplicado, funciones no usadas, prompt fantasma
- **Comunicación**: Sockets con Monitor, Kafka con Central
- **Simulación**: Recarga real con telemetría cada segundo
- **Estados**: OK/KO, cargando/no cargando, driver asignado
- **Comandos**: FAIL, RECOVER, INIT, END

¿Con cuál capítulo seguimos? 🎓
