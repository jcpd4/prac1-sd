# PENDIENTES POR IMPLEMENTAR - EV Charging Network

Fecha de creación: $(date)
Estado del proyecto: En desarrollo para Release 1

---

## 🔴 CRÍTICO (Alta Prioridad)

### 1. Protocolo de Sockets Recomendado `<STX><DATA><ETX><LRC>` ⚠️

**Estado actual**: No implementado  
**Impacto**: Puntos en sección "General" (hasta 2 puntos totales)  
**Ubicación**: Todas las comunicaciones por sockets

**Descripción**:  
Actualmente se usan mensajes en texto plano con separador `#` (ej: `REGISTER#CP_ID#LOCATION`).  
Debería implementarse el protocolo recomendado basado en tramas bien formadas.

**Especificación del protocolo**:
```
Formato de trama: <STX><DATA><ETX><LRC>

Donde:
- STX (Start of Text) = 0x02
- ETX (End of Text) = 0x03  
- LRC (Longitudinal Redundancy Check) = XOR(MESSAGE) byte a byte
- DATA = Código Operación#campo1#...#campo n

Secuencia de comunicación:
1. Conexión establecida
2. Cliente → Servidor: <ENQ> (Enquiry, 0x05)
3. Servidor → Cliente: <ACK> (0x06) o <NACK> (0x15)
4. Cliente → Servidor: <STX><REQUEST><ETX><LRC>
5. Servidor → Cliente: <ACK> o <NACK> (basado en validación LRC)
6. Servidor → Cliente: <STX><ANSWER><ETX><LRC>
7. Cliente → Servidor: <ACK> o <NACK>
8. Cliente → Servidor: <EOT> (End of Transmission, 0x04)
9. Cierre de conexión
```

**Archivos a modificar**:
- `EV_Central.py`: Función `handle_client()` y `process_socket_data2()`
- `EV_CP_M.py`: Todas las funciones que envían/reciben por socket
- `EV_CP_E.py`: Función `handle_monitor_connection()`

**Funciones auxiliares necesarias**:
```python
def calculate_lrc(message_bytes):
    """Calcula el LRC (XOR de todos los bytes)."""
    lrc = 0
    for byte in message_bytes:
        lrc ^= byte
    return lrc

def build_frame(data_string):
    """Construye una trama <STX><DATA><ETX><LRC>."""
    stx = bytes([0x02])
    etx = bytes([0x03])
    data = data_string.encode('utf-8')
    message = stx + data + etx
    lrc = calculate_lrc(message)
    return message + bytes([lrc])

def parse_frame(frame_bytes):
    """Parsea una trama y valida el LRC."""
    if len(frame_bytes) < 4:  # STX + al menos 1 byte DATA + ETX + LRC
        return None, False
    
    if frame_bytes[0] != 0x02:  # STX
        return None, False
    
    # Buscar ETX
    etx_pos = -1
    for i in range(1, len(frame_bytes) - 1):
        if frame_bytes[i] == 0x03:  # ETX
            etx_pos = i
            break
    
    if etx_pos == -1:
        return None, False
    
    data_bytes = frame_bytes[1:etx_pos]
    received_lrc = frame_bytes[etx_pos + 1]
    
    # Calcular LRC esperado
    expected_lrc = calculate_lrc(bytes([0x02]) + data_bytes + bytes([0x03]))
    
    if received_lrc != expected_lrc:
        return None, False  # LRC no coincide
    
    data = data_bytes.decode('utf-8')
    return data, True
```

---

## 🟡 IMPORTANTE (Media Prioridad)

### 2. Resiliencia: Manejo de Caída de Driver durante Suministro

**Estado actual**: Verificar implementación  
**Impacto**: Puntos en sección "Resiliencia" (hasta 3 puntos)

**Requisito según guía de corrección**:
> "Un Driver se cierra mientras se le está prestando un servicio: El servicio sigue su curso. Cuando el cliente se recupera verá el resultado de su servicio."

