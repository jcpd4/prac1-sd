# 📖 CAPÍTULO 8: Driver - Aplicación del Conductor en EVCharging

## 🎯 Objetivo
Entender cómo funciona la aplicación del conductor (`EV_Driver.py`), sus hilos, comunicación con Kafka, panel de usuario y lógica de recarga.

---

## 1) ¿Qué es el Driver?
El **Driver** es la aplicación que usan los conductores para:
- **Solicitar recargas** en puntos específicos
- **Recibir autorizaciones** o denegaciones de Central
- **Ver el estado de la red** de CPs disponibles
- **Monitorear el progreso** de su recarga en tiempo real
- **Recibir tickets** al finalizar la carga

**Analogía**: Es como la app de Uber, pero para cargar tu coche eléctrico.

---

## 2) Arquitectura del Driver

### 2.1 Hilos Concurrentes
```python
# 4 hilos principales:
1. notify_thread     → Escucha notificaciones de Central
2. network_thread    → Escucha estado de la red
3. panel_thread      → Muestra panel visual
4. main_thread       → Procesa comandos del usuario
```

### 2.2 Comunicación Kafka
- **Producer**: Envía solicitudes a `driver_requests`
- **Consumer 1**: Recibe notificaciones de `driver_notifications`
- **Consumer 2**: Recibe estado de red de `network_status`

---

## 3) Variables Globales y Estado

### 3.1 Configuración
```python
KAFKA_TOPIC_REQUESTS = 'driver_requests'      # Driver → Central
KAFKA_TOPIC_NOTIFY = 'driver_notifications'  # Central → Driver
KAFKA_TOPIC_NETWORK_STATUS = 'network_status' # Central → Driver
CLIENT_ID = ""  # ID del conductor (ej: "101")
```

### 3.2 Estado Compartido
```python
# Estado de la red de CPs
network_status = {}                    # {cp_id: {status, location}}
network_status_lock = threading.Lock() # Protege network_status

# Estado de recarga activa
active_charge_info = {}               # {cp_id: {kwh, importe}}
charge_lock = threading.Lock()        # Protege active_charge_info

# Mensajes del sistema
driver_messages = deque(maxlen=200)    # Log de comunicaciones
```

---

## 4) Hilo 1: Notificaciones de Central

### 4.1 Función: `process_central_notifications()`
```python
def process_central_notifications(kafka_broker, client_id, messages):
    consumer = KafkaConsumer(
        KAFKA_TOPIC_NOTIFY,
        bootstrap_servers=[kafka_broker],
        auto_offset_reset='latest',
        group_id=f'driver-{client_id}-notifications',
        value_deserializer=lambda x: json.loads(x.decode('utf-8'))
    )
```

### 4.2 Tipos de Mensajes Procesados

#### AUTH_OK (Autorización)
```python
if msg_type == 'AUTH_OK':
    messages.append(f" [AUTORIZADO] Recarga autorizada en CP {payload['cp_id']}.")
    active_charge_info[payload['cp_id']] = {'kwh': 0.0, 'importe': 0.0}
```

#### AUTH_DENIED (Denegación)
```python
elif msg_type == 'AUTH_DENIED':
    messages.append(f" [DENEGADO] Recarga RECHAZADA en CP {payload['cp_id']}. Razón: {payload.get('reason')}")
```

#### CONSUMO_UPDATE (Progreso)
```python
elif msg_type == 'CONSUMO_UPDATE':
    cp_id = payload['cp_id']
    if cp_id in active_charge_info:
        active_charge_info[cp_id]['kwh'] = payload['kwh']
        active_charge_info[cp_id]['importe'] = payload['importe']
```

#### TICKET (Finalización)
```python
elif msg_type == 'TICKET':
    messages.append(f" [TICKET] Recarga finalizada en CP {payload['cp_id']}. Consumo: {payload['kwh']} kWh. Coste final: {payload['importe']} €")
    if payload['cp_id'] in active_charge_info:
        del active_charge_info[payload['cp_id']]
```

#### SUPPLY_ERROR (Error)
```python
elif msg_type == 'SUPPLY_ERROR':
    reason = payload.get('reason', 'Carga interrumpida')
    kwh_p = payload.get('kwh_partial', 0)
    imp_p = payload.get('importe_partial', 0)
    messages.append(f" [ERROR SUMINISTRO] {reason}. Parcial: {kwh_p} kWh / {imp_p} €")
    if payload['cp_id'] in active_charge_info:
        del active_charge_info[payload['cp_id']]
```

### 4.3 Filtrado Inteligente
```python
# Solo procesa mensajes dirigidos a este driver
if msg_type in ['AUTH_OK', 'AUTH_DENIED']:
    if payload.get('user_id') != client_id:
        continue  # Ignorar mensajes de otros drivers

# Solo procesa consumo de CPs que está usando
elif msg_type in ['CONSUMO_UPDATE', 'TICKET', 'SUPPLY_ERROR']:
    cp_id_del_mensaje = payload.get('cp_id')
    with charge_lock:
        if cp_id_del_mensaje not in active_charge_info:
            continue  # Ignorar CPs que no está usando
```

