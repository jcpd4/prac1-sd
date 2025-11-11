# Documentación del Protocolo de Comunicación por Sockets
## Protocolo `<STX><DATA><ETX><LRC>`

### 1. Introducción

Este documento describe el protocolo de comunicación por sockets implementado en el sistema EV Charging Network. El protocolo está basado en tramas bien formadas según el estándar recomendado `<STX><DATA><ETX><LRC>` y proporciona detección de errores mediante verificación de redundancia longitudinal (LRC).

---

## 2. Arquitectura de Comunicación

El sistema utiliza este protocolo en todas las comunicaciones síncronas por sockets:

```
┌─────────────┐                    ┌─────────────┐
│   CENTRAL   │◄───Sockets─────────►│    CP_M     │
│             │                    │  (Monitor)  │
│             │                    │             │
│             │                    │             │
│             │                    └──────┬──────┘
│             │                           │
│             │                    ┌──────▼──────┐
│             │                    │    CP_E     │
│             │                    │  (Engine)   │
│             │                    └─────────────┘
└─────────────┘
```

### 2.1 Canales de Comunicación con Protocolo

1. **Central ↔ Monitor (CP_M)**: Comunicación principal de control y monitorización
2. **Monitor (CP_M) ↔ Engine (CP_E)**: Comunicación local para salud y comandos

### 2.2 Canales de Comunicación SIN Protocolo

1. **Drivers ↔ Central**: Kafka (asíncrono, JSON)
2. **Engine ↔ Central**: Kafka (telemetría, asíncrono)

---

## 3. Especificación Técnica del Protocolo

### 3.1 Constantes del Protocolo

| Constante | Valor Hex | Nombre | Descripción |
|-----------|-----------|--------|-------------|
| `STX` | `0x02` | Start of Text | Inicio de trama de datos |
| `ETX` | `0x03` | End of Text | Fin de trama de datos |
| `ENQ` | `0x05` | Enquiry | Solicitud de handshake inicial |
| `ACK` | `0x06` | Acknowledgement | Confirmación positiva |
| `NACK` | `0x15` | Negative Acknowledgement | Confirmación negativa o error |
| `EOT` | `0x04` | End of Transmission | Fin de transmisión (cierre de sesión) |

### 3.2 Formato de Trama

Cada mensaje de datos sigue el formato:

```
<STX><DATA><ETX><LRC>
```

Donde:
- **STX** (1 byte): `0x02` - Marca el inicio de la trama
- **DATA** (N bytes): Contenido del mensaje en UTF-8
- **ETX** (1 byte): `0x03` - Marca el fin de los datos
- **LRC** (1 byte): Checksum calculado como XOR de todos los bytes (STX + DATA + ETX)

### 3.3 Estructura de DATA

Los datos dentro de la trama siguen el formato:

```
CÓDIGO_OPERACIÓN#campo1#campo2#...#campoN
```

Ejemplos:
- `REGISTER#MAD-01#Calle Mayor 10#0.25`
- `FAULT#MAD-01`
- `ACK#PARAR`
- `HEALTH_CHECK#MAD-01`

### 3.4 Cálculo del LRC

El LRC (Longitudinal Redundancy Check) se calcula mediante la operación XOR byte a byte:

```python
lrc = 0
for byte in message_bytes:  # message_bytes = STX + DATA + ETX
    lrc ^= byte
```

**Ejemplo:**
```
Mensaje: REGISTER#MAD-01
STX = 0x02
DATA = 0x52 0x45 0x47 0x49 0x53 0x54 0x45 0x52 0x23 0x4D 0x41 0x44 0x2D 0x30 0x31
ETX = 0x03

LRC = 0x02 ^ 0x52 ^ 0x45 ^ ... ^ 0x03
```

---

## 4. Flujo de Comunicación

### 4.1 Handshake Inicial (Establecimiento de Conexión)

Cada conexión socket comienza con un handshake:

```
CLIENTE                    SERVIDOR
   │                          │
   │ ────ENQ (0x05)─────────►│
   │                          │
   │ ◄────ACK (0x06)──────────│
   │                          │
```

**Pasos:**
1. Cliente envía `ENQ` (1 byte: `0x05`)
2. Servidor responde `ACK` (1 byte: `0x06`) si acepta la conexión
3. Servidor responde `NACK` (1 byte: `0x15`) si rechaza la conexión
4. Si el handshake falla, la conexión se cierra

### 4.2 Intercambio de Mensajes

Después del handshake exitoso, el intercambio sigue este patrón:

