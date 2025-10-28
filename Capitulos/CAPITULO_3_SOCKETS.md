# 📖 CAPÍTULO 3: Sistema de Sockets - Cómo se Conectan los CPs

## 🎯 OBJETIVO DEL CAPÍTULO

Entender cómo funciona el sistema de sockets TCP/IP que permite a los CPs conectarse a Central.

---

## 🤔 CONCEPTO FUNDAMENTAL: ¿QUÉ ES UN SOCKET?

**Socket = Conexión telefónica directa entre dos programas**

```
CP              Socket               Central
[Conecta] ───────────────────> [Escucha]
   ↓                             ↓
Envía: REGISTER          Recibe y procesa
Recibe: ACK             Envía confirmación
```

**Características:**
- ✅ Conexión **persistente** (se mantiene abierta)
- ✅ Comunicación **bidireccional** (ambos pueden enviar/recibir)
- ✅ TCP/IP (**Transmission Control Protocol**)
- ✅ Garantiza que los mensajes lleguen **en orden** y **sin errores**

---

## 🏛️ ARQUITECTURA: SERVIDOR vs CLIENTE

### SERVIDOR (Central)
```python
# Central actúa como SERVIDOR
# Líneas 711-728 de EV_Central.py
```

**Fases:**
1. **bind()** - "Me pongo en el puerto 8000"
2. **listen()** - "Espero conexiones"
3. **accept()** - "Cuando alguien se conecte, le respondo"
4. **loop infinito** - "Siempre esperando más conexiones"

### CLIENTE (CP)
```python
# CP actúa como CLIENTE
# Líneas 174-179 de EV_CP_M.py
```

**Fases:**
1. **socket()** - "Creo un socket"
2. **connect()** - "Me conecto a 127.0.0.1:8000"
3. **send()** - "Envío mi mensaje"
4. **recv()** - "Espero respuesta"

---

## 📋 FUNCIÓN 1: `start_socket_server()` (Líneas 711-728)

### ¿Qué hace esta función?
**Sirve como "Portero" que escucha en la puerta.**

```python
def start_socket_server(host, port, central_messages, kafka_broker):
    """Inicia el servidor de sockets para escuchar a los CPs."""
```

### Paso a paso:

#### 1. Crear el Socket (Línea 713)
```python
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
```

**Explicación:**
- `socket.AF_INET` → Protocolo IPv4 (direcciones como 127.0.0.1)
- `socket.SOCK_STREAM` → TCP (Transmission Control Protocol)
- **Resultado:** Socket configurado para IPv4 + TCP

#### 2. Asociar con IP y Puerto (Línea 715)
```python
server_socket.bind((host, port))  # ('0.0.0.0', 8000)
```

**¿Qué significa `0.0.0.0`?**
- Escucha en **TODAS** las interfaces de red
- Localhost: ✅
- Red local: ✅
- Cualquier red: ✅

**Analogía:** "Estoy en el puerto 8000, escúchame desde cualquier lugar"

#### 3. Entrar en Modo Escucha (Línea 717)
```python
server_socket.listen(5)  # Puede aceptar hasta 5 conexiones en cola
```

**¿Qué significa `listen(5)`?**
- **Cola de espera:** Si llegan 6 CPs al mismo tiempo:
  - 5 esperan en cola
  - 1 se rechaza (temporalmente)
- **Sin `listen()`:** Solo 1 conexión a la vez
- **Con `listen(5)`:** 5 conexiones simultáneas + 1 procesándose = 6 total

**Analogía:** Restaurante con **5 mesas libres** esperando clientes

#### 4. Bucle Infinito (Líneas 721-728)
```python
while True:
    client_socket, address = server_socket.accept()
    client_thread = threading.Thread(target=handle_client, args=(...))
    client_thread.daemon = True
    client_thread.start()
```

**¿Qué hace `accept()`?**
- **BLOQUEA** hasta que un CP se conecte
- Cuando llega conexión, devuelve:
  - `client_socket` → Canal de comunicación con ese CP
  - `address` → IP:puerto del CP (ej: ('127.0.0.1', 54678))

**¿Por qué crear un HILO para cada CP?**
```python
# SIN HILOS (MAL):
while True:
    accept()  # Espera CP1
    procesar CP1
    accept()  # Espera CP2
    procesar CP2
    # CP2 debe ESPERAR a que CP1 termine ❌

# CON HILOS (BIEN):
while True:
    accept()  # Llega CP1
    crear_hilo(procesar CP1)  # CP1 se procesa en paralelo
    aceptar CP2  # ← Puede aceptar CP2 mientras CP1 se procesa ✅
```

**Resultado:** Todos los CPs se procesan **simultáneamente**

---

## 📋 FUNCIÓN 2: `handle_client()` (Líneas 646-709)

### ¿Qué hace esta función?
**Atiende a un CP específico que se acaba de conectar.**

```python
def handle_client(client_socket, address, central_messages, kafka_broker):
    """Maneja la conexión de un único CP."""
```

