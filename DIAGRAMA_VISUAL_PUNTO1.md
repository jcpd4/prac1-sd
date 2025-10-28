# DIAGRAMA VISUAL - FLUJO PUNTO 1

## 🖥️ INTERFAZ DEL PANEL (Cada 2 segundos)

```
┌─────────────────────────────────────────────────────────────┐
│       --- PANEL DE MONITORIZACIÓN DE EV CHARGING ---        │
│==============================================================│
│ ID        │ UBICACIÓN            │ PRECIO    │ ESTADO       │
│-----------├--------------------- │-----------│--------------│
│ MAD-01    │ C/ Serrano 10        │ 0.25 €/kWh│ [GRIS]       │
│           │                      │           │ DESCONECTADO │
│-----------├--------------------- │-----------│--------------│
│ BCN-02    │ Las Ramblas 55       │ 0.25 €/kWh│ [GRIS]       │
│           │                      │           │ DESCONECTADO │
│==============================================================│
│                                                                │
│ *** DRIVERS CONECTADOS ***                                    │
│ No hay drivers conectados.                                    │
│                                                                │
│ *** PETICIONES DE CONDUCTORES (Kafka) ***                     │
│ No hay peticiones pendientes.                                 │
│                                                                │
│ *** MENSAJES DEL SISTEMA ***                                  │
│ CENTRAL system status OK                                      │
│                                                                │
│==============================================================│
│ Comandos: [P]arar <CP_ID> │ [R]eanudar <CP_ID> │ [Q]uit      │
│ Última actualización: 2025-XX-XX XX:XX:XX                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔄 FLUJO COMPLETO DEL PUNTO 1

```
╔═══════════════════════════════════════════════════════════════╗
║                    COMANDO DE EJECUCIÓN                       ║
║                  py ev_central.py 8000 127.0.0.1:9092         ║
╚═══════════════════════════════════════════════════════════════╝
                              │
                              ▼
        ┌─────────────────────────────────────┐
        │  1. VERIFICACIÓN ARGUMENTOS         │
        │  SOCKET_PORT = 8000                  │
        │  KAFKA_BROKER = 127.0.0.1:9092      │
        └─────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────┐
        │  2. INICIALIZACIÓN VARIABLES         │
        │  central_messages = []               │
        │  driver_requests = []                 │
        │  shared_producer = KafkaProducer()   │
        └─────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────┐
        │  3. INICIAR HILOS (DAEMON)          │
        │  ├─ kafka_thread                     │
        │  ├─ network_broadcast_thread        │
        │  ├─ server_thread                    │
        │  ├─ input_thread                     │
        │  └─ cleanup_thread                   │
        └─────────────────────────────────────┘
                              │
                              ▼
        ╔══════════════════════════════════════╗
        ║  4. DATABASE.SETUP_DATABASE()       ║
        ╚══════════════════════════════════════╝
                    │
        ┌───────────┴───────────────────────────┐
        │                                       │
        ▼                                       ▼
┌───────────────┐                    ┌───────────────┐
│ Conectar a    │                    │ Crear tablas  │
│ ev_charging.db│                    │ si no existen │
└───────────────┘                    └───────────────┘
        │                                       │
        └───────────┬───────────────────────────┘
                    │
                    ▼
        ┌─────────────────────────────────────┐
        │  5. DATABASE.GET_ALL_CPS()         │
        │  SELECT * FROM charging_points     │
        └─────────────────────────────────────┘
                    │
                    ▼
        ╔══════════════════════════════════════╗
        ║  6. MARCADO A DESCONECTADO          ║
        ╚══════════════════════════════════════╝
                    │
                    │
        ┌───────────┴───────────┬───────────────┐
        │                       │               │
        ▼                       ▼               ▼