```
CLIENTE                    SERVIDOR
   │                          │
   │ ──<STX>REQUEST<ETX><LRC>►│
   │                          │
   │ ◄────ACK/NACK─────────────│
   │                          │
   │ ◄─<STX>ANSWER<ETX><LRC>───│
   │                          │
   │ ─────ACK/NACK───────────►│
   │                          │
```

**Pasos detallados:**

1. **Cliente envía REQUEST:**
   - Construye trama: `<STX><REQUEST><ETX><LRC>`
   - Envía por socket
   - Espera ACK/NACK del servidor

2. **Servidor valida REQUEST:**
   - Recibe trama
   - Verifica STX (primer byte = 0x02)
   - Busca ETX en la trama
   - Extrae DATA y LRC
   - Calcula LRC esperado
   - Compara LRC recibido vs esperado
   - Si válido: responde ACK, procesa REQUEST
   - Si inválido: responde NACK, ignora REQUEST

3. **Servidor envía ANSWER:**
   - Construye trama: `<STX><ANSWER><ETX><LRC>`
   - Envía por socket
   - Espera ACK/NACK del cliente

4. **Cliente valida ANSWER:**
   - Mismo proceso de validación que en paso 2
   - Responde ACK si válido, NACK si inválido

### 4.3 Cierre de Conexión

```
CLIENTE                    SERVIDOR
   │                          │
   │ ────EOT (0x04)──────────►│
   │                          │
   │ ◄────Close()──────────────│
   │                          │
```

**Nota:** El cierre puede ser explícito (EOT) o implícito (socket cerrado por error).

---

## 5. Flujos de Comunicación Específicos

### 5.1 Registro de Charging Point (CP_M → Central)

```
CP_M (Cliente)              Central (Servidor)
   │                             │
   │ ──────ENQ──────────────────►│
   │ ◄─────ACK───────────────────│
   │                             │
   │ ─<STX>REGISTER#...<ETX><LRC>►│
   │ ◄─────ACK───────────────────│
   │                             │
```

**Datos enviados:** `REGISTER#CP_ID#LOCATION#PRICE`

### 5.2 Health Check (CP_M → CP_E)

```
CP_M (Cliente)              CP_E (Servidor)
   │                             │
   │ ──────ENQ──────────────────►│
   │ ◄─────ACK───────────────────│
   │                             │
   │ ─<STX>HEALTH_CHECK#...<ETX>►│
   │ ◄─────ACK───────────────────│
   │                             │
   │ ◄<STX>OK<ETX><LRC>───────────│
   │ ──────ACK──────────────────►│
   │                             │
```

**Datos enviados:** `HEALTH_CHECK#CP_ID`  
**Datos recibidos:** `OK` o `KO`

### 5.3 Comando PARAR (Central → CP_M → CP_E)

```
Central                 CP_M                    CP_E
   │                     │                       │
   │ ─<STX>PARAR#...<ETX>►│                       │
   │ ◄─────ACK─────────────│                       │
   │                     │ ────ENQ──────────────►│
   │                     │ ◄────ACK──────────────│
   │                     │ ─<STX>PARAR<ETX><LRC>►│
   │                     │ ◄────ACK──────────────│
   │                     │ ◄<STX>ACK#PARAR<ETX>──│
   │                     │ ─────ACK─────────────►│
   │ ◄<STX>ACK#PARAR<ETX>│                       │
   │ ──────ACK───────────►│                       │
```

### 5.4 Reporte de Avería (CP_M → Central)

```
CP_M (Cliente)              Central (Servidor)
   │                             │
   │ ─<STX>FAULT#CP_ID<ETX><LRC>►│
   │ ◄─────ACK───────────────────│
   │                             │
```

**Nota:** Esta comunicación utiliza la conexión persistente ya establecida durante el registro.

---

## 6. Manejo de Errores

### 6.1 Errores Detectados por LRC

Si el LRC no coincide:
- El receptor responde `NACK`
- La trama se descarta
- Se registra el error en los logs
- La conexión puede continuar (se espera retransmisión)

### 6.2 Errores de Timeout

- Si el handshake no se completa en 5 segundos → conexión rechazada
- Si la recepción de trama excede el timeout → error reportado

### 6.3 Errores de Formato

- Trama demasiado corta (< 4 bytes) → NACK
- STX no encontrado → NACK
- ETX no encontrado → NACK
- Datos no decodificables como UTF-8 → NACK

---

## 7. Implementación en el Código

### 7.1 Funciones Clave

Todos los archivos (`EV_Central.py`, `EV_CP_M.py`, `EV_CP_E.py`) implementan:

