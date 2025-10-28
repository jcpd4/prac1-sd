# 📖 CAPÍTULO 6: Panel de Monitorización (CENTRAL)

## 🎯 Objetivo
Entender cómo se construye y actualiza el panel de CENTRAL, qué información muestra, de dónde obtiene los datos y cómo se mantiene fluido sin bloquear otros hilos.

---

## 1) ¿Qué es el panel?
El panel es una vista de consola que refresca cada 2 segundos y muestra:
- **CPs**: ID, ubicación, precio, estado (con colores) y, si aplica, datos de suministro.
- **Drivers conectados** y qué CP ocupan (si corresponde).
- **Peticiones Kafka** de los drivers en curso.
- **Mensajes del sistema** (logs operativos y eventos).
- **Comandos disponibles** para operar CPs.

Se ejecuta en el hilo principal, mientras el resto de hilos (Kafka, sockets, comandos, limpieza, broadcast) alimentan sus fuentes de datos.

---

## 2) Estructura del panel en el código
Función: `display_panel(central_messages, driver_requests)` en `EV_Central.py`.

Fuentes de datos:
- `database.get_all_cps()` → tabla `charging_points` (estados, consumo, driver, etc.)
- `central_messages` → deque/lista compartida con logs (push_message)
- `driver_requests` → lista de solicitudes activas (alimentada por Kafka)
- `connected_drivers` y `current_sessions` → asignaciones en memoria (con lock)

Refresco:
- Bucle infinito con `time.sleep(2)` y limpieza de pantalla (`clear_screen`) por iteración.

---

## 3) Colores y leyendas de estado
La función `get_status_color(status)` usa códigos ANSI para colorear el estado:
- `ACTIVADO` → Verde
- `DESCONECTADO` → Gris
- `SUMINISTRANDO` → Azul
- `AVERIADO` → Rojo
- `FUERA_DE_SERVICIO` → Amarillo/Naranja con leyenda "Out of Order"
- `RESERVADO` → Cyan (estado intermedio cuando Central autoriza a un driver)

Ejemplo de impresión de cada CP:
```text
ID        | UBICACIÓN                 | PRECIO       | ESTADO
MAD-01    | C/ Serrano 10            | 0.25 €/kWh   | [92mACTIVADO[0m
```

Si un CP está `SUMINISTRANDO`, se muestra una línea adicional con:
- kWh acumulados, importe acumulado, `driver_id` activo.

---

## 4) Secciones del panel
1. Cabecera y separadores
2. Tabla de CPs (datos desde BD)
3. Drivers conectados y asignaciones (en memoria con lock)
4. Peticiones Kafka en curso (`driver_requests`)
5. Mensajes del sistema (`central_messages`)
6. Pie con comandos disponibles y marca temporal

---

## 5) Integración con hilos y concurrencia
- Hilo Kafka (`process_kafka_requests`) añade solicitudes y logs y actualiza BD.
- Hilo Sockets (`start_socket_server` → `handle_client`) actualiza BD y `active_cp_sockets`.
- Hilo de Broadcast (`broadcast_network_status`) envía el estado de red cada 5s.
- Hilo de Limpieza (`cleanup_disconnected_drivers`) depura `connected_drivers` y sesiones.
- Hilo de Comandos (`process_user_input`) recibe acciones del operador.

El panel solo LEE estas fuentes, por lo que no bloquea a los que ESCRIBEN. Los accesos concurrentes a estructuras compartidas usan `active_cp_lock`.

---

## 6) Relación con la Base de Datos
- El panel confía en `database.get_all_cps()` para pintar la realidad del sistema.
- Los hilos de Kafka/sockets modifican la BD con `update_cp_status`, `update_cp_consumption`, `clear_cp_consumption`, etc.
- Esto asegura que lo que se ve en pantalla sea la "verdad" persistida.

---

## 7) Relación con Kafka
- La sección de "Peticiones de Conductores" muestra las entradas almacenadas en `driver_requests` que son alimentadas por el consumer Kafka.
- Los cambios de estado producidos por mensajes de Kafka (CONSUMO, SUPPLY_END, AVERIADO) impactan la BD y, por ende, el panel.

---

## 8) Comandos disponibles del operador
Se imprimen al final del panel y se procesan en el hilo `process_user_input`:
- `P <CP_ID>` / `PARAR <CP_ID>` → marca `FUERA_DE_SERVICIO`
- `R <CP_ID>` / `REANUDAR <CP_ID>` → vuelve a `ACTIVADO`
- `PT` (`PARAR_TODOS`) / `RT` (`REANUDAR_TODOS`)
- `Q` / `QUIT` → salir

Los comandos usan sockets síncronos guardados en `active_cp_sockets`.

---

## 9) Buenas prácticas aplicadas
- Panel no bloqueante (solo lectura + `sleep`)
- Fuentes de datos coherentes (BD como fuente de verdad)
- Colores ANSI simples y claros
- Separación de responsabilidades por hilos
- Uso de lock para estructuras compartidas

---

## 10) Posibles mejoras
- Ventanas paginadas si hay muchos CPs
- Mostrar precio dinámico por CP (ya se lee desde BD)
- Métricas agregadas (totales, promedios) en cabecera
- Filtros: solo ACTIVADOS/SUMINISTRANDO
- Exportación de panel a HTML para demo

---

## 11) Dónde mirar en el código
- `EV_Central.py`
  - `display_panel(...)`
  - `get_status_color(...)`
  - `clear_screen()`
  - `process_kafka_requests(...)`
  - `start_socket_server(...)` / `handle_client(...)`

---

## 12) Siguiente capítulo
¿Seguimos con **Capítulo 7: Locks y Sincronización** (cómo y por qué se usan `threading.Lock` en Central/Engine/DB)?