┌──────────────┐    ┌──────────────┐  ┌──────────────┐
│  MAD-01      │    │  BCN-02      │  │  ...         │
│  update_status│   │ update_status │  │ update_status│
│  'DESCONECTADO'│ │ 'DESCONECTADO'│ │ 'DESCONECTADO'│
└──────────────┘    └──────────────┘  └──────────────┘
                    │
                    ▼
        ╔══════════════════════════════════════╗
        ║  7. DISPLAY_PANEL() - CICLO INFINITO║
        ╚══════════════════════════════════════╝
                    │
        ┌───────────┴───────────────────────────┐
        │                                       │
        ▼                                       ▼
┌───────────────┐                    ┌───────────────┐
│ clear_screen() │                    │ get_all_cps() │
│ Cada 2 segundos│                    │              │
└───────────────┘                    └───────────────┘
                    │
                    ▼
        ┌─────────────────────────────────────┐
        │  8. MOSTRAR INFORMACIÓN             │
        │  - Tabla de CPs (GRIS=DESCONECTADO) │
        │  - Drivers conectados                │
        │  - Peticiones Kafka                  │
        │  - Mensajes del sistema              │
        │  - Comandos disponibles              │
        └─────────────────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────────┐
│           ESPERAR 2 SEGUNDOS                       │
│           Y REPETIR DESDE PASO 7                   │
└───────────────────────────────────────────────────┘
```

---

## 📊 ESTADO DE LA BASE DE DATOS

### Tabla: `charging_points`

| id      | location            | status       | price | driver_id | kwh | importe |
|---------|---------------------|--------------|-------|-----------|-----|---------|
| MAD-01  | C/ Serrano 10       | DESCONECTADO | 0.25  | NULL      | 0.0 | 0.0     |
| BCN-02  | Las Ramblas 55      | DESCONECTADO | 0.25  | NULL      | 0.0 | 0.0     |

### Tabla: `drivers`
```
(vacía al inicio)
```

### Tabla: `transactions`
```
(vacía al inicio)
```

---

## 🎨 CÓDIGOS DE COLOR (ANSI Escape Codes)

```python
VERDE    = \033[92m    # ACTIVADO
GRIS     = \033[90m    # DESCONECTADO ← PUNTO 1
AZUL     = \033[94m    # SUMINISTRANDO
ROJO     = \033[91m    # AVERIADO
NARANJA  = \033[93m    # FUERA_DE_SERVICIO
CYAN     = \033[96m    # RESERVADO
```

---

## 🔍 FUNCIONES INVOLUCRADAS (En orden de ejecución)

### `EV_Central.py`
1. ✅ **`__main__`** (línea 841) - Punto de entrada
2. ✅ **`setup_database()`** (línea 874) - Inicializa BD
3. ✅ **`get_all_cps()`** (línea 879) - Obtiene CPs
4. ✅ **`update_cp_status()`** (línea 883) - Marca DESCONECTADO
5. ✅ **`display_panel()`** (línea 907) - Muestra panel

### `database.py`
1. ✅ **`setup_database()`** (línea 56) - Crea tablas
2. ✅ **`get_all_cps()`** (línea 167) - Consulta SQL
3. ✅ **`update_cp_status()`** (línea 133) - Actualiza estado

---

## ✨ RESUMEN DEL PUNTO 1

**¿Qué hace el Punto 1?**
> Central, al iniciar, marca TODOS los CPs como DESCONECTADO (gris) hasta que ellos se conecten y envíen su estado real.

**¿Por qué es importante?**
> Evita que Central asuma que un CP está activo cuando en realidad puede estar desconectado. Es un principio de "trust but verify".

**¿Cuándo cambia el estado de DESCONECTADO?**
> Cuando el CP envía: `REGISTER#MAD-01#C/ Serrano 10#0.25`

---

## 🚀 PRÓXIMO PASO (Punto 2)

Cuando ejecutes:
```
py ev_cp_m.py 127.0.0.1 8000 127.0.0.1 8001 MAD-01
```

Esto activará el **PUNTO 2** donde el CP se conecta a Central.
