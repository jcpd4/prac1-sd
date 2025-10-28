# 📚 TUTORIAL COMPLETO: Entendiendo el Código de EVCharging

## 🎓 Este es un curso progresivo para entender TODO el sistema

**Profesor:** Guía paso a paso  
**Estudiante:** Tú  
**Objetivo:** Entender cada línea de código

---

# CAPÍTULO 1: CONCEPTOS FUNDAMENTALES

## 1.1 ¿Qué es un Sistema Distribuido? (Analogía del Restaurante)

**Imagínate un restaurante:**
- 🍽️ **Meseros** (EV_Central) → Atienden pedidos
- 🔌 **Puntos de recarga** (EV_CP) → Cargan vehículos
- 🚗 **Clientes** (EV_Driver) → Solicitan recarga

**Características:**
- Están en **diferentes computadoras** (no en la misma)
- Se **comunican por red** (Internet/red local)
- Trabajan **al mismo tiempo** (en paralelo)

---

## 1.2 ¿Qué es un Hilo (Thread)? (Analogía del Chef)

**Imagínate un chef en un restaurante:**

**ANTES (Sin hilos):**
```
Chef cocina Plato 1 → Espera terminar → Cocina Plato 2 → Espera terminar
                      ↑
                 Chef bloqueado
```

**AHORA (Con hilos):**
```
Chef tiene 3 brazos (hilos):
- Brazo 1: Cocina plato principal
- Brazo 2: Cocina ensalada  
- Brazo 3: Prepara postre

TODO AL MISMO TIEMPO ✓
```

**En tu código:** Central hace MÚLTIPLES cosas al mismo tiempo:
- Escucha sockets de CPs
- Lee mensajes de Kafka
- Muestra el panel
- Lee comandos del usuario

---

## 1.3 ¿Qué es Kafka? (Analogía del Buzón)

**Imagínate un buzón postal:**

```
Emisor (Driver) → Buzón (Kafka) → Receptor (Central)
```

**Kafka = Sistema de mensajería:**
- Drivers **dejan** mensajes en el buzón (envían)
- Central **lee** mensajes del buzón (recibe)
- Los mensajes **se guardan** aunque nadie los lea aún

**Topics = Diferentes buzones:**
- `driver_requests` → Pedidos de drivers
- `cp_telemetry` → Datos de CPs
- `driver_notifications` → Respuestas para drivers

---

## 1.4 ¿Qué son los Sockets? (Analogía del Teléfono)

**Socket = Llamada telefónica directa**

```
CP llama a Central → Central contesta → Hablan
      ↓                    ↓
  [Socket]            [Socket]
```

**Características:**
- Conexión **directa** y **persistente**
- Se mantiene **abierta** mientras hablan
- Central **escucha** (bind + listen)
- CP **se conecta** (connect)
- Intercambian mensajes

---

# CAPÍTULO 2: ANATOMÍA DEL MAIN DE CENTRAL

## 2.1 El Punto de Entrada: `if __name__ == "__main__":`

**Línea 842:** `if __name__ == "__main__":`

**¿Qué significa?**
```python
# Si ejecutas: py ev_central.py 8000 127.0.0.1:9092
# Este bloque se ejecuta

# Si importas el archivo desde otro: import EV_Central
# Este bloque NO se ejecuta
```

---

## 2.2 Paso 1: Verificar Argumentos (Líneas 842-845)

```python
if len(sys.argv) < 3:
    print("Uso: python ev_central.py <puerto_socket> <kafka_broker_ip:port>")
    sys.exit(1)
```

**¿Qué hace?**
- Verifica que hay argumentos suficientes
- `sys.argv` = Lista de argumentos de línea de comandos
  - `sys.argv[0]` = `ev_central.py`
  - `sys.argv[1]` = `8000` (puerto)
  - `sys.argv[2]` = `127.0.0.1:9092` (kafka)

**Si faltan → Sale con error**

---

## 2.3 Paso 2: Extraer Argumentos (Líneas 848-850)

```python
SOCKET_PORT = int(sys.argv[1])      # 8000
KAFKA_BROKER = sys.argv[2]           # 127.0.0.1:9092
HOST = '0.0.0.0'                     # Escucha en todas las IPs
```

**¿Qué significa `HOST = '0.0.0.0'`?**
- `0.0.0.0` = "Escucha en todas las direcciones IP de esta máquina"
- Central acepta conexiones desde:
  - `localhost` (127.0.0.1)
  - IP de la red local
  - Cualquier interfaz de red

---

