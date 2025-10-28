# 📖 CAPÍTULO 4: Sistema de Kafka - Mensajería Asíncrona en EVCharging

## 🎯 Objetivo
Entender cómo Central, Driver y Engine se comunican usando Kafka (producers, consumers, topics y flujo de mensajes) en tu proyecto.

---

## 1) ¿Qué es Kafka, en sencillo?
- Un "buzón de mensajes" muy rápido y fiable.
- Los emisores (producers) publican mensajes en buzones llamados topics.
- Los receptores (consumers) se suscriben a topics y leen mensajes.
- Es asíncrono: el emisor no espera a que el receptor esté escuchando.

Analogía:
- Topic = buzón con un nombre (driver_requests, cp_telemetry...)
- Producer = quien mete cartas en el buzón
- Consumer = quien abre el buzón y lee

---

## 2) Los topics de tu sistema
- `driver_requests` (drivers → central): peticiones de recarga
- `cp_telemetry` (engine → central): telemetría por segundo + eventos
- `driver_notifications` (central → drivers): respuestas y tickets
- `network_status` (central → drivers): estado global de CPs (cada 5s)

---

## 3) Productores y consumidores en tu código

### 3.1 Central
- Producer (compartido): `shared_producer_ref`
  - Envía a `driver_notifications` y `network_status`
- Consumer: `process_kafka_requests()`
  - Lee de `driver_requests` y `cp_telemetry`

### 3.2 Driver
- Producer: envía peticiones a `driver_requests`
- Consumer: `process_central_notifications()` (lee `driver_notifications`)
- Consumer: `process_network_updates()` (lee `network_status`)

### 3.3 Engine (EV_CP_E)
- Producer: `KAFKA_PRODUCER`
  - Envía a `cp_telemetry` (CONSUMO, SESSION_STARTED, SUPPLY_END, AVERIADO...)

---

## 4) Cómo se crea un Producer y un Consumer (patrón usado)

Producer (en Central y Engine):
```python
KafkaProducer(
    bootstrap_servers=[broker],
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)
```
- `bootstrap_servers`: dirección y puerto del broker Kafka (ej: 127.0.0.1:9092)
- `value_serializer`: convierte dicts Python → JSON → bytes

Consumer (en Central y Driver):
```python
KafkaConsumer(
    TOPIC1,
    TOPIC2,
    bootstrap_servers=[broker],
    auto_offset_reset='latest',
    group_id='nombre-del-grupo',
    value_deserializer=lambda x: json.loads(x.decode('utf-8'))
)
```
- `auto_offset_reset='latest'`: lee solo lo último (no histórico)
- `group_id`: los consumers del mismo grupo se reparten los mensajes
- `value_deserializer`: bytes → JSON → dict

---

## 5) Flujo completo A: Driver solicita recarga

1) Driver publica en `driver_requests`:
```python
producer.send('driver_requests', {
  "user_id": "101",
  "cp_id": "MAD-01",
  "timestamp": 1699999999.0
})
```

2) Central (consumer) recibe en `process_kafka_requests()`:
```python
if topic == KAFKA_TOPIC_REQUESTS:
    # valida disponibilidad, sesión, estado...
    database.update_cp_status(cp_id, 'RESERVADO')
    current_sessions[cp_id] = { 'driver_id': user_id, 'status': 'authorized' }
    # envía comando síncrono al CP vía socket
    auth_command = f"AUTORIZAR_SUMINISTRO#{user_id}"
    active_cp_sockets[cp_id].sendall(auth_command.encode('utf-8'))
    # responde al driver por Kafka
    send_notification_to_driver(producer, user_id, {"type": "AUTH_OK", "cp_id": cp_id})
```

3) Driver (consumer) recibe `AUTH_OK` en `process_central_notifications()` y lo muestra.

---

## 6) Flujo completo B: Engine envía telemetría de consumo

