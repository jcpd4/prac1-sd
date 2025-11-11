# Guía de Pruebas - Protocolo <STX><DATA><ETX><LRC>

## Pruebas Unitarias del Protocolo

### Ejecutar Pruebas Automáticas

```bash
python test_protocolo.py
```

Este script ejecuta 5 suites de pruebas:
1. **Cálculo de LRC**: Verifica que el LRC se calcula correctamente
2. **Detección de Tramas Inválidas**: Verifica que se detectan errores
3. **Handshake ENQ/ACK**: Prueba el establecimiento de conexión
4. **Intercambio de Tramas**: Prueba comunicación completa bidireccional
5. **Casos Límite**: Mensajes vacíos, largos, caracteres especiales

---

## Pruebas de Integración - Escenario Completo

### Preparación

1. **Verificar que Kafka está corriendo:**
   ```bash
   # Kafka debe estar ejecutándose en 127.0.0.1:9092
   ```

2. **Limpiar base de datos (opcional):**
   ```bash
   # Si existe, eliminar ev_charging.db para empezar limpio
   del ev_charging.db
   ```

### Escenario de Prueba: 1 Central, 2 CPs, 2 Drivers

#### Terminal 1: Central
```bash
py EV_Central.py 8000 127.0.0.1:9092
```

**Verificaciones esperadas:**
- ✅ `[PROTOCOLO] Esperando ENQ del cliente...` (aparecerá cuando un CP se conecte)
- ✅ Panel de monitorización visible
- ✅ Sin errores en la consola

#### Terminal 2: CP 1 (MAD-01) - Monitor
```bash
py EV_CP_M.py 127.0.0.1 8000 127.0.0.1 8001 MAD-01
```

**Verificaciones esperadas:**
- ✅ `[PROTOCOLO] Enviando ENQ (handshake inicial)...`
- ✅ `[PROTOCOLO] Handshake exitoso: Servidor respondió ACK`
- ✅ `[PROTOCOLO] Trama construida: STX + 'REGISTER#MAD-01#...' + ETX + LRC=...`
- ✅ `[Monitor] Central confirmó recepción de REGISTER`

#### Terminal 3: CP 1 (MAD-01) - Engine
```bash
py EV_CP_E.py 8001 127.0.0.1:9092 MAD-01
```

**Verificaciones esperadas:**
- ✅ `[Engine] Servidor de salud local escuchando en 0.0.0.0:8001`
- ✅ Sin errores iniciales

**En la terminal del Monitor deberías ver:**
- ✅ `[PROTOCOLO] Realizando handshake con Engine...`
- ✅ `[PROTOCOLO ENGINE] Esperando ENQ del Monitor...`
- ✅ `[PROTOCOLO ENGINE] ENQ recibido. Enviando ACK...`
- ✅ `[PROTOCOLO] Handshake exitoso: Servidor respondió ACK`
- ✅ `[PROTOCOLO] Trama construida: STX + 'HEALTH_CHECK#MAD-01' + ETX + LRC=...`
- ✅ `[PROTOCOLO ENGINE] Trama parseada correctamente: 'HEALTH_CHECK#MAD-01' (LRC válido: ...)`
- ✅ `[PROTOCOLO ENGINE] Trama construida: STX + 'OK' + ETX + LRC=...`

#### Terminal 4: CP 2 (BCN-02) - Monitor
```bash
py EV_CP_M.py 127.0.0.1 8000 127.0.0.1 8002 BCN-02
```

**Mismas verificaciones que Terminal 2**

#### Terminal 5: CP 2 (BCN-02) - Engine
```bash
py EV_CP_E.py 8002 127.0.0.1:9092 BCN-02
```

**Mismas verificaciones que Terminal 3**

#### Terminal 6: Driver 1
```bash
py EV_Driver.py 127.0.0.1:9092 101
```

**Verificaciones esperadas:**
- ✅ Panel del driver visible
- ✅ Estado de red recibido (debe mostrar los 2 CPs)

#### Terminal 7: Driver 2
```bash
py EV_Driver.py 127.0.0.1:9092 202
```