## 2.4 Paso 3: Variables Compartidas (Líneas 852-857)

```python
central_messages = ["CENTRAL system status OK"]
driver_requests = []
```

**¿Qué son?**
- **Listas compartidas** entre hilos
- **Visualización:**
  - `central_messages` → Lo que muestra el panel
  - `driver_requests` → Pedidos de drivers en cola

**¿Por qué son compartidas?**
- Múltiples hilos **escriben** en ellas
- El panel las **lee** constantemente

---

## 2.5 Paso 4: Crear el Producer de Kafka (Líneas 859-867)

```python
shared_producer = KafkaProducer(
    bootstrap_servers=[KAFKA_BROKER],
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)
shared_producer_ref = shared_producer
```

### ¿Qué es un Producer?
**Producer = Enviador de mensajes a Kafka**

**Desglosado:**
```python
# 1. bootstrap_servers=[KAFKA_BROKER]
#    → Dónde está Kafka: 127.0.0.1:9092

# 2. value_serializer=lambda v: json.dumps(v).encode('utf-8')
#    → Cómo convertir datos a bytes
#    Dict → JSON string → Bytes
```

### ¿Por qué "shared" (compartido)?
- ✅ Una sola conexión a Kafka
- ✅ Todos los hilos usan el mismo producer
- ✅ Más eficiente que crear uno por hilo

**Sin compartir (malo):**
```
Hilo 1: KafkaProducer() → Conexión 1
Hilo 2: KafkaProducer() → Conexión 2
Hilo 3: KafkaProducer() → Conexión 3
```
**Con compartir (bueno):**
```
Hilo 1 → shared_producer → 1 Conexión
Hilo 2 → shared_producer
Hilo 3 → shared_producer
```

---

## 2.6 Paso 5: Iniciar HILOS en Paralelo

### Hilo 1: kafka_thread (Líneas 870-873)
```python
kafka_thread = threading.Thread(
    target=process_kafka_requests, 
    args=(KAFKA_BROKER, central_messages, driver_requests, shared_producer)
)
kafka_thread.daemon = True
kafka_thread.start()
```

**¿Qué hace este hilo?**
- **Escucha** mensajes de Kafka
- **Recibe:**** Pedidos de drivers, telemetría de CPs
- **Procesa:**** Autoriza o deniega pedidos
- **Envía:**** Respuestas a drivers

**¿Por qué `daemon=True`?**
- Si el programa principal termina, este hilo también termina
- Sin daemon: El programa no terminaría nunca

**Analogía:** Trabajador que sigue trabajando mientras Central está abierto

---

### Hilo 2: network_broadcast_thread (Líneas 875-878)
```python
network_broadcast_thread = threading.Thread(
    target=broadcast_network_status, 
    args=(KAFKA_BROKER, shared_producer)
)
network_broadcast_thread.daemon = True
network_broadcast_thread.start()
```

**¿Qué hace este hilo?**
- **Cada 5 segundos:**
  1. Obtiene todos los CPs de la BD
  2. Envía el estado a un topic público
  3. Los drivers **reciben** este estado

**Ejemplo de mensaje:**
```json
{
  "type": "NETWORK_STATUS_UPDATE",
  "cps": [
    {"id": "MAD-01", "status": "ACTIVADO", "location": "C/ Serrano 10"},
    {"id": "BCN-02", "status": "DESCONECTADO", "location": "Las Ramblas 55"}
  ]
}
```

---

## 2.7 Paso 6: Inicializar Base de Datos (Línea 874)
```python
database.setup_database()
```
**Vamos a ver esta función después...**

---

## 2.8 Paso 7: Marcar CPs como DESCONECTADO (Líneas 877-885)

```python
all_cps_on_startup = database.get_all_cps()
if all_cps_on_startup:
    print("[CENTRAL] Restableciendo estado de CPs cargados a DESCONECTADO.")
    for cp in all_cps_on_startup:
        database.update_cp_status(cp['id'], 'DESCONECTADO')
```

**¿Qué hace?**
- Lee TODOS los CPs de la BD
- Los marca como DESCONECTADO
- **RAZÓN:** Central NO sabe el estado real hasta que el CP se conecte

**Analogía:**
- BD tiene registros antiguos
- Al arrancar, Central asume que están desconectados
- Solo cuando el CP se conecta, se actualiza

---

## 2.9 Paso 8: Iniciar Servidor de Sockets (Líneas 886-890)
```python
server_thread = threading.Thread(
    target=start_socket_server, 
    args=(HOST, SOCKET_PORT, central_messages, KAFKA_BROKER)
)
server_thread.daemon = True
server_thread.start()
```

