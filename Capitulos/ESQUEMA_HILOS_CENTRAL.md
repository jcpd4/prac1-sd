# 🧵 ESQUEMA DE HILOS - CÓMO FUNCIONAN EN PARALELO

## ⚡ VISIÓN GENERAL

Cuando Central arranca, se inician **5 HILOS DIFERENTES** trabajando **AL MISMO TIEMPO**. Todos corren en paralelo.

---

## 📊 DIAGRAMA COMPLETO DE HILOS

```
═══════════════════════════════════════════════════════════════════════════════
                           PROGRAMA PRINCIPAL (Main Thread)
═══════════════════════════════════════════════════════════════════════════════

┌──────────────────────────────────────────────────────────────────────────────┐
│                          HILO PRINCIPAL                                      │
│                                                                              │
│  • Inicia: display_panel() [BLOQUEA AQUÍ - INFINITO]                       │
│  • Muestra panel cada 2 segundos                                            │
│  • NO puede hacer otra cosa (bloqueado)                                      │
└──────────────────────────────────────────────────────────────────────────────┘

                                   ⬇️ ⬇️ ⬇️ 
                        (Creó estos 5 hilos antes de bloquearse)

═══════════════════════════════════════════════════════════════════════════════
                              HILOS EN PARALELO
═══════════════════════════════════════════════════════════════════════════════

    HILO 1                HILO 2              HILO 3               HILO 4               HILO 5
┌─────────────┐    ┌──────────────┐       ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│kafka_thread │    │network_      │       │server_thread │    │input_thread  │    │cleanup_      │
│             │    │broadcast_    │       │              │    │              │    │thread        │
│             │    │thread        │       │              │    │              │    │              │
├─────────────┤    ├──────────────┤       ├──────────────┤    ├──────────────┤    ├──────────────┤
│FUNCIÓN:     │    │FUNCIÓN:      │       │FUNCIÓN:      │    │FUNCIÓN:      │    │FUNCIÓN:      │
│process_     │    │broadcast_    │       │start_        │    │process_      │    │cleanup_      │
│kafka_       │    │network_      │       │socket_       │    │user_input    │    │disconnected_ │
│requests     │    │status        │       │server        │    │              │    │drivers       │
├─────────────┤    ├──────────────┤       ├──────────────┤    ├──────────────┤    ├──────────────┤
│TIEMPO:      │    │TIEMPO:       │       │TIEMPO:       │    │TIEMPO:       │    │TIEMPO:       │
│CONTINUO     │    │5 segundos    │       │CONTINUO      │    │ON DEMAND     │    │30 segundos   │
├─────────────┤    ├──────────────┤       ├──────────────┤    ├──────────────┤    ├──────────────┤
│QUE HACE:    │    │QUE HACE:     │       │QUE HACE:     │    │QUE HACE:     │    │QUE HACE:     │
│• Escucha    │    │• Obtiene     │       │• Escucha     │    │• Espera      │    │• Revisa      │
│  mensajes   │    │  todos los   │       │  puerto 8000 │    │  comandos    │    │  drivers     │
│  de Kafka   │    │  CPs         │       │ (bind+listen)│    │  del usuario │    │  conectados  │
│• Recibe     │    │• Cada 5s     │       │• Cuando      │    │• Lee: P, R,  │    │• Cada 30s    │
│  pedidos    │    │  envía estado│       │  se conecta  │    │  PT, RT, Q   │    │  elimina     │
│  drivers    │    │  al topic    │       │  CP, crea    │    │• Procesa     │    │  drivers     │
│• Recibe     │    │  network_    │       │  hilo nuevo  │    │  comandos    │    │  inactivos   │
│  telemetría │    │  status      │       │  para él     │    │  PARAR/      │    │  (>60s)      │
│• Autoriza/  │    │              │       │              │    │  REANUDAR    │    │              │
│  deniega    │    │              │       │              │    │              │    │              │
│• Envía      │    │              │       │              │    │              │    │              │
│  notific    │    │              │       │              │    │              │    │              │
└─────────────┘    └──────────────┘       └──────────────┘    └──────────────┘    └──────────────┘
      ⬆️                   ⬆️                      ⬆️                   ⬆️                   ⬆️
      │                   │                       │                    │                    │
      └───────────────────┴───────────────────────┴────────────────────┴────────────────────┘
                                                       │
                                                       │
                                           Comparten: central_messages, driver_requests,
                                           shared_producer, active_cp_lock, etc.

```

---

## 🔄 COMUNICACIÓN ENTRE HILOS

### Variables Compartidas (Thread-Safe)

```
┌─────────────────────────────────────────────────────────────┐
│                  VARIABLES GLOBALES                         │
├─────────────────────────────────────────────────────────────┤
│ • central_messages = []     (Lista de mensajes)             │
│ • driver_requests = []      (Cola de pedidos)               │
│ • active_cp_sockets = {}    (CPs conectados)               │
│ • connected_drivers = set() (Drivers activos)               │
│ • current_sessions = {}     (Sesiones activas)               │
│ • shared_producer_ref       (Kafka producer)                │
│ • active_cp_lock             (Cerrojo de seguridad)         │
└─────────────────────────────────────────────────────────────┘
```

**¿Por qué hay un LOCK (active_cp_lock)?**
- Múltiples hilos modifican las mismas variables
- Sin lock → **Race Condition** (condición de carrera)
- Con lock → Solo un hilo modifica a la vez

**Ejemplo de Race Condition:**
```python
# HILO 1 (kafka_thread):
active_cp_sockets['MAD-01'] = socket1  # ← Escribe

# HILO 2 (server_thread) AL MISMO TIEMPO:
active_cp_sockets['MAD-01'] = socket2  # ← Escribe también

# Resultado: INCORRECTO, no sabemos cuál ganó
```