### Fase 1: Leer Primer Mensaje (Líneas 651-653)
```python
message = client_socket.recv(1024).decode('utf-8')
# Espera hasta 1024 bytes de datos
# Decodifica: b'REGISTER#MAD-01#...' → 'REGISTER#MAD-01#...'

parts = message.strip().split('#')
# 'REGISTER#MAD-01#C/ Serrano 10#0.25' → ['REGISTER', 'MAD-01', 'C/ Serrano 10', '0.25']
```

**¿Qué puede recibir?**
- `REGISTER#CP_ID#LOCATION#PRICE` → Registro de CP
- `CHECK_SESSION#CP_ID` → Consulta de sesión
- `CHECK_DRIVER#CP_ID` → Consulta de driver

### Fase 2: Procesar REGISTER (Líneas 660-674)
```python
if len(parts) >= 3 and parts[0] == 'REGISTER':
    cp_id = parts[1]           # 'MAD-01'
    location = parts[2]         # 'C/ Serrano 10'
    price = float(parts[3])    # 0.25 (si existe)
```

**Procesamiento:**
```python
# 1. Registrar en BD
database.register_cp(cp_id, location, price_per_kwh=price)

# 2. Cambiar estado a ACTIVADO
database.update_cp_status(cp_id, 'ACTIVADO')

# 3. Guardar referencia del socket
with active_cp_lock:
    active_cp_sockets[cp_id] = client_socket
    # Para poder enviar comandos después
```

**¿Por qué guardar el socket?**
```python
# Central necesita comunicarse con CP más tarde
# Ejemplo: Operador escribe "P MAD-01" → Parar MAD-01
# 
# active_cp_sockets['MAD-01'].sendall(b"PARAR#CENTRAL")
```

### Fase 3: Bucle de Escucha (Líneas 680-688)
```python
while True:
    data = client_socket.recv(1024)
    if not data:
        break  # CP cerró conexión
    
    process_socket_data2(data, cp_id, address, ...)
```

**¿Qué puede recibir mientras está conectado?**
- `FAULT#MAD-01` → Reporte de avería
- `RECOVER#MAD-01` → CP recuperado
- `ACK#PARAR` → Confirmación de comando
- `NACK#PARAR` → Rechazo de comando

**¿Por qué `while True`?**
- CP puede enviar **múltiples mensajes**
- Sin loop → Solo recibe 1 mensaje y termina
- Con loop → Sigue recibiendo mensajes indefinidamente

### Fase 4: Desconexión (Líneas 695-709)
```python
finally:
    if cp_id:
        # Limpiar del diccionario
        with active_cp_lock:
            if cp_id in active_cp_sockets:
                del active_cp_sockets[cp_id]
        # Actualizar estado
        database.update_cp_status(cp_id, 'DESCONECTADO')
    client_socket.close()
```

**¿Cuándo se ejecuta `finally`?**
- CP cierra su programa
- Error en la conexión
- Central se cae
- **SIEMPRE** se ejecuta (garantiza limpieza)

---

## 📋 FUNCIÓN 3: `process_socket_data2()` (Líneas 505-643)

### ¿Qué hace esta función?
**Procesa mensajes que llegan durante la conexión.**

### Tipos de mensajes que maneja:

#### 1. FAULT - Reporte de Avería (Líneas 522-572)
```python
if command == 'FAULT':
    # 1. ¿Hay suministro en curso?
    if cp_info.get('status') == 'SUMINISTRANDO':
        # 2. Notificar al driver
        send_notification_to_driver(...)
        # 3. Guardar consumo parcial
    # 4. Cambiar estado a AVERIADO
    database.update_cp_status(cp_id, 'AVERIADO')
```

**Ejemplo de flujo:**
```
CP MAD-01 detecta avería
    ↓
Envía: FAULT#MAD-01
    ↓
Central procesa
    ↓
Estado: SUMINISTRANDO → AVERIADO (ROJO)
    ↓
Driver recibe: "Carga interrumpida"
```

#### 2. RECOVER - CP Recuperado (Líneas 574-578)
```python
elif command == 'RECOVER':
    # CP se recuperó de avería
    database.update_cp_status(cp_id, 'ACTIVADO')
```

#### 3. ACK/NACK - Confirmaciones (Líneas 581-600)
```python
elif command == 'ACK':
    if action == 'REANUDAR':
        # CP reanudó correctamente
        database.update_cp_status(cp_id, 'ACTIVADO')
    elif action == 'PARAR':
        # CP se paró correctamente
        database.update_cp_status(cp_id, 'FUERA_DE_SERVICIO')
```

#### 4. CHECK_DRIVER / CHECK_SESSION (Líneas 602-637)
**Usado por Engine para validar sesiones antes de iniciar suministro**

---

## 🎬 ESCENARIO COMPLETO: Ejemplo Práctico

### Paso 1: CP inicia conexión
```
TIEMPO: 0:10
CP MAD-01 → connect(127.0.0.1:8000)
Central acepta → Crear hilo para MAD-01
```