**Verificación necesaria**:
- [ ] Verificar que si un Driver se desconecta (Ctrl+C o cierre) durante un suministro, el CP continúa el suministro
- [ ] Verificar que cuando el Driver se reconecta, puede consultar/recibir el ticket de servicios anteriores
- [ ] Implementar almacenamiento de tickets pendientes para drivers desconectados en la BD

**Archivos a revisar**:
- `EV_Central.py`: Función `cleanup_disconnected_drivers()` y lógica de tickets
- `database.py`: Agregar tabla `pending_tickets` para drivers desconectados
- `EV_Driver.py`: Al conectar, consultar tickets pendientes

---

### 3. Resiliencia: Manejo de Caída de Central durante Suministro

**Estado actual**: Verificar implementación  
**Impacto**: Puntos en sección "Resiliencia"

**Requisito según guía de corrección**:
> "La Central: Los CP siguen prestando su servicio hasta que lo finalicen momento en el cual se paran si la central no se ha restaurado. No será posible admitir nuevas peticiones de servicios."

**Verificación necesaria**:
- [ ] Si la Central cae durante un suministro, el CP debe finalizar el suministro actual
- [ ] El CP no debe aceptar nuevos suministros manuales mientras la Central esté caída
- [ ] Cuando la Central se recupere, debe recibir el estado final del suministro
- [ ] Los CPs deben detectar la pérdida de conexión con Central

**Archivos a revisar**:
- `EV_CP_E.py`: Detectar desconexión de Kafka/Central y comportarse según especificación
- `EV_CP_M.py`: Detectar pérdida de conexión por socket con Central
- `EV_Central.py`: Al recuperarse, solicitar estado de suministros pendientes a CPs

---

### 4. Lectura de Archivo de Servicios en Driver

**Estado actual**: Verificar implementación  
**Impacto**: Funcionalidad base requerida

**Requisito según especificación**:
> "la aplicación del conductor también podrá leer los servicios de recarga a solicitar desde un archivo con el siguiente formato:
```
<ID_CP>
<ID_CP>
…
```

**Verificación necesaria**:
- [ ] El Driver puede leer desde línea de comandos un archivo con IDs de CPs
- [ ] El Driver procesa servicios uno por uno
- [ ] Espera 4 segundos entre servicios consecutivos
- [ ] El archivo debe contener al menos 10 servicios para las pruebas (según guía de corrección)

**Archivo a revisar**:
- `EV_Driver.py`: Función `main()` o similar para procesar archivo de servicios

---

### 5. Espera de 4 Segundos entre Servicios

**Estado actual**: Verificar implementación  
**Impacto**: Funcionalidad base requerida

**Requisito según especificación**:
> "Cuando un suministro concluya, si dicho conductor precisa de otro servicio (tiene más registros en su fichero) el sistema esperará 4 segundos y procederá a solicitar un nuevo servicio."

**Verificación necesaria**:
- [ ] Después de recibir TICKET o SUPPLY_ERROR, esperar 4 segundos antes de solicitar siguiente servicio
- [ ] Aplicar solo si hay más servicios pendientes en el archivo

**Archivo a revisar**:
- `EV_Driver.py`: Lógica de procesamiento de servicios consecutivos

---

### 6. Simulación de Avería en Engine

**Estado actual**: Verificar implementación  
**Impacto**: Funcionalidad base requerida

**Requisito según especificación**:
> "Para simular dichas incidencias, la aplicación EV_CP_E deberá permitir que, en tiempo de ejecución, se pulse una tecla para reportar un KO al monitor."

**Verificación necesaria**:
- [ ] Presionar tecla (ej: 'F' para FAIL) cambia el estado del Engine a KO
- [ ] El Monitor detecta el KO y envía FAULT a Central
- [ ] Presionar otra tecla (ej: 'R' para RECOVER) restaura el estado a OK
- [ ] El Monitor detecta la recuperación y envía RECOVER a Central

**Archivo a revisar**:
- `EV_CP_E.py`: Función de entrada de teclado y cambio de estado ENGINE_STATUS['health']