---

## Checklist de Verificación Visual

### ✅ Verificaciones en Central (Terminal 1)

- [ ] Panel muestra 2 CPs registrados (MAD-01 y BCN-02)
- [ ] Estado de CPs: **VERDE** (ACTIVADO)
- [ ] Mensajes `[PROTOCOLO]` visibles en los logs
- [ ] Se ven mensajes de handshake: `Handshake exitoso (ENQ recibido, ACK enviado)`

### ✅ Verificaciones en Monitor (Terminal 2 y 4)

- [ ] Mensaje: `¡Conectado al servidor central! Enviando registro.`
- [ ] Mensaje: `[PROTOCOLO] Handshake exitoso: Servidor respondió ACK`
- [ ] Mensaje: `[Monitor] Central confirmó recepción de REGISTER`
- [ ] Cada segundo: Mensajes `[PROTOCOLO]` de HEALTH_CHECK con Engine
- [ ] Mensajes: `[PROTOCOLO ENGINE] Trama parseada correctamente`

### ✅ Verificaciones en Engine (Terminal 3 y 5)

- [ ] Mensaje: `[Engine] Servidor de salud local escuchando en ...`
- [ ] Cuando Monitor se conecta: `[PROTOCOLO ENGINE] Esperando ENQ del Monitor...`
- [ ] Mensaje: `[PROTOCOLO ENGINE] Handshake exitoso: ACK enviado al Monitor`
- [ ] Mensajes de tramas parseadas: `[PROTOCOLO ENGINE] Trama parseada correctamente: 'HEALTH_CHECK#...'`

---

## Pruebas Funcionales

### Prueba 1: Solicitud de Recarga desde Driver

**Pasos:**
1. En Driver 1 (Terminal 6), solicitar recarga en MAD-01
2. Observar en Central la autorización
3. Observar en Monitor CP1 el comando AUTORIZAR_SUMINISTRO
4. Observar en Engine CP1 la recepción del comando

**Mensajes esperados del protocolo:**

**Central → Monitor:**
```
[PROTOCOLO] Trama construida: STX + 'AUTORIZAR_SUMINISTRO#101' + ETX + LRC=...
[PROTOCOLO] Trama enviada correctamente: 'AUTORIZAR_SUMINISTRO#101'
```

**Monitor → Engine:**
```
[PROTOCOLO] Realizando handshake con Engine...
[PROTOCOLO] Trama construida: STX + 'AUTORIZAR_SUMINISTRO#101' + ETX + LRC=...
[PROTOCOLO ENGINE] Trama parseada correctamente: 'AUTORIZAR_SUMINISTRO#101' (LRC válido: ...)
```

**Engine → Monitor:**
```
[PROTOCOLO ENGINE] Trama construida: STX + 'ACK#AUTORIZAR_SUMINISTRO' + ETX + LRC=...
[PROTOCOLO ENGINE] Trama enviada correctamente: 'ACK#AUTORIZAR_SUMINISTRO'
```

### Prueba 2: Comando PARAR desde Central

**Pasos:**
1. En Central (Terminal 1), escribir: `P MAD-01`
2. Observar flujo completo de comandos

**Mensajes esperados:**

**Central → Monitor:**
```
[PROTOCOLO] Trama construida: STX + 'PARAR#CENTRAL' + ETX + LRC=...
```

**Monitor → Engine:**
```
[PROTOCOLO] Trama construida: STX + 'PARAR' + ETX + LRC=...
[PROTOCOLO ENGINE] Trama parseada correctamente: 'PARAR' (LRC válido: ...)
```

**Engine → Monitor:**
```
[PROTOCOLO ENGINE] Trama construida: STX + 'ACK#PARAR' + ETX + LRC=...
```

**Monitor → Central:**
```
[PROTOCOLO] Trama construida: STX + 'ACK#PARAR' + ETX + LRC=...
```

### Prueba 3: Simulación de Avería

**Pasos:**
1. En Engine CP1 (Terminal 3), presionar `F` (FAIL)
2. Observar mensajes de detección