1) Engine simula carga y publica en `cp_telemetry` cada segundo:
```python
send_telemetry_message({
  "type": "CONSUMO",
  "cp_id": CP_ID,
  "user_id": driver_id,
  "kwh": 0.3,
  "importe": 0.06
})
```

2) Central (consumer) procesa:
```python
elif topic == KAFKA_TOPIC_STATUS and msg_type == 'CONSUMO':
    database.update_cp_consumption(cp_id, kwh, importe, driver_id)
    # (extra) reenviamos al driver su consumo actual
    producer.send(KAFKA_TOPIC_DRIVER_NOTIFY, {
      "type": "CONSUMO_UPDATE",
      "cp_id": cp_id,
      "user_id": driver_id,
      "kwh": kwh,
      "importe": importe
    })
```

3) Driver (consumer) recibe `CONSUMO_UPDATE` y actualiza su panel.

---

## 7) Flujo completo C: Fin de suministro (ticket)

1) Engine envía `SUPPLY_END` a `cp_telemetry`:
```python
send_telemetry_message({
  "type": "SUPPLY_END",
  "cp_id": CP_ID,
  "user_id": driver_id,
  "kwh": total_kwh,
  "importe": total_importe
})
```

2) Central lo procesa, limpia BD y notifica al driver:
```python
elif msg_type == 'SUPPLY_END':
    database.clear_cp_consumption(cp_id)
    # ticket final al driver
    send_notification_to_driver(producer, driver_id, {
      "type": "TICKET",
      "cp_id": cp_id,
      "user_id": driver_id,
      "kwh": kwh,
      "importe": importe
    })
    database.update_cp_status(cp_id, 'ACTIVADO')
```

---

## 8) Flujo completo D: Avería (interrupción de carga)

1) Engine detecta problema y envía `AVERIADO`:
```python
send_telemetry_message({
  "type": "AVERIADO",
  "cp_id": CP_ID,
  "user_id": driver_id,
  "kwh": parcial,
  "importe": parcial
})
```

2) Central notifica al driver con `SUPPLY_ERROR` y mantiene estado de CP:
```python
elif msg_type in ('AVERIADO', 'CONEXION_PERDIDA', 'FAULT'):
    # si estaba suministrando, notificación de error al driver
    send_notification_to_driver(producer, driver_id, {
      "type": "SUPPLY_ERROR",
      "cp_id": cp_id,
      "user_id": driver_id,
      "reason": "Avería detectada",
      "kwh_partial": kwh,
      "importe_partial": importe
    })
    database.clear_cp_telemetry_only(cp_id)
    database.update_cp_status(cp_id, 'AVERIADO')
```

---

## 9) Broadcast de estado de red a Drivers
Central publica cada 5 segundos el estado de todos los CPs en `network_status`:
```python
message = {
  'type': 'NETWORK_STATUS_UPDATE',
  'cps': [{'id': 'MAD-01', 'status': 'ACTIVADO', 'location': '...'}, ...]
}
producer.send(KAFKA_TOPIC_NETWORK_STATUS, value=message)
```

Drivers consumen este topic y muestran CPs disponibles en su panel.

---

## 10) Buenas prácticas aplicadas
- Producer compartido en Central (`shared_producer_ref`)
- Serialización JSON consistente (serializer/deserializer)
- `auto_offset_reset='latest'` para no procesar histórico
- `group_id` para agrupar consumidores por rol
- Manejo de errores y logs en Central y Engine

---

## 11) Diagrama de Flujo (resumen)

```
Driver --(driver_requests)--> Kafka --(process_kafka_requests)--> Central
   ^                                                             |
   |                                                             v
   +--(driver_notifications)<-- Kafka <---------[shared_producer]--

Engine --(cp_telemetry)--> Kafka --(process_kafka_requests)--> Central

Central --(network_status)--> Kafka --> Drivers (panel de red)
```

---

## 12) Próximo capítulo
¿Seguimos con la **Base de Datos** o con el **Driver**?