---

## 🟢 MEJORAS (Baja Prioridad - Opcionales)

### 7. Almacenamiento de Tickets Pendientes

**Descripción**: Si un Driver se desconecta durante un suministro, guardar el ticket final en la BD para que pueda recuperarlo al reconectarse.

**Implementación sugerida**:
- Agregar tabla `pending_tickets` en `database.py`
- Al generar ticket, verificar si el driver está conectado
- Si no está conectado, guardar en `pending_tickets`
- Al reconectar, el Driver consulta tickets pendientes

---

### 8. Persistencia de Estado de Suministros Interrumpidos

**Descripción**: Si un CP se desconecta durante un suministro, guardar el estado parcial para poder finalizarlo cuando se reconecte.

**Implementación sugerida**:
- En `database.py`, tabla `interrupted_sessions`
- Al detectar desconexión durante suministro, guardar estado parcial
- Al reconectar, verificar si hay sesión interrumpida y finalizarla

---

### 9. Mejora en Detección de Desconexión de Central

**Descripción**: Implementar heartbeat/ping desde CP a Central para detectar desconexión más rápidamente.

**Implementación sugerida**:
- En `EV_CP_M.py`, enviar heartbeat cada X segundos a Central
- Si no hay respuesta en Y intentos, considerar Central caída
- Comportarse según especificación (finalizar suministro actual, no aceptar nuevos)

---

## 📋 CHECKLIST DE VERIFICACIÓN PRE-CORRECCIÓN

Antes de la corrección en laboratorio, verificar:

### Despliegue
- [ ] Sistema funciona en 3 ordenadores distintos
- [ ] Todos los parámetros son configurables (no hardcodeados)
- [ ] Se pueden desplegar múltiples instancias de CPs y Drivers
- [ ] El sistema puede iniciarse sin errores en todas las máquinas

### Funcionalidad Base
- [ ] Central muestra panel de monitorización con colores correctos
- [ ] CPs se registran correctamente
- [ ] Drivers solicitan servicios y reciben autorización/denegación
- [ ] Suministro funciona y muestra consumo en tiempo real
- [ ] Ticket final se genera y entrega correctamente
- [ ] Comandos P/R funcionan (parar/reanudar CPs)
- [ ] Comandos PT/RT funcionan (parar/reanudar todos)
- [ ] Monitor detecta averías y las reporta a Central
- [ ] Driver puede leer archivo con múltiples servicios (>10)
- [ ] Espera de 4 segundos entre servicios funciona

### Resiliencia
- [ ] Si Monitor cae → CP se marca DESCONECTADO, suministro finaliza si Engine también cae
- [ ] Si Engine cae → Monitor envía FAULT, Central marca AVERIADO
- [ ] Si Driver cae durante suministro → suministro continúa
- [ ] Si Central cae durante suministro → CP finaliza suministro, no acepta nuevos
- [ ] Reconexión de componentes funciona correctamente

### Protocolo
- [ ] (Opcional pero recomendado) Protocolo `<STX><DATA><ETX><LRC>` implementado
- [ ] Handshake ENQ/ACK/NACK funciona
- [ ] Validación LRC funciona correctamente

---

## 📝 NOTAS ADICIONALES

1. **Protocolo de Sockets**: Aunque es "recomendado" y no estrictamente obligatorio, su implementación se valora positivamente en la sección "General" y puede marcar la diferencia en la calificación final.

2. **Base de datos**: Ya está implementada con SQLite y fallback a diccionarios. Verificar que todas las funciones necesarias están implementadas.

3. **Interfaz de usuario**: Verificar que todos los mensajes se muestran claramente en pantalla tanto en Central como en Drivers y CPs.

4. **Archivo de servicios del Driver**: Debe contener al menos 10 servicios para las pruebas durante la corrección.

5. **Documentación**: Recordar actualizar la memoria con los cambios realizados.

---

**Última actualización**: $(date)  
**Próxima revisión**: Antes de la corrección en laboratorio