**Con Lock:**
```python
# HILO 1:
with active_cp_lock:  # 🔒 Cierra la puerta
    active_cp_sockets['MAD-01'] = socket1

# HILO 2: Espera a que HILO 1 termine

with active_cp_lock:  # 🔓 Ahora puedo entrar
    active_cp_sockets['MAD-01'] = socket2
```

---

## 🎬 FLUJO TEMPORAL (Qué pasa en cada segundo)

### Segundo 0 (Inicio)
```
HILO 1: Inicia escucha Kafka
HILO 2: Espera... (primer envío en 5s)
HILO 3: Inicia servidor en puerto 8000
HILO 4: Espera comando del usuario
HILO 5: Espera... (primera limpieza en 30s)
Panel: Muestra "No hay Puntos de Recarga"
```

### Segundo 1 (Arranca primer CP)
```
HILO 3: Recibe conexión → CP MAD-01 conecta
       → Crear nuevo hilo para CP MAD-01
       → CP envía: REGISTER#MAD-01#C/ Serrano 10#0.25
       → Actualiza BD, cambia estado a ACTIVADO

Panel: Refresca → Muestra MAD-01 en VERDE
```

### Segundo 2 (Driver solicita)
```
HILO 1: Recibe de Kafka: driver 101 quiere CP MAD-01
       → Verifica estado: ACTIVADO ✓
       → Autoriza → Envía AUTH_OK a driver
       → Cambia estado a RESERVADO

Panel: Refresca → Muestra MAD-01 en CYAN (RESERVADO)
```

### Segundo 5 (Anuncio de red)
```
HILO 2: Lee BD → Encuentra MAD-01
       → Envía a Kafka topic network_status:
       {
         "type": "NETWORK_STATUS_UPDATE",
         "cps": [{"id": "MAD-01", "status": "RESERVADO", ...}]
       }
       → Drivers lo reciben
```

### Segundo 30 (Limpieza)
```
HILO 5: Revisa drivers conectados
       → Driver 999 no envía hace 90s
       → Lo elimina de connected_drivers
       → Libera su asignación de CP
```

---

## 🎯 RESUMEN VISUAL: "¿QUÉ HACE CADA HILO?"

```
┌─────────────────────────────────────────────────────────────┐
│                    CENTRAL ARRANCA                          │
└─────────────────────────────────────────────────────────────┘
                            │
                ┌───────────┼───────────┬──────────┬──────────┐
                ▼           ▼           ▼          ▼          ▼
           ┌────────┐  ┌─────────┐ ┌─────────┐ ┌──────┐ ┌────────┐
           │KAFKA   │  │NETWORK  │ │SOCKET   │ │INPUT │ │CLEANUP │
           │THREAD  │  │BROADCAST│ │SERVER   │ │THREAD│ │THREAD  │
           │        │  │         │ │         │ │      │ │        │
           │∞ loop  │  │cada 5s  │ │∞ loop   │ │await │ │cada 30s│
           └────────┘  └─────────┘ └─────────┘ └──────┘ └────────┘
```

**Leyenda:**
- ∞ loop = Bucle infinito
- cada Xs = Se ejecuta cada X segundos
- await = Espera evento (input del usuario)

---

## ⏰ EJEMPLO PRÁCTICO: Escenario Completo

```
TIEMPO  Estado del Sistema
─────────────────────────────────────────────────────────────
0:00     ✅ 5 hilos iniciados
         └─ Esperando eventos

0:15     🔌 CP MAD-01 se conecta
         └─ HILO 3: Registra en BD → Estado: ACTIVADO

0:20     🚗 Driver 101 solicita MAD-01
         └─ HILO 1: Autoriza → Estado: RESERVADO

0:25     📢 HILO 2: Anuncia estado de red
         └─ Envía a topic network_status

0:45     ⚡ Driver 101 enchufa vehículo
         └─ Envía INIT → Estado: SUMINISTRANDO

1:45     📊 Panel: Muestra consumo 0.7 kWh, 0.14 €

2:15     🔴 CP reporta FALLT
         └─ HILO 1: Recibe → Estado: AVERIADO
         └─ Envía SUPPLY_ERROR a driver

2:30     🔵 HILO 2: Anuncia nuevo estado (AVERIADO)

3:30     🧹 HILO 5: Limpia driver desconectado (timeout)

∞        🔁 Se repite indefinidamente...
```

---

## 🎓 PREGUNTAS DE COMPRENSIÓN

### ¿Cuántos hilos corren en TOTAL en Central?
**Respuesta:** 5 hilos + 1 hilo por cada CP conectado

### ¿Qué pasa si el Panel se "congela"?
**Respuesta:** Significa que `display_panel()` se bloqueó. Es normal, es su comportamiento.

### ¿Por qué el Panel bloquea el programa?
**Respuesta:** Si no bloquea, el programa terminaría y todos los hilos se cerrarían.

### ¿Qué pasa si un hilo falla?
**Respuesta:** Los otros hilos siguen funcionando (son independientes).

---

## ✅ CONCEPTO CLAVE

**Todos los hilos trabajan SIMULTÁNEAMENTE y se comunican mediante:**
- Variables compartidas (con locks)
- Base de datos SQLite
- Topics de Kafka
- Sockets TCP/IP

**Ningún hilo espera a otro** → Son completamente independientes.

---

**¿Has entendido cómo funcionan los hilos en paralelo?** 🤔

Cuando estés listo, di **"Siguiente"** y continuamos con el CAPÍTULO 3: Sistema de Sockets 🚀