1. **`calculate_lrc(message_bytes)`**: Calcula el checksum
2. **`build_frame(data_string)`**: Construye trama completa
3. **`parse_frame(frame_bytes)`**: Parsea y valida trama recibida
4. **`send_frame(socket_ref, data_string)`**: Envía trama por socket
5. **`receive_frame(socket_ref, timeout)`**: Recibe y valida trama
6. **`handshake_client(socket_ref)`**: Handshake lado cliente
7. **`handshake_server(socket_ref)`**: Handshake lado servidor
8. **`send_ack(socket_ref)` / `send_nack(socket_ref)`**: Envío de confirmaciones

### 7.2 Ejemplo de Uso

**Envío de mensaje:**
```python
# Construir y enviar trama
send_frame(socket, "REGISTER#MAD-01#Calle Mayor#0.25")
# Esperar ACK de confirmación
ack = socket.recv(1)
if ack == ACK:
    print("Mensaje confirmado")
```

**Recepción de mensaje:**
```python
# Recibir y validar trama
data, is_valid = receive_frame(socket)
if is_valid:
    print(f"Recibido: {data}")
    send_ack(socket)  # Confirmar recepción válida
else:
    send_nack(socket)  # Indicar error
```

---

## 8. Ventajas del Protocolo

1. **Detección de Errores**: El LRC detecta errores de transmisión
2. **Validación de Integridad**: Verifica que los datos llegaron completos
3. **Confirmaciones**: ACK/NACK proporcionan feedback inmediato
4. **Estándar Industrial**: Basado en protocolos ampliamente utilizados
5. **Extensible**: Fácil añadir nuevos tipos de mensajes

---

## 9. Logs y Depuración

Todos los mensajes del protocolo se registran en consola con el prefijo `[PROTOCOLO]`:

```
[PROTOCOLO] Trama construida: STX + 'REGISTER#MAD-01#...' + ETX + LRC=3A
[PROTOCOLO] Trama enviada correctamente: 'REGISTER#MAD-01#...'
[PROTOCOLO] Trama parseada correctamente: 'REGISTER#MAD-01#...' (LRC válido: 0x3A)
[PROTOCOLO] ACK enviado
```

**Formato de logs:**
- `[PROTOCOLO]` - Mensajes generales
- `[PROTOCOLO ENGINE]` - Mensajes específicos del Engine
- `ERROR` - Errores detectados

---

## 10. Resumen de Flujos Completos

### Flujo 1: Conexión y Registro de CP

```
1. CP_M conecta a Central
2. Handshake ENQ/ACK
3. CP_M envía REGISTER#CP_ID#LOCATION#PRICE
4. Central valida, responde ACK
5. Conexión persistente establecida
```

### Flujo 2: Health Check Continuo

```
1. CP_M conecta a CP_E (cada 1 segundo)
2. Handshake ENQ/ACK
3. CP_M envía HEALTH_CHECK#CP_ID
4. CP_E responde OK o KO
5. CP_M confirma recepción
6. Conexión se cierra
7. Se repite cada segundo
```

### Flujo 3: Comando de Control

```
1. Central envía comando a CP_M (conexión persistente)
2. CP_M valida y responde ACK
3. CP_M conecta a CP_E
4. Handshake ENQ/ACK
5. CP_M reenvía comando a CP_E
6. CP_E procesa y responde ACK/NACK
7. CP_M reenvía respuesta a Central
8. Central confirma
```

---

## Apéndice A: Tabla de Códigos de Operación

| Código | Dirección | Descripción |
|--------|-----------|-------------|
| `REGISTER` | CP_M → Central | Registro de nuevo CP |
| `FAULT` | CP_M → Central | Reporte de avería |
| `RECOVER` | CP_M → Central | Reporte de recuperación |
| `ACK#COMANDO` | Bidireccional | Confirmación de comando |
| `NACK#COMANDO` | Bidireccional | Rechazo de comando |
| `PARAR#CENTRAL` | Central → CP_M | Orden de parar CP |
| `REANUDAR#CENTRAL` | Central → CP_M | Orden de reanudar CP |
| `AUTORIZAR_SUMINISTRO#DRIVER_ID` | Central → CP_M → CP_E | Autorizar suministro |
| `HEALTH_CHECK#CP_ID` | CP_M → CP_E | Consulta de salud |
| `CHECK_SESSION#CP_ID` | CP_E → CP_M → Central | Consulta de sesión activa |
| `CHECK_DRIVER#CP_ID` | CP_E → CP_M → Central | Consulta de driver asignado |

---

**Versión del Documento:** 1.0  
**Fecha:** 2025  
**Autor:** Implementación práctica SD Sistemas Distribuidos