### Paso 2: CP envía REGISTER
```
TIEMPO: 0:11
CP envía: "REGISTER#MAD-01#C/ Serrano 10#0.25"
    ↓
Central procesa:
    ↓
• Llama a database.register_cp('MAD-01', 'C/ Serrano 10', 0.25)
• Llama a database.update_cp_status('MAD-01', 'ACTIVADO')
• Guarda socket en active_cp_sockets['MAD-01']
    ↓
Panel: MAD-01 aparece en VERDE ✅
```

### Paso 3: CP reporta avería
```
TIEMPO: 1:30
CP envía: "FAULT#MAD-01"
    ↓
Central procesa:
    ↓
• Lee estado actual: SUMINISTRANDO (driver 101 cargando)
• Notifica a driver 101: "Carga interrumpida"
• Cambia estado a AVERIADO
    ↓
Panel: MAD-01 aparece en ROJO ❌
```

### Paso 4: CP se recupera
```
TIEMPO: 5:00
CP envía: "RECOVER#MAD-01"
    ↓
Central procesa:
    ↓
• Cambia estado a ACTIVADO
    ↓
Panel: MAD-01 aparece en VERDE ✅
```

### Paso 5: Operador escribe "P MAD-01"
```
TIEMPO: 10:00
Operador escribe: P MAD-01
    ↓
input_thread → process_user_input()
    ↓
send_cp_command('MAD-01', 'PARAR')
    ↓
Recupera socket: active_cp_sockets['MAD-01']
    ↓
Envía: "PARAR#CENTRAL"
    ↓
CP recibe, procesa, responde: "ACK#PARAR"
    ↓
Panel: MAD-01 aparece en NARANJA ⚠️
```

---

## 🔐 CONCEPTOS IMPORTANTES: LOCKS

### ¿Por qué se usa `active_cp_lock`?
```python
# Línea 677:
with active_cp_lock:
    active_cp_sockets[cp_id] = client_socket
```

**Problema sin lock:**
```
HILO 1: active_cp_sockets['MAD-01'] = socket1  # ← Escribe
HILO 2: active_cp_sockets['MAD-01'] = socket2  # ← Escribe AL MISMO TIEMPO
# Resultado: No sabemos cuál ganó → ERROR
```

**Solución con lock:**
```
HILO 1: 
    🔒 CERRO LA PUERTA
    active_cp_sockets['MAD-01'] = socket1
    🔓 ABRE LA PUERTA

HILO 2: (Espera que HILO 1 termine)
    🔒 CERRO LA PUERTA
    active_cp_sockets['MAD-01'] = socket2
    🔓 ABRE LA PUERTA
```

**Resultado:** Solo 1 hilo modifica a la vez → Seguro ✅

---

## 📊 RESUMEN DEL FLUJO COMPLETO

```
┌─────────────────────────────────────────────────────────────┐
│                  CP INICIA CONEXIÓN                        │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  1. server_socket.accept() - Espera que CP se conecte      │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Crear nuevo HILO para atender a este CP                │
│     → handle_client() corre en paralelo                    │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  3. client_socket.recv() - Lee primer mensaje              │
│     "REGISTER#MAD-01#C/ Serrano 10#0.25"                   │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  4. database.register_cp() - Guardar en BD                 │
│     database.update_cp_status('MAD-01', 'ACTIVADO')        │
│     active_cp_sockets['MAD-01'] = client_socket           │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  5. while True: - Bucle infinito escuchando mensajes       │
│     • FAULT, RECOVER, ACK, NACK, etc.                     │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  6. CP se desconecta                                       │
│     finally: - Limpiar conexión                            │
│     del active_cp_sockets['MAD-01']                        │
└─────────────────────────────────────────────────────────────┘
```

---

## ✅ CONCEPTOS CLAVE APRENDIDOS

1. **Socket = Conexión directa persistente**
2. **Servidor escucha** (bind + listen + accept)
3. **Cliente se conecta** (connect)
4. **Hilo por cliente** → Atiende múltiples CPs simultáneamente
5. **Locks** → Protegen variables compartidas
6. **Estado persistente** → Se guarda en BD SQLite

---

## 🎓 PREGUNTAS DE COMPRENSIÓN

### ¿Cuántos CPs pueden conectarse simultáneamente?
**Respuesta:** Ilimitados (cada uno en su propio hilo)

### ¿Qué pasa si un CP envía un mensaje no válido?
**Respuesta:** Se procesa como "mensaje no reconocido" y se ignora

### ¿Por qué se guarda el socket en `active_cp_sockets`?
**Respuesta:** Para poder enviar comandos PARAR/REANUDAR después

---

## 🚀 PRÓXIMO CAPÍTULO

¿Continuamos con el **CAPÍTULO 4: Sistema de Kafka**?

Enseñaré:
- ✅ Topic `driver_requests` (pedidos de drivers)
- ✅ Topic `cp_telemetry` (telemetría)
- ✅ Topic `driver_notifications` (respuestas)
- ✅ Función `process_kafka_requests()`