**¿Qué hace este hilo?**
- **Escucha en puerto 8000** esperando conexiones
- Cuando un CP se conecta, crea un hilo nuevo para él
- **Espera** mensajes de:
  - `REGISTER#CP_ID#LOCATION#PRICE` → Registro
  - `FAULT#CP_ID` → Avería
  - `ACK#PARAR` → Confirmación

**Analogía:** Un portero que deja entrar a quienes llaman

---

## 2.10 Paso 9: Hilo de Comandos del Usuario (Líneas 913-917)
```python
input_thread = threading.Thread(target=process_user_input, args=(central_messages,))
input_thread.daemon = True
input_thread.start()
```

**¿Qué hace este hilo?**
- **Espera** a que el operador escriba comandos
- **Lee** comandos: `P <CP_ID>`, `R <CP_ID>`, `PT`, `RT`, `Q`
- **Procesa** comandos de PARAR/REANUDAR CPs

**Comandos disponibles:**
- `P <CP_ID>` o `PARAR <CP_ID>` → Parar un CP
- `R <CP_ID>` o `REANUDAR <CP_ID>` → Reanudar un CP
- `PT` o `PARAR_TODOS` → Parar todos los CPs
- `RT` o `REANUDAR_TODOS` → Reanudar todos los CPs
- `Q` o `QUIT` → Salir

**Ejemplo:**
```
> P MAD-01
[CENTRAL] Comando PARAR enviado a MAD-01
```

---

## 2.11 Paso 10: Hilo de Limpieza de Drivers (Líneas 919-923)
```python
cleanup_thread = threading.Thread(target=cleanup_disconnected_drivers)
cleanup_thread.daemon = True
cleanup_thread.start()
```

**¿Qué hace este hilo?**
- **Cada 30 segundos:**
  1. Revisa qué drivers están conectados
  2. Busca drivers que no han enviado peticiones en 60 segundos
  3. Los elimina de la lista de `connected_drivers`
  4. Libera sus asignaciones de CPs

**Por qué es necesario:**
- Si un driver se desconecta bruscamente (cae el programa)
- Central no se enteraría sin este hilo
- Se limpia automáticamente la lista de drivers activos

---

## 2.12 Paso 11: Panel de Monitorización (Línea 925)
```python
display_panel(central_messages, driver_requests)
```

**¿Qué hace?**
- Bucle infinito que muestra el estado del sistema
- Se refresca cada 2 segundos
- **Muestra:**
  - Tabla de CPs (con colores)
  - Drivers conectados
  - Pedidos en cola
  - Mensajes del sistema
  - Comandos disponibles

**Nota:** Es la única función que NO corre en hilo
- Bloquea el programa principal
- Mantiene Central "vivo" y mostrando el panel

---

# RESUMEN DEL ARRANQUE DE CENTRAL

```
Paso 1: Verifica argumentos (línea 847)
Paso 2: Extrae puerto y Kafka broker (líneas 853-855)
Paso 3: Crea variables compartidas (líneas 858-859)
Paso 4: Crea producer de Kafka compartido (líneas 862-867)
Paso 5.1: Inicia hilo para consumir mensajes de Kafka (líneas 876-878)
Paso 5.2: Inicia hilo para anunciar estado de red (líneas 885-887)
Paso 6: Configura base de datos (línea 891)
Paso 7: Marca CPs como DESCONECTADO (líneas 896-900)
Paso 8: Inicia hilo servidor de sockets (líneas 909-911)
Paso 9: Inicia hilo de comandos del usuario (líneas 915-917)
Paso 10: Inicia hilo de limpieza de drivers (líneas 921-923)
Paso 11: Muestra panel (bloquea aquí) (línea 937)
```

**Total de hilos iniciados:**
- ✅ `kafka_thread` - Consume mensajes de Kafka
- ✅ `network_broadcast_thread` - Anuncia estado de red
- ✅ `server_thread` - Escucha sockets de CPs
- ✅ `input_thread` - Lee comandos del usuario
- ✅ `cleanup_thread` - Limpia drivers desconectados
- ⏸️ Panel se ejecuta en hilo principal (bloquea)

---

---

# CAPÍTULO 3: Sistema de Sockets ✅ COMPLETADO

📄 **Ver archivo:** `CAPITULO_3_SOCKETS.md`