**Mensajes esperados:**

**Engine → Monitor:**
```
[PROTOCOLO ENGINE] Trama construida: STX + 'KO' + ETX + LRC=...
[PROTOCOLO] Trama parseada correctamente: 'KO' (LRC válido: ...)
```

**Monitor → Central:**
```
[PROTOCOLO] Trama construida: STX + 'FAULT#MAD-01' + ETX + LRC=...
[PROTOCOLO] Trama enviada correctamente: 'FAULT#MAD-01'
```

**En Central:**
- Panel debe mostrar MAD-01 en **ROJO** (AVERIADO)

### Prueba 4: Recuperación de Avería

**Pasos:**
1. En Engine CP1 (Terminal 3), presionar `R` (RECOVER)
2. Observar mensajes de recuperación

**Mensajes esperados:**

**Engine → Monitor:**
```
[PROTOCOLO ENGINE] Trama construida: STX + 'OK' + ETX + LRC=...
```

**Monitor → Central:**
```
[PROTOCOLO] Trama construida: STX + 'RECOVER#MAD-01' + ETX + LRC=...
```

**En Central:**
- Panel debe mostrar MAD-01 en **VERDE** (ACTIVADO)

---

## Pruebas de Resiliencia (obligatorias en la defensa)

> Todas las pruebas se hacen con Central, al menos 1 CP (Monitor+Engine) y 1 Driver activos. Ejecutar una sola variación cada vez para observar claramente el efecto.

### R1) Caída súbita del Monitor (Ctrl+C)

Pasos:
1. Con servicio en reposo (o incluso autorizado), cerrar la terminal de `EV_CP_M.py`.
2. Observar Central durante 5-10 s.

Esperado:
- Central cambia el CP a `DESCONECTADO` y no acepta nuevos suministros para ese CP.
- Si había SUMINISTRO en curso, este finalizará salvo que también caiga el Engine (en ese caso se detiene por pérdida de telemetría).
- Cuando se relance el Monitor, el CP vuelve a reportar estado y pasa a `ACTIVADO`/estado coherente.

### R2) Caída súbita del Engine (Ctrl+C)

Pasos:
1. Iniciar un SUMINISTRO (Driver autoriza y Engine pulsa `I`).
2. Cerrar la terminal de `EV_CP_E.py` en mitad de la carga.

Esperado:
- El Monitor detecta KO y envía `FAULT` a Central.
- Central marca `AVERIADO`, envía `SUPPLY_ERROR` al Driver con el parcial (kWh/€) y muestra el mensaje en panel.
- Al relanzar el Engine y pulsar `R` (RECOVER) o recibir `RECOVER` desde Monitor, el CP vuelve a `ACTIVADO`.

### R3) Cierre de la app del Driver durante el servicio

Pasos:
1. Driver solicita y es autorizado; Engine inicia suministro (`I`).
2. Cerrar la terminal del Driver.

Esperado:
- El suministro continúa hasta el fin (END) y se genera `SUPPLY_END` → TICKET (si el driver vuelve, verá el ticket cuando reciba notificaciones; en caso de no estar conectado, el evento queda publicado igualmente).
- Si el Driver cierra ANTES de iniciar (reservado), Central libera la reserva inmediatamente (`RESERVA liberada...`) y el CP vuelve a `ACTIVADO`.

### R4) Caída de Central

Pasos:
1. Iniciar un SUMINISTRO.
2. Cerrar la Central.
3. Dejar que el Engine continúe 5-10 s y luego finalizar (`E`).
4. Relanzar Central.

Esperado:
- Durante la caída, el Engine sigue enviando telemetría; si el broker Kafka no está disponible, el Engine la bufferiza.
- Al volver Central/Broker, la telemetría pendiente se reenvía; al finalizar, se procesa `SUPPLY_END` y se envía TICKET.
- No se admiten nuevas autorizaciones mientras la Central no esté disponible.

### R5) Kafka temporalmente no disponible (opcional)