---

## 5) Hilo 2: Estado de la Red

### 5.1 Función: `process_network_updates()`
```python
def process_network_updates(kafka_broker):
    consumer = KafkaConsumer(
        KAFKA_TOPIC_NETWORK_STATUS,
        bootstrap_servers=[kafka_broker],
        auto_offset_reset='latest',
        value_deserializer=lambda x: json.loads(x.decode('utf-8'))
    )
```

### 5.2 Actualización del Estado
```python
for message in consumer:
    payload = message.value
    if payload.get('type') == 'NETWORK_STATUS_UPDATE':
        with network_status_lock:
            network_status.clear()
            for cp in payload.get('cps', []):
                network_status[cp['id']] = {
                    'status': cp['status'], 
                    'location': cp['location']
                }
```

---

## 6) Hilo 3: Panel Visual

### 6.1 Función: `display_driver_panel()`
```python
def display_driver_panel(messages):
    while True:
        clear_screen()
        print(f"--- EV DRIVER APP: CLIENTE {CLIENT_ID} ---")
        print("="*50)
```

### 6.2 Secciones del Panel

#### Estado de Recarga Personal
```python
with charge_lock:
    if not active_charge_info:
        print("ESTADO: Listo para solicitar recarga.")
    else:
        for cp_id, data in active_charge_info.items():
            print(f"ESTADO: 🔋 Suministrando en {cp_id}...")
            print(f"   Consumo: {data['kwh']:.3f} kWh")
            print(f"   Coste actual: {data['importe']:.2f} €")
```

#### Puntos de Recarga Disponibles
```python
with network_status_lock:
    available_cps = {cp_id: data for cp_id, data in network_status.items() 
                    if data['status'] == 'ACTIVADO'}
    if not available_cps:
        print("Buscando puntos de recarga en la red...")
    else:
        for cp_id, data in available_cps.items():
            print(f"  -> {cp_id:<10} ({data['location']})")
```

#### Log de Comunicaciones
```python
print("\n*** LOG DE COMUNICACIONES (últimas) ***")
for msg in list(messages):
    print(msg)
```

---

## 7) Hilo 4: Lógica Interactiva

### 7.1 Función: `start_driver_interactive_logic()`
```python
def start_driver_interactive_logic(producer, messages):
    while True:
        command_line = input("DRIVER> ").strip()
        parts = command_line.split()
        command = parts[0].upper() if parts else ""
```

### 7.2 Comando SOLICITAR
```python
if command == 'SOLICITAR':
    if len(parts) != 2:
        messages.append("Uso: SOLICITAR <CP_ID>")
        continue
    cp_id = parts[1]
    request_message = {
        "user_id": CLIENT_ID, 
        "cp_id": cp_id, 
        "timestamp": time.time()
    }
    producer.send(KAFKA_TOPIC_REQUESTS, value=request_message)
    messages.append(f"-> Petición enviada a Central para CP {cp_id}. Esperando autorización...")
```

### 7.3 Comando BATCH
```python
elif command == 'BATCH' and len(parts) == 2:
    file_path = parts[1]
    with open(file_path, 'r') as fh:
        cps_to_request = [line.strip() for line in fh if line.strip()]
    
    for i, cp_id in enumerate(cps_to_request):
        # 1. Enviar petición
        request_message = {"user_id": CLIENT_ID, "cp_id": cp_id, "timestamp": time.time()}
        producer.send(KAFKA_TOPIC_REQUESTS, value=request_message)
        
        # 2. Esperar autorización
        time.sleep(5)
        
        # 3. Esperar fin de recarga
        while True:
            with charge_lock:
                if not active_charge_info:
                    break  # Recarga terminada
            time.sleep(1)
        
        # 4. Esperar 4 segundos entre recargas
        time.sleep(4)
```

---

## 8) Inicialización del Driver

### 8.1 Argumentos de Línea de Comandos
```python
if len(sys.argv) != 3:
    print(f"Uso: py ev_driver.py <kafka_broker_ip:port> <ID_CLIENTE>")
    print(f"Ejemplo: py ev_driver.py localhost:9092 101")
    sys.exit(1)

KAFKA_BROKER = sys.argv[1]  # localhost:9092
CLIENT_ID = sys.argv[2]     # 101
```

### 8.2 Configuración del Producer
```python
kafka_producer = KafkaProducer(
    bootstrap_servers=[KAFKA_BROKER],
    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    acks=1,        # Confirmación rápida
    linger_ms=5,   # Latencia baja
    retries=2      # Reintentos limitados
)
```

### 8.3 Inicio de Hilos
```python
# Hilo de notificaciones
notify_thread = threading.Thread(
    target=process_central_notifications,
    args=(KAFKA_BROKER, CLIENT_ID, driver_messages),
    daemon=True
)
notify_thread.start()

# Hilo de estado de red
network_thread = threading.Thread(
    target=process_network_updates,
    args=(KAFKA_BROKER,),
    daemon=True
)
network_thread.start()

# Hilo de panel visual
panel_thread = threading.Thread(
    target=display_driver_panel,
    args=(driver_messages,),
    daemon=True
)
panel_thread.start()

# Hilo principal: lógica interactiva
start_driver_interactive_logic(kafka_producer, driver_messages)
```