**Contenido:**
- ✅ ¿Qué es un Socket?
- ✅ Función `start_socket_server()` - Portero que escucha
- ✅ Función `handle_client()` - Atiende cada CP
- ✅ Función `process_socket_data2()` - Procesa mensajes
- ✅ Ejemplo práctico completo
- ✅ Conceptos de Locks

---

# CAPÍTULO 4: Sistema de Kafka ✅ COMPLETADO

📄 **Ver archivo:** `CAPITULO_4_KAFKA.md`

**Contenido:**
- ✅ Topics: driver_requests, cp_telemetry, driver_notifications, network_status
- ✅ Producers y Consumers en Central, Driver y Engine
- ✅ Serialización/Deserialización JSON
- ✅ Flujos completos: Solicitud, Consumo, Ticket, Avería
- ✅ Producer compartido en Central
- ✅ Diagrama de flujo resumen

---

# CAPÍTULO 5: Base de Datos SQLite ✅ COMPLETADO

📄 **Ver archivo:** `CAPITULO_5_BASE_DATOS.md`

**Contenido:**
- ✅ SQLite embebido: archivo único ev_charging.db
- ✅ 3 tablas: charging_points, drivers, transactions
- ✅ Thread safety con db_lock
- ✅ Estados de CPs: DESCONECTADO → ACTIVADO → RESERVADO → SUMINISTRANDO
- ✅ Funciones CRUD: register_cp, update_cp_status, get_all_cps
- ✅ Gestión de consumo: update_cp_consumption, clear_cp_consumption
- ✅ Integración con panel de Central
- ✅ Manejo de errores y fallback

---

# CAPÍTULO 6: Panel de Monitorización ✅ COMPLETADO

📄 **Ver archivo:** `CAPITULO_6_PANEL.md`

**Contenido:**
- ✅ Estructura y fuentes de datos del panel
- ✅ Colores ANSI por estado (incluye RESERVADO)
- ✅ Secciones: CPs, Drivers conectados, Peticiones, Mensajes, Comandos
- ✅ Relación con BD y Kafka
- ✅ Concurrencia: solo lectura + locks donde aplica
- ✅ Posibles mejoras

---

# CAPÍTULO 7: Locks y Sincronización ✅ COMPLETADO

📄 **Ver archivo:** `CAPITULO_7_LOCKS.md`

**Contenido:**
- ✅ Qué es threading.Lock y por qué se necesita
- ✅ 3 locks principales: active_cp_lock, status_lock, db_lock
- ✅ Patrón `with` para adquisición/liberación automática
- ✅ Casos de uso reales en Central, Engine y BD
- ✅ Condiciones de carrera y cómo evitarlas
- ✅ Buenas prácticas: granularidad, duración mínima
- ✅ Ejemplos completos de flujos de autorización
- ✅ Tabla resumen de locks por módulo

---

# CAPÍTULO 8: Driver - Aplicación del Conductor ✅ COMPLETADO

📄 **Ver archivo:** `CAPITULO_8_DRIVER.md`

**Contenido:**
- ✅ Arquitectura del Driver: 4 hilos concurrentes
- ✅ Comunicación Kafka: Producer + 2 Consumers
- ✅ Estado compartido: network_status, active_charge_info
- ✅ Panel visual dinámico con estado de recarga
- ✅ Comandos: SOLICITAR (manual), BATCH (automático)
- ✅ Filtrado inteligente de mensajes
- ✅ Manejo de errores y interrupciones
- ✅ Flujo completo de recarga paso a paso
- ✅ Integración con Central y Kafka
- ✅ Ejemplos de uso y buenas prácticas

---

# CAPÍTULO 9: Engine - Motor del Punto de Recarga ✅ COMPLETADO

📄 **Ver archivo:** `CAPITULO_9_ENGINE.md`

**Contenido:**
- ✅ Arquitectura del Engine: 3 hilos concurrentes
- ✅ Comunicación: Sockets con Monitor, Kafka con Central
- ✅ Estado compartido: ENGINE_STATUS con locks
- ✅ Simulación de recarga con telemetría cada segundo
- ✅ Comandos: FAIL, RECOVER, INIT, END
- ✅ Problemas identificados: panel duplicado, funciones no usadas
- ✅ Flujo completo de autorización e inicio de recarga
- ✅ Integración con Monitor y Central
- ✅ Ejemplos de uso y código optimizado propuesto

---

# PRÓXIMOS CAPÍTULOS
- Capítulo 10: Flujo Completo del Sistema (end-to-end)
- Capítulo 11: Troubleshooting y Debugging
- Capítulo 12: Mejoras y Optimizaciones

¿Con cuál seguimos? 🎓