Pasos:
1. Parar el broker Kafka mientras hay carga.
2. Observar `EV_CP_E.py` (mensajes: telemetría en buffer).
3. Levantar de nuevo Kafka.

Esperado:
- El Engine no pierde datos: envía el buffer al reconectar (puede verse una ráfaga de CONSUMO al volver).

---

## Secuencia de Demo Recomendada (3 PCs)

1) Arranque: Central en PC-A; Monitor+Engine de 2 CPs en PC-B; 1-2 Drivers en PC-C.
2) Flujo base: solicitud, autorización, inicio (`I`), consumo, fin (`E`), TICKET.
3) Comandos Central: `P <CP>`, `R <CP>`, `PT`, `RT` (ver que no desconecta sockets y que RT recupera KO).
4) Resiliencia: ejecutar R1→R4.
5) Cierre: mostrar que el sistema queda estable y CPs listos.

---

## Errores Comunes y Soluciones

### Error: "Handshake fallido"

**Síntomas:**
- `[PROTOCOLO] ERROR: No se recibió respuesta al ENQ`
- `[PROTOCOLO] ERROR: Handshake fallido`

**Soluciones:**
1. Verificar que el servidor esté escuchando en el puerto correcto
2. Verificar que no hay firewall bloqueando
3. Verificar que el orden de arranque sea correcto (servidor antes que cliente)

### Error: "LRC no coincide"

**Síntomas:**
- `[PROTOCOLO] ERROR: LRC no coincide. Recibido: 0xXX, Esperado: 0xYY`

**Soluciones:**
1. Verificar que ambas partes usan la misma función `calculate_lrc`
2. Verificar que no hay corrupción de datos en la red
3. Reiniciar componentes

### Error: "Trama demasiado corta"

**Síntomas:**
- `[PROTOCOLO] ERROR: Trama demasiado corta (X bytes). Mínimo necesario: 4 bytes`

**Soluciones:**
1. Verificar que el socket no se cerró prematuramente
2. Verificar que se envía la trama completa
3. Verificar timeout de socket

### Error: "Timeout esperando trama"

**Síntomas:**
- `[PROTOCOLO] Timeout esperando trama (timeout=Xs)`

**Soluciones:**
1. Verificar que el remoto está activo y respondiendo
2. Aumentar timeout si es necesario (para pruebas locales)
3. Verificar que el flujo de mensajes es el esperado

---

## Resumen de Logs del Protocolo

### Prefijos de Log

- `[PROTOCOLO]` - Logs generales del protocolo
- `[PROTOCOLO ENGINE]` - Logs específicos del Engine (EV_CP_E.py)
- `[CENTRAL]` - Logs del módulo Central
- `[Monitor]` - Logs del módulo Monitor
- `[ENGINE]` - Logs del módulo Engine

### Mensajes Clave a Buscar

**Handshake exitoso:**
```
[PROTOCOLO] Handshake exitoso: Servidor respondió ACK
```

**Trama enviada:**
```
[PROTOCOLO] Trama enviada correctamente: 'MENSAJE'
```

**Trama recibida:**
```
[PROTOCOLO] Trama parseada correctamente: 'MENSAJE' (LRC válido: 0xXX)
```

**Error detectado:**
```
[PROTOCOLO] ERROR: ...
```

---

## Conclusión

Si todas las verificaciones pasan y ves los mensajes `[PROTOCOLO]` en todas las comunicaciones, el protocolo está funcionando correctamente.

**Indicadores de éxito:**
- ✅ Handshakes exitosos en todas las conexiones
- ✅ Tramas parseadas correctamente con LRC válido
- ✅ ACKs/NACKs intercambiados correctamente
- ✅ Sin errores de LRC o formato
- ✅ Panel de Central muestra los CPs correctamente
- ✅ Health checks funcionan cada segundo

**Si algo falla:**
1. Revisar los mensajes de error en consola
2. Verificar que todos los componentes usan la misma versión del protocolo
3. Ejecutar `test_protocolo.py` para pruebas unitarias
4. Revisar la documentación en `DOCUMENTACION_PROTOCOLO.md`