---

## 9) Flujo Completo de una Recarga

### 9.1 Solicitud Manual
1. **Usuario**: Escribe `SOLICITAR MAD-01`
2. **Driver**: Envía mensaje a `driver_requests`
3. **Central**: Procesa y autoriza/deniega
4. **Driver**: Recibe `AUTH_OK` o `AUTH_DENIED`
5. **Si autorizado**: Recibe `CONSUMO_UPDATE` cada segundo
6. **Al finalizar**: Recibe `TICKET` con totales

### 9.2 Solicitud BATCH
1. **Usuario**: Escribe `BATCH archivo.txt`
2. **Driver**: Lee CPs del archivo
3. **Para cada CP**: Repite flujo manual
4. **Entre recargas**: Espera 4 segundos
5. **Al final**: Muestra "Proceso BATCH finalizado"

---

## 10) Manejo de Errores

### 10.1 Errores de Kafka
```python
except Exception as e:
    messages.append(f"[ERROR KAFKA] No se pudo enviar la petición: {e}")
```

### 10.2 Errores de Archivo
```python
except Exception as e:
    messages.append(f"[ERROR] No se pudo leer el fichero: {e}")
    continue
```

### 10.3 Interrupciones
```python
except (EOFError, KeyboardInterrupt):
    raise  # Salir limpiamente
```

---

## 11) Características Avanzadas

### 11.1 Filtrado Inteligente
- Solo procesa mensajes dirigidos a su `CLIENT_ID`
- Solo muestra consumo de CPs que está usando
- Ignora mensajes de otros drivers

### 11.2 Estado Persistente
- Mantiene `active_charge_info` durante toda la recarga
- Actualiza progreso en tiempo real
- Limpia estado al recibir `TICKET` o `SUPPLY_ERROR`

### 11.3 Panel Dinámico
- Refresca cada segundo
- Muestra CPs disponibles en tiempo real
- Actualiza consumo durante la carga

---

## 12) Integración con el Sistema

### 12.1 Con Central
- **Envía**: Solicitudes de recarga
- **Recibe**: Autorizaciones, tickets, errores

### 12.2 Con Kafka
- **Topics**: `driver_requests`, `driver_notifications`, `network_status`
- **Serialización**: JSON automática
- **Configuración**: Latencia baja para mejor UX

### 12.3 Con Archivos
- **BATCH**: Lee lista de CPs desde archivo
- **Formato**: Un CP por línea
- **Ejemplo**: `archivo.txt` con `MAD-01`, `BCN-02`, etc.

---

## 13) Buenas Prácticas Aplicadas

### 13.1 Thread Safety
- Locks para `network_status` y `active_charge_info`
- Acceso seguro a estructuras compartidas

### 13.2 Manejo de Errores
- Try-catch en operaciones críticas
- Mensajes informativos al usuario
- Continuación del programa tras errores

### 13.3 UX/UI
- Panel claro y actualizado
- Comandos simples (`SOLICITAR`, `BATCH`, `QUIT`)
- Feedback inmediato de acciones

---

## 14) Ejemplo de Uso

### 14.1 Inicio del Driver
```bash
py ev_driver.py localhost:9092 101
```

### 14.2 Panel Mostrado
```
--- EV DRIVER APP: CLIENTE 101 ---
==================================================
ESTADO: Listo para solicitar recarga.

--- PUNTOS DE RECARGA DISPONIBLES ---
  -> MAD-01     (C/ Serrano 10)
  -> BCN-02     (Las Ramblas 55)
==================================================
COMANDOS: SOLICITAR <CP_ID> | [Q]UIT

*** LOG DE COMUNICACIONES (últimas) ***
Driver 101 iniciado.
Broker: localhost:9092
```

### 14.3 Durante la Recarga
```
ESTADO: 🔋 Suministrando en MAD-01...
   Consumo: 2.350 kWh
   Coste actual: 0.47 €

*** LOG DE COMUNICACIONES (últimas) ***
[AUTORIZADO] Recarga autorizada en CP MAD-01.
[CONSUMO_UPDATE] Progreso: 2.350 kWh / 0.47 €
```

---

## 15) Próximo Capítulo
¿Seguimos con:
- **Capítulo 9**: Flujo Completo del Sistema (end-to-end)
- **Capítulo 10**: Troubleshooting y Debugging
- **Capítulo 11**: Mejoras y Optimizaciones

---

## 16) Resumen
- **4 hilos**: Notificaciones, Red, Panel, Interactivo
- **3 topics Kafka**: Requests, Notifications, Network Status
- **2 comandos**: SOLICITAR (manual), BATCH (automático)
- **Estado persistente**: Seguimiento de recarga activa
- **Panel dinámico**: Actualización en tiempo real
- **Filtrado inteligente**: Solo mensajes relevantes

¿Con cuál capítulo seguimos? 🎓
