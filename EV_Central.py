# Fichero: ev_central.py
import socket
import threading
import sys
import time
import os
# Asegúrate de tener instalado: pip install kafka-python
from kafka import KafkaConsumer, KafkaProducer
import json
import database 
#segunda entrega:
import EV_Central_API # codigo mio(juanky) donde hago la parte de api_central
from database import log_audit_event # Seva: funcion para loguear eventos de auditoria
from cryptography.fernet import Fernet, InvalidToken # Seva: Cifrado

# --- NUEVO: FUNCIÓN PARA LEER CONFIGURACIÓN DE RED ---
def get_network_config():
    try:
        with open('network_config.json', 'r') as f:
            return json.load(f)
    except:
        return {}
# -----------------------------------------------------

# --- Configuración global ---
KAFKA_TOPIC_REQUESTS = 'driver_requests' # Conductores -> Central
KAFKA_TOPIC_STATUS = 'cp_telemetry'      # CP -> Central (Telemetría/Averías/Consumo)
KAFKA_TOPIC_DRIVER_NOTIFY = 'driver_notifications' # Central -> Drivers
KAFKA_TOPIC_NETWORK_STATUS = 'network_status' # anunciar el estado de la red (11)
# Diccionario para almacenar la referencia a los sockets de los CPs activos
active_cp_sockets = {}
# Referencia global al producer compartido de Kafka
shared_producer_ref = None 
# Diccionario para controlar qué driver está usando cada CP
cp_driver_assignments = {}  # {cp_id: driver_id}
# Diccionario para controlar qué drivers están conectados
connected_drivers = set()  # {driver_id1, driver_id2, ...}
# Lock para proteger active_cp_sockets y lista de mensajes compartidos
active_cp_lock = threading.Lock()
# Sesiones actuales autorizadas/activas: { cp_id: { 'driver_id': str, 'status': 'authorized'|'charging' } }
current_sessions = {}

# Comandos pendientes por CP para poder confirmar o revertir con ACK/NACK
# Formato: { cp_id: { 'command': 'PARAR'|'REANUDAR', 'prev_status': str } }
pending_cp_commands = {}

# Ventana de gracia tras RECOVER/REANUDAR para evitar parpadeo a DESCONECTADO
# { cp_id: timestamp }
recent_recover_events = {}

# Última vez que se recibió actividad desde el monitor (socket) y desde el engine (telemetría)
monitor_last_seen = {}  # { cp_id: timestamp }
engine_last_seen = {}   # { cp_id: timestamp }
engine_health_status = {}  # { cp_id: 'OK'|'KO' }

# Timeouts para considerar que un monitor o un engine están caídos
MONITOR_HEARTBEAT_TIMEOUT = 7   # segundos sin mensajes del monitor ⇒ DESCONECTADO
ENGINE_TELEMETRY_TIMEOUT = 5      # segundos sin telemetría ⇒ AVERIADO
RECONCILE_INTERVAL = 1.0          # segundos entre comprobaciones del reconciliador

# Desconexiones pendientes (gracia antes de marcar DESCONECTADO definitivo)
pending_monitor_disconnects = {}  # { cp_id: timestamp }

# CPs que ya se han conectado al menos una vez desde que arrancó esta CENTRAL (para diferenciar primera conexión de sesión)
connected_once_this_session = set()

# Control de verbosidad del protocolo (solo imprime si es True)
DEBUG_PROTOCOL = False

# Control de verbosidad de consola en CENTRAL (reduce prints en stdout)
CENTRAL_VERBOSE = False

# --- Funciones auxiliares ---
def push_message(msg_list, msg, maxlen=200):
    msg_list.append(msg) # Nuestra lista inteligente ya añade el timestamp sola
    if len(msg_list) > maxlen:
        del msg_list[0:len(msg_list)-maxlen]

class TimestampedList(list):
    """Lista personalizada que guarda automáticamente el timestamp al añadir mensajes."""
    def append(self, item):
        # Guardamos un diccionario con el mensaje y la hora actual
        super().append({'msg': str(item), 'timestamp': time.time()})

def force_release_cp_session(cp_id, central_messages=None, reason="", target_status=None,
                             notify_driver=True, clear_metrics=True, supply_error_reason=None,
                             partial_kwh=None, partial_importe=None, driver_override=None):
    
    # --- STEP 1: INITIAL METRIC AND DRIVER RETRIEVAL ---
    # Leemos la sesión y métricas desde la BD ANTES de cualquier limpieza.
    
    db_metrics = None
    try:
        cp_data_list = database.get_all_cps()
        db_metrics = next((cp for cp in cp_data_list if cp['id'] == cp_id), None)
    except Exception:
        pass # Database access failed

    if db_metrics:
        # Si no se pasaron métricas, usamos las de la DB
        if partial_kwh is None:
            # Aseguramos que kwh sea float o 0.0 si es NULL o texto no válido
            partial_kwh = db_metrics.get('kwh')
            if partial_kwh is not None:
                 try: partial_kwh = float(partial_kwh)
                 except: partial_kwh = 0.0
            else: partial_kwh = 0.0

        if partial_importe is None:
            partial_importe = db_metrics.get('importe')
            if partial_importe is not None:
                try: partial_importe = float(partial_importe)
                except: partial_importe = 0.0
            else: partial_importe = 0.0
        
        # Aseguramos que el driver sea identificado desde la DB si no se pasó
        if not driver_override:
            driver_override = db_metrics.get('driver_id')

    # Aseguramos valores 0.0 si siguen siendo None
    if partial_kwh is None: partial_kwh = 0.0
    if partial_importe is None: partial_importe = 0.0
    
    # --- STEP 2: LIBERACIÓN DE SESIÓN Y NOTIFICACIÓN ---

    driver_id = None
    session_status = None

    with active_cp_lock:
        session = current_sessions.pop(cp_id, None)
        if session:
            driver_id = session.get('driver_id')
            session_status = session.get('status')
        
        assigned_driver = cp_driver_assignments.pop(cp_id, None)
        if assigned_driver and not driver_id:
            driver_id = assigned_driver

    if driver_override and not driver_id:
        driver_id = driver_override

    # NOTIFICACIÓN DE ERROR
    if driver_id and supply_error_reason and notify_driver and shared_producer_ref:
        if central_messages is not None:
            push_message(
                central_messages,
                f"[RELEASE] SUPPLY_ERROR -> CP {cp_id} driver {driver_id} "
                f"({supply_error_reason}) parcial {partial_kwh:.3f} kWh / "
                f"{partial_importe:.2f} €"
            )
        error_msg = {
            "type": "SUPPLY_ERROR",
            "cp_id": cp_id,
            "user_id": driver_id,
            "reason": supply_error_reason,
            "kwh_partial": partial_kwh, # Usamos el valor asegurado
            "importe_partial": partial_importe # Usamos el valor asegurado
        }
        try:
            send_notification_to_driver(shared_producer_ref, driver_id, error_msg)
        except Exception as e:
            if central_messages is not None:
                push_message(central_messages, f"[RELEASE] Error notificando SUPPLY_ERROR a driver {driver_id}: {e}")
    # ... (Resto de notificación de cancelación si no hay supply error)
    
    # LIMPIEZA DE MÉTRICAS (ocurre AHORA, después de notificar)
    if clear_metrics:
        try:
            database.clear_cp_telemetry_only(cp_id)
        except Exception as e:
            if central_messages is not None:
                push_message(central_messages, f"[RELEASE] Error limpiando telemetría de {cp_id}: {e}")

    # CAMBIO DE ESTADO
    if target_status:
        try:
            database.update_cp_status(cp_id, target_status)
        except Exception as e:
            if central_messages is not None:
                push_message(central_messages, f"[RELEASE] Error actualizando estado de {cp_id} a {target_status}: {e}")
    if target_status != 'DESCONECTADO':
        pending_monitor_disconnects.pop(cp_id, None)

# --- Funciones del Protocolo de Sockets <STX><DATA><ETX><LRC> ---

# En EV_Central.py, añádelo cerca de get_status_color

def get_status_emoji(status):
    """Devuelve un emoji para el panel-matriz basado en el estado."""
    emojis = {
        "ACTIVADO": "🟢",
        "DESCONECTADO": "⚪",
        "SUMINISTRANDO": "🔵",
        "AVERIADO": "🔴",
        "FUERA_DE_SERVICIO": "🟠",
        "RESERVADO": "🟣",
    }
    return emojis.get(status, "❓") # Emoji por defecto para estados desconocidos

# Constantes del protocolo
STX = bytes([0x02])  # Start of Text
ETX = bytes([0x03])  # End of Text
ENQ = bytes([0x05])  # Enquiry (handshake inicial)
ACK = bytes([0x06])  # Acknowledgement (respuesta positiva)
NACK = bytes([0x15]) # Negative Acknowledgement (respuesta negativa)
EOT = bytes([0x04])  # End of Transmission (cierre de conexión)

def calculate_lrc(message_bytes):
    """
    Calcula el LRC (Longitudinal Redundancy Check) mediante XOR byte a byte.
    El LRC es una técnica de detección de errores que calcula el XOR de todos los bytes del mensaje.
    
    Args:
        message_bytes: Bytes del mensaje completo (STX + DATA + ETX)
    
    Returns:
        int: Valor del LRC (0-255)
    """
    #Paso 1: Inicializar LRC en 0
    lrc = 0
    #Paso 2: Calcular XOR de todos los bytes del mensaje
    for byte in message_bytes:
        lrc ^= byte
    #Paso 3: Devolver el valor del LRC
    return lrc

def build_frame(data_string):
    """
    Construye una trama completa siguiendo el protocolo <STX><DATA><ETX><LRC>.
    Esta función toma un string de datos y lo empaqueta con los delimitadores y checksum.
    
    Args:
        data_string: String con los datos a enviar (ej: "REGISTER#CP01#Ubicacion")
    
    Returns:
        bytes: Trama completa lista para enviar por socket
    """
    #Paso 1: Convertir el string de datos a bytes UTF-8
    data = data_string.encode('utf-8')
    #Paso 2: Construir el mensaje completo: STX + DATA + ETX
    message = STX + data + ETX
    #Paso 3: Calcular el LRC del mensaje completo
    lrc_value = calculate_lrc(message)
    #Paso 4: Añadir el LRC al final de la trama
    frame = message + bytes([lrc_value])
    #Paso 5: Mostrar en consola la trama construida (solo para depuración)
    if DEBUG_PROTOCOL:
        print(f"[PROTOCOLO] Trama construida: STX + '{data_string}' + ETX + LRC={lrc_value:02X}")
    #Paso 6: Devolver la trama completa
    return frame

def parse_frame(frame_bytes):
    """
    Parsea una trama recibida y valida el LRC para detectar errores de transmisión.
    Esta función extrae los datos del mensaje y verifica la integridad mediante el LRC.
    
    Args:
        frame_bytes: Bytes recibidos del socket (debe contener STX + DATA + ETX + LRC)
    
    Returns:
        tuple: (data_string, is_valid) donde:
            - data_string: String con los datos extraídos o None si hay error
            - is_valid: True si el LRC es válido, False en caso contrario
    """
    #Paso 1: Verificar que la trama tenga el tamaño mínimo (STX + al menos 1 byte DATA + ETX + LRC)
    if len(frame_bytes) < 4:
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] ERROR: Trama demasiado corta ({len(frame_bytes)} bytes). Mínimo necesario: 4 bytes")
        return None, False
    
    #Paso 2: Verificar que el primer byte sea STX (0x02)
    if frame_bytes[0] != 0x02:
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] ERROR: Primer byte no es STX (recibido: 0x{frame_bytes[0]:02X}, esperado: 0x02)")
        return None, False
    
    #Paso 3: Buscar la posición del byte ETX (0x03) en la trama
    etx_pos = -1
    for i in range(1, len(frame_bytes) - 1):  # -1 porque después del ETX debe venir el LRC
        if frame_bytes[i] == 0x03:  # ETX encontrado
            etx_pos = i
            break
    
    #Paso 4: Verificar que se encontró el ETX
    if etx_pos == -1:
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] ERROR: No se encontró ETX en la trama recibida")
        return None, False
    
    #Paso 5: Extraer los bytes de datos (entre STX y ETX)
    data_bytes = frame_bytes[1:etx_pos]
    #Paso 6: Extraer el LRC recibido (byte después del ETX)
    received_lrc = frame_bytes[etx_pos + 1]
    
    #Paso 7: Reconstruir el mensaje original (STX + DATA + ETX) para calcular LRC esperado
    message_with_delimiters = STX + data_bytes + ETX
    #Paso 8: Calcular el LRC esperado
    expected_lrc = calculate_lrc(message_with_delimiters)
    
    #Paso 9: Comparar el LRC recibido con el esperado
    if received_lrc != expected_lrc:
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] ERROR: LRC no coincide. Recibido: 0x{received_lrc:02X}, Esperado: 0x{expected_lrc:02X}")
        return None, False  # LRC no coincide, hay error en la transmisión
    
    #Paso 10: Decodificar los datos a string UTF-8
    try:
        data = data_bytes.decode('utf-8')
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] Trama parseada correctamente: '{data}' (LRC válido: 0x{received_lrc:02X})")
        return data, True
    except UnicodeDecodeError as e:
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] ERROR: No se pudo decodificar los datos como UTF-8: {e}")
        return None, False

def send_frame(socket_ref, data_string, central_messages=None):
    """
    Envía una trama completa a través de un socket usando el protocolo <STX><DATA><ETX><LRC>.
    
    Args:
        socket_ref: Referencia al socket donde enviar la trama
        data_string: String con los datos a enviar
        central_messages: (Opcional) Lista de mensajes para logs
    
    Returns:
        bool: True si el envío fue exitoso, False en caso contrario
    """
    try:
        #Paso 1: Construir la trama con el protocolo
        frame = build_frame(data_string)
        #Paso 2: Enviar la trama por el socket
        socket_ref.sendall(frame)
        #Paso 3: Mostrar confirmación en consola
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] Trama enviada correctamente: '{data_string}'")
        #Paso 4: Si hay lista de mensajes, agregar el mensaje
        if central_messages is not None:
            push_message(central_messages, f"[PROTOCOLO] Enviado: {data_string}")
        return True
    except Exception as e:
        #Paso 5: Manejar errores de envío
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] ERROR al enviar trama '{data_string}': {e}")
        if central_messages is not None:
            push_message(central_messages, f"[PROTOCOLO] ERROR enviando: {data_string} - {e}")
        return False

def receive_frame(socket_ref, central_messages=None, timeout=None):
    """
    Recibe una trama completa desde un socket y la parsea según el protocolo <STX><DATA><ETX><LRC>.
    
    Args:
        socket_ref: Referencia al socket de donde recibir la trama
        central_messages: (Opcional) Lista de mensajes para logs
        timeout: (Opcional) Timeout en segundos para la recepción
    
    Returns:
        tuple: (data_string, is_valid) donde:
            - data_string: String con los datos recibidos o None si hay error
            - is_valid: True si la trama es válida, False en caso contrario
    """
    try:
        #Paso 1: Configurar timeout si se especifica
        if timeout is not None:
            socket_ref.settimeout(timeout)
        else:
            socket_ref.settimeout(None)
        
        #Paso 2: Recibir los bytes del socket (hasta 1024 bytes)
        frame_bytes = socket_ref.recv(1024)
        
        #Paso 3: Si no se recibieron datos, la conexión se cerró
        if not frame_bytes:
            if DEBUG_PROTOCOL:
                print("[PROTOCOLO] Conexión cerrada por el remoto (no se recibieron datos)")
            # Conexión realmente cerrada
            return None, False
        
        #Paso 4: Detectar ACK/NACK/EOT de un solo byte antes de parsear
        if frame_bytes == ACK:
            if central_messages is not None:
                push_message(central_messages, "[PROTOCOLO] ACK recibido")
            return "__ACK__", True
        if frame_bytes == NACK:
            if central_messages is not None:
                push_message(central_messages, "[PROTOCOLO] NACK recibido")
            return "__NACK__", True
        if frame_bytes == EOT:
            if central_messages is not None:
                push_message(central_messages, "[PROTOCOLO] EOT recibido")
            return "EOT", True

        #Paso 5: Parsear la trama recibida
        data, is_valid = parse_frame(frame_bytes)
        #Paso 6: Si hay lista de mensajes, agregar el mensaje
        if central_messages is not None and data is not None:
            push_message(central_messages, f"[PROTOCOLO] Recibido: {data} (Válido: {is_valid})")
        
        return data, is_valid
        
    except socket.timeout:
        #Paso 6: Manejar timeout
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] Timeout esperando trama (timeout={timeout}s)")
        # Señalizar timeout con un valor centinela
        return "__TIMEOUT__", False
    except Exception as e:
        #Paso 7: Manejar otros errores
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] ERROR al recibir trama: {e}")
        if central_messages is not None:
            push_message(central_messages, f"[PROTOCOLO] ERROR recibiendo: {e}")
        return None, False

def handshake_client(socket_ref, central_messages=None):
    """
    Realiza el handshake inicial (ENQ/ACK) desde el lado cliente.
    El cliente envía ENQ y espera ACK o NACK del servidor.
    
    Args:
        socket_ref: Referencia al socket de conexión
        central_messages: (Opcional) Lista de mensajes para logs
    
    Returns:
        bool: True si el handshake fue exitoso (se recibió ACK), False en caso contrario
    """
    try:
        #Paso 1: Enviar ENQ (Enquiry) al servidor
        if DEBUG_PROTOCOL:
            print("[PROTOCOLO] Enviando ENQ (handshake inicial)...")
        socket_ref.sendall(ENQ)
        
        #Paso 2: Esperar respuesta del servidor (ACK o NACK)
        response = socket_ref.recv(1)
        
        #Paso 3: Verificar la respuesta recibida
        if not response:
            if DEBUG_PROTOCOL:
                print("[PROTOCOLO] ERROR: No se recibió respuesta al ENQ")
            if central_messages is not None:
                push_message(central_messages, "[PROTOCOLO] ERROR: No respuesta al handshake ENQ")
            return False
        
        #Paso 4: Decodificar la respuesta
        if response == ACK:
            if DEBUG_PROTOCOL:
                print("[PROTOCOLO] Handshake exitoso: Servidor respondió ACK")
            if central_messages is not None:
                push_message(central_messages, "[PROTOCOLO] Handshake exitoso (ACK recibido)")
            return True
        elif response == NACK:
            if DEBUG_PROTOCOL:
                print("[PROTOCOLO] Handshake fallido: Servidor respondió NACK")
            if central_messages is not None:
                push_message(central_messages, "[PROTOCOLO] Handshake fallido (NACK recibido)")
            return False
        else:
            if DEBUG_PROTOCOL:
                print(f"[PROTOCOLO] ERROR: Respuesta de handshake inválida (recibido: 0x{response[0]:02X})")
            if central_messages is not None:
                push_message(central_messages, f"[PROTOCOLO] ERROR: Respuesta inválida al handshake")
            return False
            
    except Exception as e:
        #Paso 5: Manejar errores durante el handshake
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] ERROR durante handshake: {e}")
        if central_messages is not None:
            push_message(central_messages, f"[PROTOCOLO] ERROR en handshake: {e}")
        return False

def handshake_server(socket_ref, central_messages=None):
    """
    Realiza el handshake inicial (ENQ/ACK) desde el lado servidor.
    El servidor espera ENQ del cliente y responde con ACK.
    
    Args:
        socket_ref: Referencia al socket de conexión (cliente conectado)
        central_messages: (Opcional) Lista de mensajes para logs
    
    Returns:
        bool: True si el handshake fue exitoso, False en caso contrario
    """
    try:
        #Paso 1: Configurar timeout para el handshake
        socket_ref.settimeout(5)  # Esperar máximo 5 segundos por el ENQ
        
        #Paso 2: Esperar ENQ del cliente
        if DEBUG_PROTOCOL:
            print("[PROTOCOLO] Esperando ENQ del cliente...")
        enq = socket_ref.recv(1)
        
        #Paso 3: Verificar que se recibió ENQ
        if not enq or enq != ENQ:
            if DEBUG_PROTOCOL:
                print(f"[PROTOCOLO] ERROR: No se recibió ENQ válido (recibido: {enq.hex() if enq else 'vacío'})")
            if central_messages is not None:
                push_message(central_messages, "[PROTOCOLO] ERROR: ENQ inválido o no recibido")
            return False
        
        #Paso 4: Responder con ACK al cliente
        if DEBUG_PROTOCOL:
            print("[PROTOCOLO] ENQ recibido. Enviando ACK...")
        socket_ref.sendall(ACK)
        if DEBUG_PROTOCOL:
            print("[PROTOCOLO] Handshake exitoso: ACK enviado al cliente")
        if central_messages is not None:
            push_message(central_messages, "[PROTOCOLO] Handshake exitoso (ENQ recibido, ACK enviado)")
        
        #Paso 5: Restaurar timeout normal (None = blocking)
        socket_ref.settimeout(None)
        return True
        
    except socket.timeout:
        #Paso 6: Manejar timeout esperando ENQ
        if DEBUG_PROTOCOL:
            print("[PROTOCOLO] ERROR: Timeout esperando ENQ del cliente")
        if central_messages is not None:
            push_message(central_messages, "[PROTOCOLO] ERROR: Timeout en handshake (no se recibió ENQ)")
        return False
    except Exception as e:
        #Paso 7: Manejar otros errores
        if DEBUG_PROTOCOL:
            print(f"[PROTOCOLO] ERROR durante handshake del servidor: {e}")
        if central_messages is not None:
            push_message(central_messages, f"[PROTOCOLO] ERROR en handshake servidor: {e}")
        return False

def send_ack(socket_ref):
    """Envía ACK (confirmación positiva) por el socket."""
    socket_ref.sendall(ACK)
    if DEBUG_PROTOCOL:
        print("[PROTOCOLO] ACK enviado")

def send_nack(socket_ref):
    """Envía NACK (confirmación negativa) por el socket."""
    socket_ref.sendall(NACK)
    if DEBUG_PROTOCOL:
        print("[PROTOCOLO] NACK enviado")

def send_eot(socket_ref):
    """Envía EOT (End of Transmission) para indicar cierre de conexión."""
    socket_ref.sendall(EOT)
    if DEBUG_PROTOCOL:
        print("[PROTOCOLO] EOT enviado (fin de transmisión)")

def cleanup_disconnected_drivers():
    """Limpia drivers que no han enviado peticiones recientemente."""
    while True:
        try:
            time.sleep(30)  # Verificar cada 30 segundos
            current_time = time.time()
            
            with active_cp_lock:
                # Obtener drivers que no han enviado peticiones en los últimos 60 segundos
                drivers_to_remove = set()
                for driver_id in connected_drivers.copy():
                    # Buscar la última petición de este driver
                    last_request_time = 0
                    for req in driver_requests:
                        if req.get('user_id') == driver_id:
                            # Usar timestamp actual como aproximación
                            last_request_time = current_time
                    
                    # Si no hay peticiones recientes, marcar para eliminar
                    if current_time - last_request_time > 60:
                        drivers_to_remove.add(driver_id)
                
                # Eliminar drivers desconectados
                for driver_id in drivers_to_remove:
                    connected_drivers.discard(driver_id)
                    # Liberar asignaciones de CPs si el driver estaba asignado
                    for cp_id, assigned_driver in list(cp_driver_assignments.items()):
                        if assigned_driver == driver_id:
                            force_release_cp_session(cp_id, None, reason="Driver inactivo", target_status='ACTIVADO', notify_driver=False)
                            print(f"[CENTRAL] Driver {driver_id} desconectado. CP {cp_id} liberado.")
                
                if drivers_to_remove:
                    print(f"[CENTRAL] Drivers desconectados eliminados: {drivers_to_remove}")
                    
        except Exception as e:
            print(f"[CENTRAL] Error en limpieza de drivers: {e}")

def reconcile_cp_states(central_messages):
    """
    Reconciliador periódico que ajusta el estado de los CPs según la actividad reciente
    del monitor (socket) y del engine (telemetría). El objetivo es garantizar la tabla de
    verdad especificada en la guía de corrección:
        Monitor_OK & Engine_OK  -> ACTIVADO (verde)
        Monitor_OK & Engine_KO  -> AVERIADO (rojo)
        Monitor_KO  (Engine OK o KO) -> DESCONECTADO (gris)
    Además respeta estados manuales críticos como FUERA_DE_SERVICIO o sesiones en curso.
    """
    while True:
        try:
            now = time.time()
            cps = database.get_all_cps()

            for cp in cps:
                cp_id = cp.get('id')
                if not cp_id:
                    continue

                current_status = cp.get('status') or 'DESCONECTADO'
                driver_attached = cp.get('driver_id')
                authorized_grace = None

                with active_cp_lock:
                    sess = current_sessions.get(cp_id)
                    if sess and not driver_attached:
                        driver_attached = sess.get('driver_id')
                    if sess:
                        authorized_grace = sess.get('authorized_since')
                    session_exists = cp_id in current_sessions and sess is not None
                pending_disconnect_at = pending_monitor_disconnects.get(cp_id)

                # Determinar si el monitor sigue vivo (socket activo y latido reciente)
                with active_cp_lock:
                    monitor_connected = cp_id in active_cp_sockets
                last_monitor = monitor_last_seen.get(cp_id)
                monitor_alive = False
                if monitor_connected:
                    if last_monitor is None:
                        monitor_alive = True
                    else:
                        monitor_alive = (now - last_monitor) <= (MONITOR_HEARTBEAT_TIMEOUT + 2.5)

                # Determinar si el engine sigue vivo (telemetría reciente)
                last_engine = engine_last_seen.get(cp_id)
                engine_state_flag = engine_health_status.get(cp_id)
                engine_alive = True
                if engine_state_flag == 'KO':
                    engine_alive = False
                elif session_exists or current_status in ['RESERVADO', 'SUMINISTRANDO']:
                    grace_ok = False
                    if authorized_grace is not None and (now - authorized_grace) <= (ENGINE_TELEMETRY_TIMEOUT * 2):
                        grace_ok = True
                    if last_engine is not None and (now - last_engine) <= ENGINE_TELEMETRY_TIMEOUT:
                        grace_ok = True
                    engine_alive = grace_ok
                else:
                    engine_alive = True

                target_status = None

                # 1) Si el monitor ha caído, el estado debe ser DESCONECTADO (tras gracia)
                if not monitor_alive:
                    if current_status == 'FUERA_DE_SERVICIO': 
                        target_status = None
                    elif current_status != 'DESCONECTADO':
                        in_session = session_exists or current_status in ['RESERVADO', 'SUMINISTRANDO']
                        if in_session and engine_alive:
                            target_status = None  # mantener estado durante la carga si engine sigue informando
                        elif pending_disconnect_at and (now - pending_disconnect_at) <= MONITOR_HEARTBEAT_TIMEOUT:
                            target_status = None  # aún en gracia
                        else:
                            target_status = 'DESCONECTADO'

                else:
                    # 2) Monitor OK. Respetar estados manuales (Fuera de servicio) hasta nueva orden
                    if current_status == 'FUERA_DE_SERVICIO':
                        target_status = None
                    # Durante suministro, solo degradar si detectamos engine KO
                    elif current_status == 'SUMINISTRANDO':
                        if not engine_alive:
                            target_status = 'AVERIADO'
                    # Estado reservado: mantener mientras haya driver asignado
                    elif current_status == 'RESERVADO':
                        if not engine_alive and not session_exists:
                            target_status = 'AVERIADO'
                    else:
                        # Situaciones normales: decidir entre ACTIVADO y AVERIADO
                        if not engine_alive:
                            if current_status != 'AVERIADO':
                                target_status = 'AVERIADO'
                        else:
                            if current_status in ['DESCONECTADO', 'AVERIADO']:
                                # Si no hay driver asociado, volvemos a ACTIVADO
                                if not driver_attached:
                                    target_status = 'ACTIVADO'

                if target_status and target_status != current_status:
                    try:
                        database.update_cp_status(cp_id, target_status)
                        push_message(central_messages, f"[RECON] CP {cp_id}: {current_status} -> {target_status}")
                    except Exception as e:
                        push_message(central_messages, f"[RECON] Error actualizando {cp_id} a {target_status}: {e}")
                if monitor_alive:
                    pending_monitor_disconnects.pop(cp_id, None)

            time.sleep(RECONCILE_INTERVAL)
        except Exception as e:
            push_message(central_messages, f"[RECON] Error reconciliando estados: {e}")
            time.sleep(RECONCILE_INTERVAL)

# --- Funciones del Panel de Monitorización ---
def clear_screen():
    """Limpia la pantalla de la terminal."""
    os.system('cls' if os.name == 'nt' else 'clear')

def get_status_color(status):
    """Devuelve un 'color' para el panel basado en el estado."""
    # Códigos de escape ANSI para colores en la terminal
    colors = {
        "ACTIVADO": "\033[92m",      # Verde
        "DESCONECTADO": "\033[90m", # Gris
        "SUMINISTRANDO": "\033[94m",# Azul
        "AVERIADO": "\033[91m",      # Rojo
        "FUERA_DE_SERVICIO": "\033[38;5;208m", # Naranja (256-color)
        "RESERVADO": "\033[96m"      # 
    }
    END_COLOR = "\033[0m"
    return f"{colors.get(status, '')}{status}{END_COLOR}"

def display_panel(central_messages, driver_requests):
    """Muestra el estado de todos los CPs en una matriz y los mensajes del sistema."""
    
    # --- Parámetros de la Matriz ---
    GRID_COLUMNS = 3  # Número de CPs por fila. Puedes ajustarlo (ej. 4 o 5)
    CELL_WIDTH = 28   # Ancho de cada celda en caracteres. Ajústalo a tu gusto
    
    while True:
        clear_screen()
        print("--- PANEL DE MONITORIZACIÓN DE EV CHARGING ---")
        
        # 1. --- Sección Matriz de Puntos de Recarga (CPs) ---
        print(f"--- MATRIZ DE PUNTOS DE RECARGA (CPs) [Columnas={GRID_COLUMNS}] ---")
        all_cps = database.get_all_cps()
        
        if not all_cps:
            print("No hay Puntos de Recarga registrados.")
            print("=" * ((CELL_WIDTH + 3) * GRID_COLUMNS)) # Borde inferior
        else:
            # Imprimir el borde superior de la matriz
            print("=" * ((CELL_WIDTH + 3) * GRID_COLUMNS))

            # Iterar por los CPs en filas de GRID_COLUMNS
            for i in range(0, len(all_cps), GRID_COLUMNS):
                row_cps = all_cps[i:i + GRID_COLUMNS]
                
                # Preparar las líneas de texto para la fila actual
                line_ids = ""
                line_locations = ""
                line_status = ""
                line_supply = ""

                for cp in row_cps:
                    # Extraer datos del CP
                    cp_id = cp.get('id', 'N/A')
                    location = cp.get('location', 'N/A')[:CELL_WIDTH-2] # Truncar ubicación
                    status = cp.get('status', 'DESCONECTADO')
                    
                    # Obtener representaciones visuales
                    emoji = get_status_emoji(status)
                    colored_status = get_status_color(status) # (ej. \033[92mACTIVADO\033[0m)
                    
                    # --- Lógica de alineación ---

                    # Línea de IDs
                    line_ids += f"| {cp_id:<{CELL_WIDTH}} "

                    # Línea de Ubicaciones
                    line_locations += f"| {location:<{CELL_WIDTH}} "

                    # Línea de Estado (CORREGIDA PARA CÓDIGOS DE COLOR)
                    prefix_str = f"Color: {emoji} "
                    status_visible_len = len(status)
                    padding_len = CELL_WIDTH - (len(prefix_str) + status_visible_len)
                    if padding_len < 0:
                        padding_len = 0
                    padding = " " * padding_len
                    line_status += f"| {prefix_str}{colored_status}{padding}"
                    
                    # Línea de Suministro
                    if status == 'SUMINISTRANDO':
                        kwh = cp.get('kwh', 0.0)
                        importe = cp.get('importe', 0.0)
                        driver = cp.get('driver_id', 'N/A')
                        supply_str = f"{driver} | {kwh:.1f}kWh | {importe:.1f}€"
                        line_supply += f"| {supply_str[:CELL_WIDTH]:<{CELL_WIDTH}} "
                    else:
                        line_supply += f"| {' ':<{CELL_WIDTH}} " # Celda vacía para alinear

                # Imprimir las líneas de la fila
                print(line_ids + "|")
                print(line_locations + "|")
                print(line_status + "|")
                
                # CORRECCIÓN: Imprimir siempre la línea de suministro (incluso vacía)
                print(line_supply + "|")
                
                # --- CORRECCIÓN: Lógica del borde inferior ---
                is_last_row = (i + GRID_COLUMNS) >= len(all_cps)
                
                if is_last_row:
                    # Para la última fila, calcular borde basado en celdas *en esta fila*
                    num_cells_in_row = len(row_cps)
                    print("=" * ((CELL_WIDTH + 3) * num_cells_in_row))
                else:
                    # Para una fila completa, imprimir un borde de ancho completo
                    print("=" * ((CELL_WIDTH + 3) * GRID_COLUMNS))
        
        # --- Resto del panel (Drivers, Peticiones, Mensajes) ---
        # (Esta parte es la misma que ya tenías)
        
        print("\n*** DRIVERS CONECTADOS ***")
        with active_cp_lock:
            if connected_drivers: #
                for driver_id in connected_drivers:
                    assigned_cp = None
                    for cp_id, assigned_driver in cp_driver_assignments.items(): #
                        if assigned_driver == driver_id:
                            assigned_cp = cp_id
                            break
                    if assigned_cp:
                        print(f"Driver {driver_id} -> CP {assigned_cp} (ASIGNADO)")
                    else:
                        print(f"Driver {driver_id} (DISPONIBLE)")
            else:
                print("No hay drivers conectados.")
        
        print("-" * 80)
        print("\n*** PETICIONES DE CONDUCTORES EN CURSO (Kafka) ***")
        if driver_requests: #
            for req in driver_requests:
                print(f"[{req['timestamp']}] Driver {req['user_id']} solicita recarga en CP {req['cp_id']}")
        else:
            print("No hay peticiones pendientes.")
        
        print("-" * 80)
        print("\n*** MENSAJES DEL SISTEMA ***")
        if central_messages: 
            protocol_msgs = []
            other_msgs = []
            for entry in central_messages:
                # Soporte dual: si es dict (nuevo) o str (viejo/compatibilidad)
                msg_text = entry['msg'] if isinstance(entry, dict) else str(entry)
                
                if "[PROTOCOLO]" in msg_text or "PROTOCOLO" in msg_text or "Handshake" in msg_text:
                    protocol_msgs.append(msg_text)
                else:
                    other_msgs.append(msg_text)
            
            if other_msgs:
                for msg in other_msgs[-7:]:
                    print(msg)
        
        print("-" * 80)
        print("\n*** MENSAJES DEL PROTOCOLO (últimos 7) ***")
        print("-" * 80)
        if central_messages:
            protocol_msgs = []
            for msg in central_messages:
                if "[PROTOCOLO]" in msg or "PROTOCOLO" in msg or "Handshake" in msg:
                    protocol_msgs.append(msg)
            
            if protocol_msgs:
                for msg in protocol_msgs[-7:]:
                    clean_msg = msg.replace("[PROTOCOLO] ", "")
                    clean_msg = clean_msg.replace("Handshake exitoso (ENQ recibido, ACK enviado)", "Handshake exitoso")
                    clean_msg = clean_msg.replace("Recibido: ", "← ")
                    clean_msg = clean_msg.replace("Enviado: ", "→ ")
                    if clean_msg.startswith("← REGISTER#") or clean_msg.startswith("→ REGISTER#"):
                        continue
                    if "Handshake exitoso" in clean_msg or "Realizando handshake" in clean_msg:
                        continue
                    if "ERROR recibiendo:" in clean_msg and "WinError 10054" in clean_msg:
                        clean_msg = "⚠ Conexión cerrada (reconexión automática)"
                    elif "ERROR recibiendo:" in clean_msg:
                        clean_msg = clean_msg.replace("ERROR recibiendo: ", "⚠ ")
                    print(f"  {clean_msg}")
            else:
                print("  No hay mensajes del protocolo.")
        
        print("="*80)
        print("Comandos: [P]arar <CP_ID> | [R]eanudar <CP_ID> | [PT] Parar todos | [RT] Reanuduar todos | [Q]uit")
        print(f"Última actualización: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(2) # El panel se refresca cada 2 segundos
# --- Funciones de Kafka ---

# Funcion de Kafka para enviar el estado de la red a todos los drivers
def broadcast_network_status(kafka_broker, producer):
    """
    Envía periódicamente el estado de todos los CPs a un topic público.
    """
    #Paso 1: Enviar el estado de la red a todos los drivers
    while True:
        try:
            all_cps = database.get_all_cps()
            # Paso 1.1: Creamos una lista simplificada solo con lo que el driver necesita
            status_list = [{'id': cp['id'], 'status': cp['status'], 'location': cp['location']} for cp in all_cps]
            
            message = {'type': 'NETWORK_STATUS_UPDATE', 'cps': status_list}
            # Paso 1.2: Enviar el estado de la red a todos los drivers
            producer.send(KAFKA_TOPIC_NETWORK_STATUS, value=message)
        except Exception as e:
            # Paso 1.3: Mostrar mensaje de error en la consola
            print(f"[ERROR Broadcast] No se pudo enviar el estado de la red: {e}")
        
        time.sleep(5) # Paso 1.4: Envía la actualización cada 5 segundos



# Funcion de Kafka para enviar notificaciones a los drivers
def send_notification_to_driver(producer, driver_id, notification):
    """Envía una notificación solo al driver específico si está conectado."""
    # OBTENER EL TIPO DE MENSAJE
    msg_type = notification.get('type')

    #Paso 1: Verificar si el driver está conectado
    with active_cp_lock:
        # CORRECCIÓN: Si el mensaje es un TICKET o un ERROR,
        # debemos enviarlo SIEMPRE, aunque el driver esté desconectado.
        # Kafka se encargará de guardarlo.
        if msg_type not in ['TICKET', 'SUPPLY_ERROR', 'SESSION_CANCELLED']:
            if driver_id not in connected_drivers:
                print(f"[CENTRAL] Driver {driver_id} no está conectado. No se envía notificación: {notification['type']}")
                return False

    #Paso 2: Enviar la notificación al driver
    try:
        #Paso 2.1: Añadir el driver_id al mensaje para que el driver pueda filtrarlo
        notification['target_driver'] = driver_id
        producer.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=notification)
        producer.flush()
        #Paso 2.2: Mostrar mensaje de notificación en la consola
        if CENTRAL_VERBOSE:
            print(f"[CENTRAL] Notificación enviada a Driver {driver_id}: {notification['type']}")
        return True
    except Exception as e:
        #Paso 2.3: Mostrar mensaje de error en la consola
        print(f"[CENTRAL] Error enviando notificación a Driver {driver_id}: {e}")
        return False


#  Los topics del sistema son:
# - driver_requests (drivers → central): peticiones de recarga
# - cp_telemetry (engine → central): telemetría por segundo + eventos
# - driver_notifications (central → drivers): respuestas y tickets
# - network_status (central → drivers): estado global de CPs (cada 5s)
def process_kafka_requests(kafka_broker, central_messages, driver_requests,producer):
    """
      Central
        - Producer (compartido): shared_producer_ref
           Envía a driver_notifications y network_status
        - Consumer: process_kafka_requests()
           Lee de driver_requests y cp_telemetry
    """
    # Paso 1: Cargar los mensajes de los topics en el consumer
    try:
        consumer = KafkaConsumer(
            KAFKA_TOPIC_REQUESTS, # driver_requests
            KAFKA_TOPIC_STATUS, # cp_telemetry
            bootstrap_servers=[kafka_broker], #dirección y puerto del broker Kafka (ej: 127.0.0.1:9092)
            auto_offset_reset='latest', # lee solo lo último (no histórico)
            group_id='central-processor', #los consumers del mismo grupo se reparten los mensajes
            value_deserializer=lambda x: json.loads(x.decode('utf-8')) #bytes → JSON → dict
        )
        central_messages.append(f"Kafka Consumer: Conectado al broker {kafka_broker}")
    except Exception as e:
        central_messages.append(f"ERROR: No se pudo conectar a Kafka ({kafka_broker}): {e}")
        return


    # Paso 2: Procesar los mensajes 
    for message in consumer:
        try:
            payload = message.value
            topic = message.topic
            # Extraer IP de origen del mensaje (si existe), si no, usar fallback
            origin_ip = payload.get('source_ip') or "Kafka(Unknown)"
            # Seva: LÓGICA DE DESCIFRADO Y SEGURIDAD
            # Verificamos si el mensaje viene con la bandera de encriptado
            if isinstance(payload, dict) and payload.get('encrypted') is True:
                cp_id_cifrado = payload.get('cp_id') # Este ID viene en claro
                ciphertext = payload.get('ciphertext')
                
                # 1. Buscar la clave simétrica de ESTE CP en la Base de Datos
                key = database.get_cp_symmetric_key(cp_id_cifrado)
                
                if key:
                    try:
                        f = Fernet(key)
                        # 2. Intentar Descifrar
                        decrypted_bytes = f.decrypt(ciphertext.encode('utf-8'))
                        
                        # 3. Reemplazar el payload cifrado por el JSON original
                        payload = json.loads(decrypted_bytes.decode('utf-8'))
                        
                    except (InvalidToken, Exception) as e:
                        #ERROR DE SEGURIDAD (Clave incorrecta o datos corruptos) 
                        err_msg = f"ERROR SEGURIDAD: Fallo al descifrar mensaje de {cp_id_cifrado}. Clave inválida o desincronizada."
                        print(f"[CENTRAL] {err_msg}")
                        
                        # Mostrar alerta en el Front-end (Central Messages)
                        push_message(central_messages, f"{err_msg}")

                        # Auditoría del incidente
                        log_audit_event(
                            source_ip=origin_ip,
                            action="ERROR_DESCIFRADO",
                            description=f"Fallo criptográfico con CP {cp_id_cifrado}. La clave usada por el CP no coincide con la BD.",
                            cp_id=cp_id_cifrado
                        )
                        continue # IMPORTANTE: Descartar mensaje, no procesarlo
                else:
                    #ERROR: No hay clave en BD 
                    err_msg = f"ERROR: Recibido mensaje cifrado de {cp_id_cifrado} pero no existe clave en BD (¿Fue revocada?)."
                    print(f"[CENTRAL] {err_msg}")
                    push_message(central_messages, f"{err_msg}")
                    continue
            
           

            # Paso 2.1: Procesar las peticiones de drivers (driver_requests)
            if topic == KAFKA_TOPIC_REQUESTS:
                cp_id = payload.get('cp_id') # ID del CP solicitado
                user_id = payload.get('user_id') # ID del driver que solicita la recarga
                action = (payload.get('type') or '').upper() # Tipo de acción (REQUEST_CHARGE, STOP_CHARGE, etc.)
                ts = time.strftime('%H:%M:%S') # Usar 'timestamp' para compatibilizar con display_panel
                driver_requests.append({'cp_id': cp_id, 'user_id': user_id, 'timestamp': ts})
                # Log inmediato en consola para trazabilidad
                if CENTRAL_VERBOSE:
                    print(f"[CENTRAL] Solicitud recibida del driver {user_id} para CP {cp_id}...")
                # Si el driver cierra su app, liberar reservas inmediatamente
                if action == 'DRIVER_QUIT':
                    with active_cp_lock:
                        connected_drivers.discard(user_id)
                    log_audit_event(
                        source_ip=origin_ip,
                        action="DRIVER_DESCONEXION",
                        description=f"El conductor {user_id} cerró la sesión voluntariamente.",
                        cp_id=None
                    )
                    # Liberar cualquier sesión asociada al driver
                    release_targets = []
                    with active_cp_lock:
                        for cp_k, sess in list(current_sessions.items()):
                            if sess.get('driver_id') == user_id:
                                release_targets.append(cp_k)
                    for cp_k in release_targets:
                        force_release_cp_session(cp_k, central_messages,
                                                 reason="Driver desconectado",
                                                 target_status='ACTIVADO',
                                                 notify_driver=False)
                    # eliminar cualquier petición pendiente del driver
                    driver_requests[:] = [req for req in driver_requests if req.get('user_id') != user_id]
                    continue

                #Paso 2.1.1: Registrar/actualizar que el driver está conectado
                with active_cp_lock:
                    connected_drivers.add(user_id)
                #Paso 2.1.2: Verificar si el driver ya está conectado a otro CP (por sesiones activas)
                driver_already_connected = any(sess.get('driver_id') == user_id for sess in current_sessions.values())
                if driver_already_connected:
                    notify = {"type": "AUTH_DENIED", "cp_id": cp_id, "user_id": user_id, "reason": "Driver ya conectado a otro CP"}
                    send_notification_to_driver(producer, user_id, notify)
                    central_messages.append(f"DENEGADO: Driver {user_id} -> CP {cp_id} (ya conectado a otro CP)")
                    print(f"[CENTRAL] DENEGACIÓN enviada a Driver {user_id} para CP {cp_id} (ya conectado a otro CP)")
                    #Eliminar peticiones procesadas de forma segura (sin índices)
                    driver_requests[:] = [req for req in driver_requests if not (req.get('cp_id') == cp_id and req.get('user_id') == user_id)]
                    continue
                #Paso 2.1.3: Verificar si el CP ya está siendo usado por otro driver (sesión activa)
                if cp_id in current_sessions:
                    notify = {"type": "AUTH_DENIED", "cp_id": cp_id, "user_id": user_id, "reason": "CP ya en uso por otro driver"}
                    #Paso 2.1.3.1: Enviar notificación de denegación al driver
                    send_notification_to_driver(producer, user_id, notify)
                    #Paso 2.1.3.2: Agregar mensaje de denegación a la lista de mensajes
                    central_messages.append(f"DENEGADO: Driver {user_id} -> CP {cp_id} (CP ya en uso)")
                    #Paso 2.1.3.3: Mostrar mensaje de denegación en la consola
                    print(f"[CENTRAL] DENEGACIÓN enviada a Driver {user_id} para CP {cp_id} (CP ya en uso)")
                    #Paso 2.1.3.4: Eliminar peticiones procesadas de forma segura
                    driver_requests[:] = [req for req in driver_requests if not (req.get('cp_id') == cp_id and req.get('user_id') == user_id)]
                    continue


                
                #Paso 2.2: Cargar el estado del CP
                cp_status = database.get_cp_status(cp_id)
                

                #Paso 2.3: Autorizar solo si CP está ACTIVADO y disponible
                if cp_status == 'ACTIVADO' and (action in ['', 'REQUEST_CHARGE']):
                    if CENTRAL_VERBOSE:
                        print(f"[CENTRAL] Enviando START_SESSION al CP...")
                    #Paso 2.3.1: Reservar el CP inmediatamente
                    database.update_cp_status(cp_id, 'RESERVADO') # Reservamos el CP inmediatamente
                    #Paso 2.3.2: Registrar driver como conectado y abrir sesión en el CP
                    with active_cp_lock:
                        connected_drivers.add(user_id)
                        current_sessions[cp_id] = { 'driver_id': user_id, 'status': 'authorized', 'authorized_since': time.time() }
                    try:
                        engine_last_seen[cp_id] = time.time()
                        engine_health_status[cp_id] = 'OK'
                    except Exception:
                        pass
                    
                    #Paso 2.3.3: Enviar comando de autorización al Monitor del CP vía SOCKET usando protocolo
                    if cp_id in active_cp_sockets:
                        try:
                            cp_socket = active_cp_sockets[cp_id]
                            # Emular START_SESSION semánticamente con AUTORIZAR_SUMINISTRO hacia el CP
                            auth_command = f"AUTORIZAR_SUMINISTRO#{user_id}"
                            # Usar protocolo para enviar comando
                            if send_frame(cp_socket, auth_command, central_messages):
                                if CENTRAL_VERBOSE:
                                    print(f"[CENTRAL] Comando AUTORIZAR_SUMINISTRO enviado a Monitor de CP {cp_id} para Driver {user_id}")
                                    print(f"[CENTRAL] Esperando confirmación del CP...")
                            else:
                                central_messages.append(f"ERROR: No se pudo enviar comando de autorización a CP {cp_id}")
                        except Exception as e:
                            central_messages.append(f"ERROR: No se pudo enviar comando de autorización a CP {cp_id}: {e}")
                    #Paso 2.3.4: Enviar notificación de autorización al driver
                    notify = {"type": "AUTH_OK", "cp_id": cp_id, "user_id": user_id, "message": "Autorizado"}
                    #Paso 2.3.4.1: Enviar notificación de autorización al driver
                    # Seva: AUDITORÍA: DRIVER AUTORIZACIÓN OK ***
                    log_audit_event(
                        source_ip=origin_ip,
                        action="DRIVER_AUTORIZACION_OK",
                        description=f"Recarga autorizada. CP reservado y esperando inicio de suministro.",
                        cp_id=cp_id
                    )
                    # ****************************************************************
                    send_notification_to_driver(producer, user_id, notify)
                    #Paso 2.3.4.2: Agregar mensaje de autorización a la lista de mensajes
                    central_messages.append(f"AUTORIZADO: Driver {user_id} -> CP {cp_id}")
                    #Paso 2.3.4.3: Mostrar mensaje de autorización en la consola
                    if CENTRAL_VERBOSE:
                        print(f"[CENTRAL] AUTORIZACIÓN enviada a Driver {user_id} para CP {cp_id}")
                else:
                    #Paso 2.3.5: Enviar notificación de denegación al driver
                    print(f"[CENTRAL] Enviando DENEGACIÓN al driver...")
                    notify = {"type": "AUTH_DENIED", "cp_id": cp_id, "user_id": user_id, "reason": cp_status}
                    #Paso 2.3.5.1: Enviar notificación de denegación al driver
                    # Seva: AUDITORÍA: DRIVER AUTORIZACIÓN FALLIDA ***
                    log_audit_event(
                        source_ip=origin_ip,
                        action="DRIVER_AUTORIZACION_FALLIDA",
                        description=f"Solicitud rechazada. Razón: CP en estado {cp_status}.",
                        cp_id=cp_id
                    )
                    # ********************************************************************
                    send_notification_to_driver(producer, user_id, notify)
                    #Paso 2.3.5.2: Agregar mensaje de denegación a la lista de mensajes
                    central_messages.append(f"DENEGADO: Driver {user_id} -> CP {cp_id} (estado={cp_status})")
                    #Paso 2.3.5.3: Mostrar mensaje de denegación en la consola
                    print(f"[CENTRAL] DENEGACIÓN enviada a Driver {user_id} para CP {cp_id} (estado={cp_status})")
                    # Eliminar peticiones procesadas de forma segura
                    driver_requests[:] = [req for req in driver_requests if not (req.get('cp_id') == cp_id and req.get('user_id') == user_id)]




            #Paso 2.4: Procesar las telemetrías de los CPs (cp_telemetry)
            elif topic == KAFKA_TOPIC_STATUS:
                msg_type = payload.get('type', '').upper() # Tipo de mensaje (CONSUMO, SESSION_STARTED, SUPPLY_END, etc.)
                cp_id = payload.get('cp_id') # ID del CP

                payload_driver_id = payload.get('user_id') or payload.get('driver_id')

                if cp_id:
                    try:
                        engine_last_seen[cp_id] = time.time()
                        engine_health_status[cp_id] = 'OK'
                    except Exception:
                        pass
                
                #Seva: he modificado esta parte para poder procesar la carga sin un usuario asignado
                #Paso 2.4.1: Procesar el consumo periódico (ENGINE envía cada segundo)
                if msg_type == 'CONSUMO':
                    # --- 🛑 EL POLICÍA: BLOQUEO DE SEGURIDAD 🛑 ---
                    # Verificamos el estado real en la Base de Datos
                    current_db_status = database.get_cp_status(cp_id)
                    
                    # Si el CP está roto (AVERIADO) o parado (FUERA_DE_SERVICIO), ¡NO PUEDE CARGAR!
                    if current_db_status in ['AVERIADO', 'FUERA_DE_SERVICIO', 'DESCONECTADO']:
                        print(f"[CENTRAL] 🚨 ALERTA DE SEGURIDAD: CP {cp_id} intentando cargar en estado {current_db_status}.")
                        
                        # 1. Enviamos orden de CORTE INMEDIATO al CP
                        # Usamos la función send_cp_command que ya tienes definida
                        # Nota: Necesitas asegurarte de tener acceso a send_cp_command o usar la lógica directa
                        if cp_id in active_cp_sockets:
                            try:
                                # Enviamos PARAR directamente por el socket
                                socket_ref = active_cp_sockets[cp_id]
                                send_frame(socket_ref, "PARAR#CENTRAL", central_messages)
                                print(f"[CENTRAL] 🛑 Orden de PARADA FORZOSA enviada a {cp_id}")
                            except:
                                pass
                        
                        # 2. Ignoramos este paquete de consumo (no cobramos ni registramos)
                        continue 
                    # -----------------------------------------------------
                    kwh = float(payload.get('kwh', 0)) # Consumo en kWh
                    importe = float(payload.get('importe', 0)) # Importe en euros
                    driver_id = payload.get('user_id') or payload.get('driver_id') # ID del driver

                    # Paso 2.4.1.1: Si el CP no está registrado, lo creamos automáticamente
                    current_status = database.get_cp_status(cp_id)
                    if current_status == 'NO_EXISTE' or current_status is None:
                        database.register_cp(cp_id, "Desconocida")
                        database.update_cp_status(cp_id, 'ACTIVADO')
                        push_message(central_messages, f"AUTOREGISTRO: CP {cp_id} registrado automáticamente (ubicación desconocida).")

                    # Paso 2.4.1.2: Actualiza BD (esto marcará SUMINISTRANDO)
                    # La BD registrará el consumo aunque sea "INVITADO" (facturación local)
                    database.update_cp_consumption(cp_id, kwh, importe, driver_id)
                    
                    # Paso 2.4.1.3: Actualizar estado de sesión a 'charging' si coincide driver
                    with active_cp_lock:
                        sess = current_sessions.get(cp_id)
                        if sess and sess.get('driver_id') == driver_id:
                            current_sessions[cp_id]['status'] = 'charging'
                            current_sessions[cp_id]['authorized_since'] = time.time()
                    pending_monitor_disconnects.pop(cp_id, None)

                    # Paso 2.4.1.4: Reenviar una notificación de consumo al driver a través de su topic
                    # CORRECCIÓN: SOLO SI NO ES INVITADO
                    if driver_id != "INVITADO":
                        try:
                            consumo_msg = {"type": "CONSUMO_UPDATE", "cp_id": cp_id, "user_id": driver_id, "kwh": kwh, "importe": importe}
                            # Paso 2.4.1.4.1: Enviar la notificación al driver
                            producer.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=consumo_msg)
                        except Exception as e:
                            push_message(central_messages, f"ERROR: no se pudo notificar consumo a driver {driver_id}: {e}")
                

                    # Paso 2.4.1.5: Recuperar precio real desde la BD (no calcularlo)
                    price = database.get_cp_price(cp_id)
                    price_str = f"{price:.2f} €/kWh" if price is not None else "N/A"
                    # Paso 2.4.1.5.1: Agregar mensaje de telemetría a la lista de mensajes
                    #push_message(central_messages,
                    #    f"TELEMETRÍA: CP {cp_id} - {kwh:.3f} kWh - {importe:.2f} € - driver {driver_id} - precio {price_str}"
                    #)

                # Paso 2.4.2: Procesar el inicio de sesión (opcional, informativo)
                elif msg_type == 'SESSION_STARTED':
                    # Paso 2.4.2.1: Robustez: si no viene driver_id en payload, úsalo de la sesión
                    driver_id = payload.get('user_id') or payload.get('driver_id')
                    if not driver_id:
                        with active_cp_lock:
                            # Paso 2.4.2.1.1: Obtener el driver_id de la sesión
                            sess = current_sessions.get(cp_id)
                            if sess:
                                driver_id = sess.get('driver_id')
                    # Paso 2.4.2.1.2: Actualizar el estado de sesión a 'charging' si coincide driver
                    with active_cp_lock:
                        sess = current_sessions.get(cp_id)
                        if sess and sess.get('driver_id') == driver_id:
                            current_sessions[cp_id]['status'] = 'charging'
                    # Paso 2.4.2.1.3: Agregar mensaje de inicio de sesión a la lista de mensajes
                    push_message(central_messages, f"SESIÓN INICIADA: CP {cp_id} con driver {driver_id}")

                # Paso 2.4.3: Procesar el fin de suministro
                elif msg_type == 'SUPPLY_END':
                    kwh = float(payload.get('kwh', 0)) # Consumo en kWh
                    importe = float(payload.get('importe', 0)) # Importe en euros
                    
                    session_data = current_sessions.get(cp_id)

                    if session_data:
                        # Si session_data es un diccionario (como muestra tu log), sacamos solo el ID
                        if isinstance(session_data, dict):
                            driver_id = session_data.get('driver_id', 'DESCONOCIDO')
                        else:
                            # Si fuera solo texto, lo usamos tal cual
                            driver_id = str(session_data)
                    else:
                        # Si no hay sesión en memoria, nos fiamos del Engine
                        driver_id = payload.get('driver_id', 'INVITADO')

                    current_status = database.get_cp_status(cp_id) # Estado del CP

                    # Paso 2.4.3.1: Si el CP está FUERA_DE_SERVICIO (Interrupción)
                    if current_status == 'FUERA_DE_SERVICIO':
                        # Paso 2.4.3.1.1: Crear el mensaje de error
                        error_msg = {
                            "type": "SUPPLY_ERROR",
                            "cp_id": cp_id,
                            "user_id": driver_id,
                            "reason": "Carga interrumpida: CP puesto fuera de servicio",
                            "kwh_partial": kwh,
                            "importe_partial": importe
                        }
                        
                        # Solo enviamos a Kafka si es un usuario con App
                        if driver_id != "INVITADO":
                            producer.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=error_msg)
                            producer.flush()
                        
                        # Paso 2.4.3.1.2: Agregar mensaje de error a la lista de mensajes
                        central_messages.append(
                            f"CARGA INTERRUMPIDA: CP {cp_id} - driver {driver_id} - Parcial: {kwh:.3f} kWh / {importe:.2f} €"
                        )
                        # Paso 2.4.3.1.3: Limpiar telemetría pero mantener estado FUERA_DE_SERVICIO
                        database.clear_cp_telemetry_only(cp_id)
                        
                    else:
                        # Paso 2.4.3.2: Caso normal: generar ticket y dejar CP disponible
                        database.clear_cp_consumption(cp_id)  # Esto pone estado en ACTIVADO

                        central_messages.append(
                            f"TICKET FINAL: CP {cp_id} - driver {driver_id} - {kwh:.3f} kWh - {importe:.2f} €"
                        )

                        # Paso 2.4.3.2.1: Notificar ticket normal
                        # CORRECCIÓN: Discriminación de usuario
                        if driver_id != "INVITADO":
                            try:
                                ticket_msg = {
                                    "type": "TICKET",
                                    "cp_id": cp_id,
                                    "user_id": driver_id,
                                    "kwh": kwh,
                                    "importe": importe
                                }
                                # Enviar el ticket al driver (App)
                                send_notification_to_driver(producer, driver_id, ticket_msg)
                                
                                # Seva: AUDITORÍA: FIN DE SUMINISTRO (TICKET APP) ***
                                log_audit_event(
                                    source_ip=origin_ip,
                                    action="SUMINISTRO_FINALIZADO",
                                    description=f"Recarga APP completada. Ticket: {kwh:.3f} kWh, {importe:.2f} €",
                                    cp_id=cp_id
                                )
                            except Exception as e:
                                central_messages.append(f"ERROR: no se pudo notificar ticket a driver {driver_id}: {e}")
                                print(f"[CENTRAL] EXCEPTION al enviar ticket: {e}")
                        else:
                            # CASO INVITADO: Solo Auditoría Interna
                            log_audit_event(
                                source_ip=origin_ip,
                                action="SUMINISTRO_MANUAL_FINALIZADO",
                                description=f"Recarga MANUAL (Invitado) completada. Ticket: {kwh:.3f} kWh, {importe:.2f} €",
                                cp_id=cp_id
                            )
                        
                        # Paso 2.4.3.2.1.2: Cerrar sesión y liberar la asignación del driver al CP
                        with active_cp_lock:
                            if cp_id in current_sessions:
                                del current_sessions[cp_id]   

                        # Paso 2.4.3.2.1.3: Actualizar estado del CP a ACTIVADO
                        database.update_cp_status(cp_id, 'ACTIVADO')

                # Paso 2.4.4: Procesar los eventos de avería / pérdida de conexión
                elif msg_type in ('AVERIADO', 'CONEXION_PERDIDA', 'FAULT'):
                    #Paso 2.4.4.1: Comprobar si hay suministro en curso
                    try:
                        engine_health_status[cp_id] = 'KO'
                        engine_last_seen.pop(cp_id, None)
                    except Exception:
                        pass
                    cp_data = database.get_all_cps()
                    cp_info = next((cp for cp in cp_data if cp['id'] == cp_id), None)

                    is_monitor_loss = msg_type == 'CONEXION_PERDIDA' and payload.get('component', '').upper() == 'MONITOR'
                    partial_kwh = float(payload.get('kwh', 0.0))
                    partial_importe = float(payload.get('importe', 0.0))
                    audit_action = "INCIDENCIA_DESCONEXION_MONITOR" if is_monitor_loss else "INCIDENCIA_AVERIA_ENGINE"
                    audit_reason = "Monitor desconectado" if is_monitor_loss else "Engine averiado"
                    
                    # AUDITORÍA: AVERÍA (IP REAL)
                    log_audit_event(
                        source_ip=origin_ip,
                        action=audit_action,
                        description=f"Incidencia crítica: {audit_reason}.",
                        cp_id=cp_id
                    )
                    if cp_info:
                        db_kwh = cp_info.get('kwh')
                        db_importe = cp_info.get('importe')
                        if db_kwh is not None:
                            try:
                                partial_kwh = float(db_kwh)
                            except Exception:
                                partial_kwh = db_kwh
                        if db_importe is not None:
                            try:
                                partial_importe = float(db_importe)
                            except Exception:
                                partial_importe = db_importe

                    supply_error_to_send = None

                    if cp_info and cp_info.get('status') == 'SUMINISTRANDO':
                        driver_id = cp_info.get('driver_id') or payload_driver_id
                        if driver_id:
                            #Paso 2.4.4.2: Notificar al conductor la interrupción
                            if is_monitor_loss:
                                reason = "Carga interrumpida: Monitor desconectado"
                            else:
                                reason = "Carga interrumpida: Engine averiado"
                            supply_error_to_send = reason
                            descriptor = "DESCONECTADO" if is_monitor_loss else "AVERIADO"
                            log_msg = (
                                f"INTERRUPCIÓN DURANTE SUMINISTRO en CP {cp_id}\n"
                                f"    → Estado: {descriptor}\n"
                            )
                            if driver_id:
                                log_msg += f"    → Driver: {driver_id}\n"
                            log_msg += (
                                f"    → Consumo parcial: {partial_kwh:.3f} kWh / {partial_importe:.2f} €\n"
                                f"    → Notificación {'enviada' if driver_id else 'no enviada (driver desconocido)'} ({reason})"
                            )
                            central_messages.append(log_msg)
                            print(f"[CENTRAL] {log_msg}")
                    else:
                        if is_monitor_loss:
                            msg = f"Monitor de CP {cp_id} desconectado - Estado actualizado a DESCONECTADO"
                            audit_action = "INCIDENCIA_DESCONEXION_MONITOR"
                            audit_reason = "Monitor desconectado"
                        else:
                            msg = f"AVERÍA detectada en CP {cp_id} - Estado actualizado a ROJO"
                            audit_action = "INCIDENCIA_AVERIA_ENGINE"
                            audit_reason = "Engine reporta avería"
                        central_messages.append(msg)
                        print(f"[CENTRAL] {msg}")

                    # Seva: AUDITORÍA: INTERRUPCIÓN DE CARGA / AVERÍA ***
                    log_audit_event(
                        source_ip=origin_ip,
                        action=audit_action,
                        description=f"Incidencia crítica. Razón: {audit_reason}. Consumo parcial: {partial_kwh:.3f} kWh.",
                        cp_id=cp_id
                    )
                    # ****************************************************
                    if is_monitor_loss:
                        force_release_cp_session(
                            cp_id,
                            central_messages,
                            reason="Monitor desconectado durante suministro" if cp_info else "Monitor desconectado",
                            target_status='DESCONECTADO',
                            supply_error_reason=supply_error_to_send,
                            partial_kwh=partial_kwh,
                            partial_importe=partial_importe,
                            driver_override=payload_driver_id
                        )
                    else:
                        database.update_cp_status(cp_id, 'AVERIADO')
                        force_release_cp_session(
                            cp_id,
                            central_messages,
                            reason="Engine reporta avería",
                            target_status='AVERIADO',
                            supply_error_reason=supply_error_to_send,
                            partial_kwh=partial_kwh,
                            partial_importe=partial_importe,
                            driver_override=payload_driver_id
                        )

        except Exception as e:
            central_messages.append(f"Error al procesar mensaje de Kafka: {e}")



# --- Funciones del Servidor de Sockets ---

# Funcion Socket para procesar los mensajes que llegan desde el CP (Monitor)
def process_socket_data2(data_string, cp_id, address, client_socket, central_messages, kafka_broker):
    """
    Procesa los mensajes que llegan desde el CP (Monitor).
    Ahora recibe el string de datos ya parseado del protocolo <STX><DATA><ETX><LRC>.
    """
    # Registrar actividad reciente del monitor
    try:
        monitor_last_seen[cp_id] = time.time()
    except Exception:
        pass
    #FASE 1: Verificar que hay datos válidos
    if not data_string:
        print(f"[CENTRAL] ERROR: Mensaje vacío recibido de CP {cp_id}")
        return
    
    #FASE 2: Parsear el mensaje recibido
    # Normalizar el comando (solo la parte del comando), pero mantenemos el resto
    parts = data_string.split('#')
    #Extraer el comando
    command = parts[0].upper() if parts else ""
    # Mostrar el mensaje recibido
    if CENTRAL_VERBOSE:
        print(f"[CENTRAL] Recibido de CP {cp_id}: {data_string}")
    #Mostrar el mensaje recibido en el panel de estado
    push_message(central_messages, f"CP {cp_id} -> CENTRAL: {data_string}")




    #FASE 2: Procesar el mensaje recibido
    

    #FASE 2.1: Reporte de avería desde el Monitor
    if command == 'FAULT':
        try:
            engine_health_status[cp_id] = 'KO'
            engine_last_seen.pop(cp_id, None)
        except Exception:
            pass
        
        #Cargar información del CP
        cp_data = database.get_all_cps()
        cp_info = next((cp for cp in cp_data if cp['id'] == cp_id), None)
        # 2.1.1 ¿Hay suministro en curso?
        if cp_info and cp_info.get('status') == 'SUMINISTRANDO':
            # Cargar información del driver asignado al CP
            driver_id = cp_info.get('driver_id')
            # Cargar información del consumo del CP
            kwh = cp_info.get('kwh', 0.0)
            # Cargar información del importe del CP
            importe = cp_info.get('importe', 0.0)
            
            # 2.1.2 Notificar al conductor la interrupción por avería
            try:
                # 2.1.2.1 Usar el producer compartido en lugar de crear uno nuevo
                if shared_producer_ref:
                    # 2.1.2.2 Crear el mensaje de error
                    error_msg = {
                        "type": "SUPPLY_ERROR",
                        "cp_id": cp_id,
                        "user_id": driver_id,
                        "reason": "Carga interrumpida: Engine averiado",
                        "kwh_partial": kwh,
                        "importe_partial": importe
                    }
                    send_notification_to_driver(shared_producer_ref, driver_id, error_msg)
                
                # 2.1.3 Log detallado en Central
                msg = (f" AVERÍA DURANTE SUMINISTRO en CP {cp_id}\n"
                      f"    → Estado: AVERIADO (ROJO)\n"
                      f"    → Driver: {driver_id}\n"
                      f"    → Consumo hasta avería: {kwh:.3f} kWh / {importe:.2f} €\n"
                      f"    → Notificación enviada al conductor")
                central_messages.append(msg)
                print(f"[CENTRAL] {msg}")
            # 2.1.4 Log del error
            except Exception as e:
                msg = f" Error al notificar avería a driver {driver_id}: {e}"
                central_messages.append(msg)
                print(f"[CENTRAL] {msg}")
            
            # 2.1.5 Limpiar consumo pero mantener estado AVERIADO
            database.update_cp_consumption(cp_id, 0, 0, None)
            
        else:
            # CP no estaba suministrando
            msg = f" AVERÍA en CP {cp_id} - Estado actualizado a ROJO"
            central_messages.append(msg)
        
        # Actualizar estado a AVERIADO y liberar sesión
        database.update_cp_status(cp_id, 'AVERIADO')
        force_release_cp_session(cp_id, central_messages, reason="Monitor reporta avería", target_status='AVERIADO')
        # Seva: AUDITORÍA: REPORTE DE AVERÍA
        source_ip = address[0] # IP del Monitor que reporta
        log_audit_event(
            source_ip=source_ip,
            action="CP_AVERIA_REPORTADA",
            description="Incidencia: Monitor reporta avería del Engine (KO). Carga interrumpida y CP en AVERIADO.",
            cp_id=cp_id
        )
        # ****************


    #FASE 2.2: Recuperación de avería desde el Monitor
    elif command == 'RECOVER':
        # Seva: AUDITORÍA: REPORTE DE RECUPERACIÓN (RECOVER) ***
        source_ip = address[0] # IP del Monitor que reporta
        log_audit_event(
            source_ip=source_ip,
            action="CP_RECUPERACION_REPORTADA",
            description="CP reporta recuperación de Engine. Estado actualizado a ACTIVADO y sesión liberada.",
            cp_id=cp_id
        )
        # ******************************************

        # 2.2.1: Al recuperar, liberar cualquier sesión y dejar el CP en ACTIVADO limpio
        force_release_cp_session(cp_id, central_messages, reason="Monitor recuperado", target_status='ACTIVADO')
        try:
            engine_health_status[cp_id] = 'OK'
            engine_last_seen[cp_id] = time.time()
        except Exception:
            pass
        # 2.2.2 Marcar evento de recuperación reciente para evitar parpadeo a DESCONECTADO
        try:
            recent_recover_events[cp_id] = time.time()
        except Exception:
            pass

    
    
    #FASE 2.3: Confirmaciones ACK/NACK de comandos
    elif command == 'ACK':
        if len(parts) > 1:
            action = parts[1]
            # 2.3.1 Reanudar el CP
            if action == 'REANUDAR':
                if CENTRAL_VERBOSE:
                    print(f"[CENTRAL]  CP {cp_id} confirmó REANUDAR. Actualizando a VERDE.")
                database.update_cp_status(cp_id, 'ACTIVADO')
                force_release_cp_session(cp_id, central_messages, reason="REANUDAR confirmado", target_status='ACTIVADO', notify_driver=False)
                central_messages.append(
                    f"CP {cp_id} confirmó REANUDAR. Estado actualizado a VERDE."
                )
                # Seva: AUDITORÍA: COMANDO REANUDAR CONFIRMADO ***
                log_audit_event(
                    source_ip=address[0],
                    action="COMANDO_CONFIRMADO",
                    description=f"CP confirmó REANUDAR. Estado final: ACTIVADO.",
                    cp_id=cp_id
                )
                # ***********************************************
                # Confirmar y limpiar pendiente si existía
                if pending_cp_commands.pop(cp_id, None):
                    push_message(central_messages, f"Comando REANUDAR confirmado por {cp_id}")
                # Registrar evento reciente para evitar parpadeo a DESCONECTADO si se reconecta justo después
                try:
                    recent_recover_events[cp_id] = time.time()
                except Exception:
                    pass
            # 2.3.2 Parar el CP
            elif action == 'PARAR':
                if CENTRAL_VERBOSE:
                    print(f"[CENTRAL]  CP {cp_id} confirmó PARAR. Actualizando a NARANJA (Out of Order).")
                # IMPORTANTE: Actualizar estado ANTES de cualquier otra operación
                # Esto asegura que el estado esté actualizado incluso si la conexión se cierra después
                database.update_cp_status(cp_id, 'FUERA_DE_SERVICIO')
                force_release_cp_session(cp_id, central_messages, reason="PARAR confirmado", target_status='FUERA_DE_SERVICIO')
                push_message(central_messages, f"CP {cp_id} confirmó PARAR. Estado actualizado a FUERA_DE_SERVICIO (NARANJA - Out of Order).")
                # Verificar que el estado se actualizó correctamente
                verify_status = database.get_cp_status(cp_id)
                if verify_status != 'FUERA_DE_SERVICIO':
                    print(f"[CENTRAL] WARNING: Estado no se actualizó correctamente. Esperado: FUERA_DE_SERVICIO, Actual: {verify_status}")
                else:
                    print(f"[CENTRAL] Estado verificado: CP {cp_id} está en {verify_status}")
                # Confirmar y limpiar pendiente si existía
                if pending_cp_commands.pop(cp_id, None):
                    push_message(central_messages, f"Comando PARAR confirmado por {cp_id}")
                # Seva: AUDITORÍA: COMANDO PARAR CONFIRMADO ***
                log_audit_event(
                    source_ip=address[0],
                    action="COMANDO_CONFIRMADO",
                    description=f"CP confirmó PARAR. Estado final: FUERA_DE_SERVICIO.",
                    cp_id=cp_id
                )
                # *********************************************

    elif command == 'NACK':
        if CENTRAL_VERBOSE:
            print(f"[CENTRAL]  CP {cp_id} RECHAZÓ el comando: {data_string}")
        central_messages.append(f" CP {cp_id} rechazó el comando: {data_string}")
        # Revertir estado si había actualización optimista
        try:
            pending = pending_cp_commands.pop(cp_id, None)
            if pending:
                prev_status = pending.get('prev_status')
                if prev_status:
                    database.update_cp_status(cp_id, prev_status)
                    push_message(central_messages, f"Revertido estado de {cp_id} a {prev_status} por NACK")
                    # Seva: AUDITORÍA: COMANDO RECHAZADO ***
                    source_ip = address[0] 
                    log_audit_event(
                        source_ip=source_ip,
                        action="COMANDO_RECHAZADO",
                        description=f"CP rechazó el comando {pending.get('command')}. Estado revertido a {prev_status}.",
                        cp_id=cp_id
                    )
                    # *************************************
        except Exception as e:
            print(f"[CENTRAL] WARNING: No se pudo revertir estado tras NACK para {cp_id}: {e}")

    
    
    #FASE 2.4: Consulta de asignación de driver
    elif command == 'CHECK_DRIVER':
        # 2.4.1 Verificar que exista el CP
        if len(parts) >= 2:
            # 2.4.2 Cargar el ID del CP
            requested_cp_id = parts[1]
            with active_cp_lock:
                # 2.4.3 Cargar la sesión del CP
                sess = current_sessions.get(requested_cp_id)
                assigned_driver = sess.get('driver_id') if sess else None
                # 2.4.4 Verificar que exista sesión y el driver esté conectado
                if sess and assigned_driver and assigned_driver in connected_drivers:
                    # Usar protocolo para enviar respuesta
                    send_frame(client_socket, assigned_driver, central_messages)
                    send_ack(client_socket)  # Confirmar recepción
                    # Seva: AUDITORÍA: CHECK_DRIVER EXITOSO ***
                    log_audit_event(
                        source_ip=address[0],
                        action="CONSULTA_DRIVER_OK",
                        description=f"Monitor/Engine confirmó driver {assigned_driver} asignado y conectado.",
                        cp_id=requested_cp_id
                    )
                    # ****************************************
                    print(f"[CENTRAL] Sesión válida para CP {requested_cp_id} con Driver {assigned_driver} (status={sess.get('status')})")
                else:
                    # Usar protocolo para enviar respuesta negativa
                    send_frame(client_socket, "NO_DRIVER", central_messages)
                    send_ack(client_socket)
                    # Seva: AUDITORÍA: CHECK_DRIVER FALLIDO ***
                    log_audit_event(
                        source_ip=address[0],
                        action="CONSULTA_DRIVER_FALLIDA",
                        description=f"Monitor/Engine consultó, sin driver activo.",
                        cp_id=requested_cp_id
                    )
                    # ****************************************
                    if assigned_driver:
                        print(f"[CENTRAL] Sesión encontrada pero driver no conectado para CP {requested_cp_id}")
                    else:
                        print(f"[CENTRAL] No hay sesión activa para CP {requested_cp_id}")
        else:
            # Usar protocolo para enviar respuesta negativa
            send_frame(client_socket, "NO_DRIVER", central_messages)
            send_ack(client_socket)

    # FASE 2.5: Consulta de sesión activa autorizada
    elif command == 'CHECK_SESSION':
        # 2.5.1 Verificar que exista el CP
        if len(parts) >= 2:
            # 2.5.2 Cargar el ID del CP
            requested_cp_id = parts[1]
            with active_cp_lock:
                # 2.5.3 Cargar la sesión del CP
                sess = current_sessions.get(requested_cp_id)
                # 2.5.4 Verificar que exista sesión autorizada (status='authorized' o 'charging')
                # Verificar que exista sesión autorizada (status='authorized' o 'charging')
                if sess and sess.get('status') in ['authorized', 'charging']:
                    # 2.5.5 Cargar el driver asignado al CP
                    assigned_driver = sess.get('driver_id')
                    # 2.5.6 Enviar el driver asignado al CP usando protocolo
                    send_frame(client_socket, assigned_driver, central_messages)
                    send_ack(client_socket)  # Confirmar recepción
                    # Seva: AUDITORÍA: CHECK_SESSION EXITOSO ***
                    log_audit_event(
                        source_ip=address[0],
                        action="CONSULTA_SESSION_OK",
                        description=f"Monitor/Engine confirmó sesión autorizada para driver {assigned_driver}.",
                        cp_id=requested_cp_id
                    )
                    # *****************************************
                    print(f"[CENTRAL] Sesión autorizada confirmada para CP {requested_cp_id} con Driver {assigned_driver} (status={sess.get('status')})")
                else:
                    # Usar protocolo para enviar respuesta negativa
                    send_frame(client_socket, "NO_SESSION", central_messages)
                    send_ack(client_socket)
                    # Seva: AUDITORÍA: CHECK_SESSION FALLIDA ***
                    log_audit_event(
                        source_ip=address[0],
                        action="CONSULTA_SESSION_FALLIDA",
                        description=f"Monitor/Engine consultó, sin sesión autorizada.",
                        cp_id=requested_cp_id
                    )
                    # *****************************************
                    print(f"[CENTRAL] No hay sesión autorizada para CP {requested_cp_id}")
        else:
            # Usar protocolo para enviar respuesta negativa
            send_frame(client_socket, "NO_SESSION", central_messages)
            send_ack(client_socket)

    # --- Otros mensajes no reconocidos ---
    else:
        print(f"[CENTRAL]  Mensaje no reconocido de CP {cp_id}: {data_string}")
        central_messages.append(f" Mensaje no reconocido de CP {cp_id}: {data_string}")



# Funcion Socket para manejar la conexión de un único CP
def handle_client(client_socket, address, central_messages, kafka_broker):
    """Maneja la conexión de un único CP usando el protocolo <STX><DATA><ETX><LRC>."""
    #Inicializa cp_id para identificar qué CP se conecta (se sabrá tras el REGISTER#...)
    cp_id = None
    try:
        # FASE 1: Realizar handshake inicial (ENQ/ACK)
        #Paso 1.1: Esperar ENQ del cliente y responder con ACK
        if CENTRAL_VERBOSE:
            print(f"[CENTRAL] Nueva conexión desde {address}. Iniciando handshake...")
        push_message(central_messages, f"[CONN] Nueva conexión {address}")
        if not handshake_server(client_socket, central_messages):
            print(f"[CENTRAL] ERROR: Handshake fallido con {address}. Cerrando conexión.")
            return
        
        # FASE 2: Recibir primer mensaje usando el protocolo
        #¿Qué puede recibir?**
        # `REGISTER#CP_ID#LOCATION#PRICE` → Registro de CP
        # `CHECK_SESSION#CP_ID` → Consulta de sesión
        # `CHECK_DRIVER#CP_ID` → Consulta de driver
        # `FAULT#CP_ID` → Avería
        # `ACK#COMANDO` → Confirmación
        # `NACK#COMANDO` → Rechazo de comando
        # `RECOVER#CP_ID` → Recuperación de avería
        if CENTRAL_VERBOSE:
            print(f"[CENTRAL] Esperando primer mensaje de {address}...")
        push_message(central_messages, f"[CONN] Esperando ENQ/primer mensaje de {address}")
        data_string, is_valid = receive_frame(client_socket, central_messages)
        
        #Paso 2.1: Verificar que la trama es válida
        if not is_valid or not data_string:
            print(f"[CENTRAL] ERROR: Trama inválida recibida de {address}. Cerrando conexión.")
            send_nack(client_socket)  # Informar al cliente que hubo error
            return
        
        #Paso 2.2: Enviar ACK confirmando recepción válida
        send_ack(client_socket)
        
        #Paso 2.3: Parsear el mensaje recibido
        parts = data_string.split('#')

        
        
        # FASE 3: Procesar el mensaje recibido
        # FASE 3.1: Soportar consultas rápidas CHECK_SESSION / CHECK_DRIVER en nuevas conexiones (sin REGISTER)
        if parts and parts[0] in ['CHECK_SESSION', 'CHECK_DRIVER']:
            try:
                cmd = parts[0]
                target_cp = parts[1] if len(parts) >= 2 else None
                if cmd == 'CHECK_SESSION' and target_cp:
                    with active_cp_lock:
                        sess = current_sessions.get(target_cp)
                        if sess and sess.get('status') in ['authorized', 'charging']:
                            driver_id = sess.get('driver_id') or ""
                            send_frame(client_socket, driver_id, central_messages)
                        else:
                            send_frame(client_socket, "NO_SESSION", central_messages)
                    send_ack(client_socket)
                elif cmd == 'CHECK_DRIVER' and target_cp:
                    with active_cp_lock:
                        sess = current_sessions.get(target_cp)
                        driver_id = sess.get('driver_id') if sess else None
                        if driver_id and driver_id in connected_drivers:
                            send_frame(client_socket, driver_id, central_messages)
                        else:
                            send_frame(client_socket, "NO_DRIVER", central_messages)
                    send_ack(client_socket)
                else:
                    send_frame(client_socket, "ERROR", central_messages)
                    send_ack(client_socket)
            except Exception:
                # Silenciar errores de consultas rápidas para evitar ruido en logs
                pass
            finally:
                try:
                    send_eot(client_socket)  # Indicar fin de transmisión
                except Exception:
                    pass
            return

        # FASE 3.2: Registrar el CP si se envía un mensaje REGISTER
        if len(parts) >= 3 and parts[0] == 'REGISTER':
            cp_id = parts[1]
            location = parts[2]
            try:
                now_ts = time.time()
                monitor_last_seen[cp_id] = now_ts
                engine_last_seen[cp_id] = now_ts
                engine_health_status[cp_id] = 'OK'
            except Exception:
                pass
            # Si se envía precio opcional: REGISTER#CP_ID#LOCATION#PRICE
            price = None
            if len(parts) >= 4:
                try:
                    price = float(parts[3])
                except Exception:
                    price = None

            #Fase2.2.1: Registrar en la BD (si no existía) o actualizar ubicación/precio
            # Detectar si ya existía para ajustar los mensajes de sistema
            pre_status = database.get_cp_status(cp_id)
            first_time_in_db = pre_status in [None, 'NO_EXISTE']
            first_time_this_session = cp_id not in connected_once_this_session
            database.register_cp(cp_id, location, price_per_kwh=price)
            #Fase2.2.2: Solo actualizar estado a ACTIVADO si no está ya en AVERIADO o FUERA_DE_SERVICIO
            # Esto evita que reconexiones reseteen estados de avería o fuera de servicio
            current_status = database.get_cp_status(cp_id)
            new_status = current_status  # Por defecto mantener el estado actual
            
            # Política: Tras REGISTER el CP queda ACTIVO, salvo estados especiales previos
            if first_time_in_db or first_time_this_session:
                database.update_cp_status(cp_id, 'ACTIVADO')
                new_status = 'ACTIVADO'
            else:
                # Reconexión: mantener estados especiales; sólo ACTIVADO si no hay estados especiales
                if current_status not in ['AVERIADO', 'FUERA_DE_SERVICIO', 'SUMINISTRANDO']:
                    new_status = 'ACTIVADO'
                else:
                    if CENTRAL_VERBOSE:
                        print(f"[CENTRAL] CP {cp_id} se reconectó manteniendo estado '{current_status}' (no reseteado a ACTIVADO)")
            
            if not first_time_in_db and not first_time_this_session:
                with active_cp_lock:
                    has_session = cp_id in current_sessions or cp_id in cp_driver_assignments
                if has_session:
                    force_release_cp_session(
                        cp_id,
                        central_messages,
                        reason="Reconexión de monitor",
                        target_status=new_status
                    )

            # Mensajería más clara según si es alta inicial o reconexión
            if first_time_in_db:
                push_message(central_messages, f"CP '{cp_id}' registrado (primera vez en BD) desde {address}. Estado: {new_status} (price={price})")
                push_message(central_messages, f"[PROTOCOLO] REGISTRO_INICIAL CP {cp_id} (estado {new_status})")
                
                # Seva: AUDITORÍA: REGISTRO INICIAL
                source_ip = address[0] # Se usa la IP del socket que se conecta
                log_audit_event(
                    source_ip=source_ip,
                    action="CP_REGISTRO_INICIAL",
                    description=f"CP registrado por primera vez en BD. Ubic: {location}. Estado: {new_status}",
                    cp_id=cp_id
                )
                # Informar al Monitor explícitamente del resultado del registro
                try:
                    send_frame(client_socket, f"REGISTER_RESULT#FIRST", central_messages)
                except Exception:
                    pass
            elif first_time_this_session:
                push_message(central_messages, f"[CONN] Primera conexión de sesión de CP '{cp_id}' desde {address}. Estado: {new_status}")
                push_message(central_messages, f"[PROTOCOLO] PRIMERA_CONEXION_SESION CP {cp_id} (estado {new_status})")
                # Seva: AUDITORÍA: PRIMERA CONEXIÓN DE SESIÓN
                source_ip = address[0]
                log_audit_event(
                    source_ip=source_ip,
                    action="CP_PRIMERA_CONEXION_SESION",
                    description=f"Primera conexión de la sesión. Estado final: {new_status}",
                    cp_id=cp_id
                )
                # **********************
                # Informar al Monitor explícitamente del resultado del registro
                try:
                    send_frame(client_socket, f"REGISTER_RESULT#FIRST", central_messages)
                except Exception:
                    pass
            else:
                push_message(central_messages, f"[CONN] Reconexión de CP '{cp_id}' desde {address}. Estado: {new_status}")
                push_message(central_messages, f"[PROTOCOLO] RECONEXION CP {cp_id} (estado {new_status})")
                # Seva: AUDITORÍA: RECONEXIÓN
                source_ip = address[0]
                log_audit_event(
                    source_ip=source_ip,
                    action="CP_RECONEXION",
                    description=f"CP se reconectó a la Central. Estado final: {new_status}",
                    cp_id=cp_id
                )
                # ***************
                # Informar al Monitor de que la CENTRAL lo considera una reconexión
                try:
                    send_frame(client_socket, f"REGISTER_RESULT#RECONNECT", central_messages)
                except Exception:
                    pass
            # Aplicar el estado calculado DESPUÉS de registrar y loguear (para que el panel muestre primero el handshake/REGISTER)
            if new_status != current_status:
                database.update_cp_status(cp_id, new_status)
            pending_monitor_disconnects.pop(cp_id, None)

            #Fase2.2.3: Guardamos la referencia del socket para envíos síncronos (autorización/órdenes)
            with active_cp_lock:
                active_cp_sockets[cp_id] = client_socket 
                connected_once_this_session.add(cp_id)
            
            
            
            #FASE 4: Bucle de Escucha de mensajes del CP usando protocolo
            while True:
                #Paso 4.1: Recibir trama usando protocolo (con timeout para no bloquear)
                data_string, is_valid = receive_frame(client_socket, central_messages, timeout=5)
                
                #Paso 4.2: Gestionar timeouts vs. cierre real
                if data_string == "__TIMEOUT__":
                    try:
                        monitor_last_seen[cp_id] = time.time()
                    except Exception:
                        pass
                    # No hay tráfico, seguimos esperando sin penalizar
                    continue
                if data_string == "__ACK__":
                    if 'empty_reads' in locals():
                        empty_reads = 0
                    continue
                if data_string == "__NACK__":
                    push_message(central_messages, f"[PROTOCOLO] NACK recibido desde {cp_id}")
                    if 'empty_reads' in locals():
                        empty_reads = 0
                    continue
                
                if not data_string:
                    if 'empty_reads' not in locals():
                        empty_reads = 0
                    empty_reads += 1
                    if empty_reads >= 2:
                        push_message(central_messages, f"[CONN] Conexión cerrada por CP {cp_id}")
                        break
                    else:
                        continue
                
                #Paso 4.3: Verificar validez de la trama
                if not is_valid:
                    print(f"[CENTRAL] Trama inválida recibida de CP {cp_id}. Enviando NACK...")
                    send_nack(client_socket)
                    continue  # Continuar esperando siguiente mensaje
                
                #Paso 4.4: Enviar ACK confirmando recepción válida
                send_ack(client_socket)
                if 'empty_reads' in locals():
                    empty_reads = 0
                
                #Paso 4.5: Verificar si es EOT (fin de transmisión)
                if data_string == "EOT":
                    print(f"[CENTRAL] CP {cp_id} envió EOT. Cerrando conexión.")
                    break
                
                #Paso 4.6: Procesar mensajes de control/averías
                process_socket_data2(data_string, cp_id, address, client_socket, central_messages, kafka_broker)
                
        else:
            central_messages.append(f"ERROR: Mensaje de registro inválido de {address}. Cerrando conexión.")
            
    except Exception as e:
        central_messages.append(f"Error con el CP {cp_id} ({address}): {e}")
    finally:
        # FASE 4: Desconexión y Limpieza
        if cp_id:
            # 1. Siempre eliminamos el socket de la lista activa primero
            with active_cp_lock:
                if cp_id in active_cp_sockets:
                    del active_cp_sockets[cp_id]

            try:
                # 2. Consultamos el estado REAL en la Base de Datos
                current_db_status = database.get_cp_status(cp_id)

                # --- CORRECCIÓN DE SEGURIDAD ---
                # Si el estado es FUERA_DE_SERVICIO, significa que la API lo revocó o se paró manualmente.
                if current_db_status == 'FUERA_DE_SERVICIO':
                    push_message(central_messages, f"[CONN] Socket cerrado para {cp_id}, pero se mantiene estado FUERA_DE_SERVICIO (Seguridad).")
                
                # --- LÓGICA DE DESCONEXIÓN NORMAL ---
                else:
                    # 1. Comandos pendientes
                    if cp_id in pending_cp_commands:
                        push_message(central_messages, f"[CONN] Conexión cerrada con comando pendiente para {cp_id}.")
                    
                    # 2. Verificar Gracia por recuperación reciente
                    within_grace = False
                    try:
                        ts = recent_recover_events.get(cp_id)
                        within_grace = ts is not None and (time.time() - ts) <= 5
                    except: pass

                    # 3. Gestión de Sesión Activa
                    session_active = False
                    with active_cp_lock:
                        sess = current_sessions.get(cp_id)
                        if sess: session_active = True
                    
                    if session_active:
                        try:
                            # Intentamos liberar sesión si es caída de socket sucio
                            force_release_cp_session(
                                cp_id,
                                central_messages,
                                reason="Monitor desconectado (socket cerrado)",
                                target_status='DESCONECTADO',
                                supply_error_reason="Carga interrumpida: Monitor desconectado"
                            )
                        except Exception: pass
                        # Tras force_release, actualizamos variables locales para la lógica siguiente
                        current_db_status = 'DESCONECTADO' 
                        session_active = False

                    # 4. Decisión Final
                    if session_active or current_db_status in ['RESERVADO', 'SUMINISTRANDO']:
                        # Si estaba haciendo algo importante, damos un tiempo de gracia
                        pending_monitor_disconnects[cp_id] = time.time()
                        push_message(central_messages, f"[CONN] Monitor de {cp_id} desconectado (gracia antes de marcar DESCONECTADO).")
                    elif not within_grace:
                        # Si no hay gracia y no estaba FUERA_DE_SERVICIO, marcamos como DESCONECTADO 
                        database.update_cp_status(cp_id, 'DESCONECTADO')
                        if current_db_status == 'SUMINISTRANDO':
                             push_message(central_messages, f"[CONN] Monitor desconectado durante SUMINISTRO → DESCONECTADO.")
                        else:
                             push_message(central_messages, f"[CONN] Monitor de {cp_id} desconectado.")

            except Exception as e:
                print(f"[CENTRAL] Error gestionando desconexión de {cp_id}: {e}")

            # 3. Limpieza de variables de salud (Siempre)
            try:
                monitor_last_seen.pop(cp_id, None)
                engine_health_status.pop(cp_id, None)
            except Exception: pass
            
        try:
            client_socket.close()
        except Exception: pass

# Funcion Socket para iniciar el servidor de sockets
def start_socket_server(host, port, central_messages, kafka_broker):
    """Inicia el servidor de sockets para escuchar a los CPs."""
    #1. Crear el Socket del servidor
    # socket.AF_INET → Protocolo IPv4 (direcciones como 127.0.0.1)
    # socket.SOCK_STREAM → TCP (Transmission Control Protocol)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    #2. bind() - "Me pongo en la IP y puerto 0.0.0.0:8000"
    server_socket.bind((host, port))
    #3. listen() - "Espero conexiones"
    server_socket.listen(15) #Puede aceptar hasta 15 conexiones en cola
    central_messages.append(f"EV_Central escuchando sockets en {host}:{port}")#Este mensaje se muestra luego en el panel de estado (display_panel). - "Me pongo en el puerto 8000"
    
    #LOOP INFINITO: "Siempre esperando más conexiones"
    while True:
        #4. accept() - "Cuando alguien se conecte, le respondo"
        #   - Devuelve:
        #     - El canal de comunicación con ese CP
        #     - La IP/puerto del CP (ej: ('127.0.0.1', 54678))
        client_socket, address = server_socket.accept()
        #5. Todos los CPs se procesan simultáneamente
        client_thread = threading.Thread(target=handle_client, args=(client_socket, address, central_messages, kafka_broker))
        client_thread.daemon = True
        client_thread.start()

# --- Funciones de Comandos de CENTRAL (13ª Mecánica) ---
def send_cp_command(cp_id, command, central_messages):
    """Envía un comando (Parar/Reanudar) a un CP específico a través del socket síncrono usando protocolo <STX><DATA><ETX><LRC>.
    La confirmación ACK/NACK la procesará handle_client() en segundo plano."""
    
    ## 1. Verificamos que el CP esté conectado
    #Si el CP no está en la lista de sockets activos, muestra error. No puede mandarle nada
    if cp_id not in active_cp_sockets:
        msg = f"ERROR: CP {cp_id} no está conectado por socket para recibir comandos."
        print(f"[CENTRAL] {msg}")
        central_messages.append(msg)
        return
    
    try:
        # 2. Recuperamos el socket activo
        socket_ref = active_cp_sockets[cp_id]
        
        # 3. Enviamos el comando al CP usando protocolo <STX><DATA><ETX><LRC>
        # Usamos formato: "COMMAND#PARAMETRO". Ej: "PARAR#CENTRAL"
        command_message = f"{command.upper()}#CENTRAL"
        if CENTRAL_VERBOSE:
            print(f"[CENTRAL]  Enviando comando {command} a CP {cp_id} usando protocolo...")

        # 3.1 Actualización optimista de estado y registro de comando pendiente
        try:
            prev_status = database.get_cp_status(cp_id)
            if command.upper() == 'PARAR':
                database.update_cp_status(cp_id, 'FUERA_DE_SERVICIO')
                pending_cp_commands[cp_id] = { 'command': 'PARAR', 'prev_status': prev_status }
                push_message(central_messages, f"Estado {cp_id}: FUERA_DE_SERVICIO (pendiente ACK)")
            elif command.upper() == 'REANUDAR':
                database.update_cp_status(cp_id, 'ACTIVADO')
                pending_cp_commands[cp_id] = { 'command': 'REANUDAR', 'prev_status': prev_status }
                push_message(central_messages, f"Estado {cp_id}: ACTIVADO (pendiente ACK)")
        except Exception as e:
            print(f"[CENTRAL] WARNING: No se pudo preparar estado optimista para {cp_id}: {e}")
        
        # 4. Usar función send_frame para enviar con protocolo
        if send_frame(socket_ref, command_message, central_messages):
            # Log de protocolo explícito con el CP destino
            push_message(central_messages, f"[PROTOCOLO] → {cp_id}: {command_message}")
            # 5. Esperar ACK/NACK del CP
            # El ACK/NACK llegará como mensaje normal que será procesado por process_socket_data2
            if CENTRAL_VERBOSE:
                print(f"[CENTRAL] Comando '{command}' enviado a CP {cp_id}. Esperando ACK/NACK...")
            central_messages.append(f" Comando '{command}' enviado a CP {cp_id}. Esperando ACK/NACK...")
        else:
            msg = f"ERROR: No se pudo enviar comando a CP {cp_id}"
            if CENTRAL_VERBOSE:
                print(f"[CENTRAL] {msg}")
            central_messages.append(msg)
        
    except Exception as e:
        msg = f"ERROR al enviar comando a CP {cp_id}: {e}"
        print(f"[CENTRAL] {msg}")
        central_messages.append(msg)
        
        # Se marca el CP como desconectado
        database.update_cp_status(cp_id, 'DESCONECTADO')
        # Se borra su socket del diccionario
        with active_cp_lock:
            if cp_id in active_cp_sockets:
                del active_cp_sockets[cp_id]
        
# --- EN EV_Central.py ---
def get_local_ip():
    """Obtiene la IP real de la máquina."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)) 
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

# Funcion para procesar los comandos de la interfaz de CENTRAL
def process_user_input(central_messages):
    """Maneja los comandos de la interfaz de CENTRAL (punto 13 de la mecánica)."""
    while True:
        try:
            # Esperamos el input del usuario
            command_line = input("\n> ").strip().upper()
            
            # Si el usuario escribe QUIT o Q, salir
            if command_line == 'QUIT' or command_line == 'Q':
                raise KeyboardInterrupt
            
            parts = command_line.split()
            command = parts[0]
            
            # --- Comandos para un CP específico ---
            if command in ['P', 'PARAR']:
                if len(parts) == 2:
                    cp_id = parts[1]
                    if CENTRAL_VERBOSE:
                        print(f"\n[CENTRAL] Iniciando comando PARAR para CP {cp_id}...")
                    central_messages.append(f" Iniciando comando PARAR para CP {cp_id}...")
                    # Seva: AUDITORÍA: ORDEN MANUAL PARAR ***
                    log_audit_event(
                        source_ip=get_local_ip(), # <--- CAMBIO AQUÍ
                        action="OPERADOR_ORDEN_PARAR",
                        description=f"Operador (Consola Central) ordenó PARAR el CP.",
                        cp_id=cp_id
                    )
                    # **************************************
                    send_cp_command(cp_id, 'PARAR', central_messages)
                else:
                    print("\n[CENTRAL]  Error: Uso correcto es: P <CP_ID> o PARAR <CP_ID>")
                    central_messages.append(" Error: Uso correcto es: P <CP_ID> o PARAR <CP_ID>")
            
            elif command in ['R', 'REANUDAR']:
                if len(parts) == 2:
                    cp_id = parts[1]
                    if CENTRAL_VERBOSE:
                        print(f"\n[CENTRAL]  Iniciando comando REANUDAR para CP {cp_id}...")
                    central_messages.append(f" Iniciando comando REANUDAR para CP {cp_id}...")
                    # Seva: AUDITORÍA: ORDEN MANUAL REANUDAR ***
                    log_audit_event(
                        source_ip=get_local_ip(),
                        action="OPERADOR_ORDEN_REANUDAR",
                        description=f"Operador (Consola) ordenó REANUDAR el CP. Esperando ACK.",
                        cp_id=cp_id
                    )
                    # *****************************************
                    send_cp_command(cp_id, 'REANUDAR', central_messages)
                else:
                    print("\n[CENTRAL]  Error: Uso correcto es: R <CP_ID> o REANUDAR <CP_ID>")
                    central_messages.append(" Error: Uso correcto es: R <CP_ID> o REANUDAR <CP_ID>")
            
            # --- Comandos para TODOS los CPs ---
            elif command in ['PA', 'PT', 'PARAR_TODOS']:
                if CENTRAL_VERBOSE:
                    print("\n[CENTRAL]  Enviando comando PARAR a todos los CPs conectados...")
                central_messages.append(" Iniciando comando PARAR para TODOS los CPs...")
                with active_cp_lock:
                    for cp_id in list(active_cp_sockets.keys()):
                        # Evitar enviar PARAR a CPs ya fuera de servicio
                        try:
                            st = database.get_cp_status(cp_id)
                            if st == 'FUERA_DE_SERVICIO':
                                push_message(central_messages, f"[SKIP] CP {cp_id} ya está FUERA_DE_SERVICIO. No se envía PARAR.")
                                continue
                        except Exception:
                            pass
                        # Seva: AUDITORÍA: ORDEN MANUAL PARAR TODOS ***
                        log_audit_event(
                            source_ip=get_local_ip(),
                            action="OPERADOR_ORDEN_PARAR_MASIVA",
                            description=f"Operador (Consola) ordenó PARAR el CP como parte de un comando masivo (PT). Esperando ACK.",
                            cp_id=cp_id
                        )
                        # **********************************************
                        send_cp_command(cp_id, 'PARAR', central_messages)
            
            elif command in ['RA', 'RT', 'REANUDAR_TODOS']:
                if CENTRAL_VERBOSE:
                    print("\n[CENTRAL]  Enviando comando REANUDAR a todos los CPs conectados...")
                central_messages.append(" Iniciando comando REANUDAR para TODOS los CPs...")
                with active_cp_lock:
                    for cp_id in list(active_cp_sockets.keys()):
                        # Enviar REANUDAR solo si procede
                        try:
                            st = database.get_cp_status(cp_id)
                            if st not in ['FUERA_DE_SERVICIO', 'AVERIADO', 'DESCONECTADO']:
                                push_message(central_messages, f"[SKIP] CP {cp_id} en estado {st}. No se envía REANUDAR.")
                                continue
                        except Exception:
                            pass
                        # Seva: AUDITORÍA: ORDEN MANUAL REANUDAR TODOS ***
                        log_audit_event(
                            source_ip=get_local_ip(),
                            action="OPERADOR_ORDEN_REANUDAR_MASIVA",
                            description=f"Operador (Consola) ordenó REANUDAR el CP como parte de un comando masivo (RT). Esperando ACK.",
                            cp_id=cp_id
                        )
                        # **************************************************
                        send_cp_command(cp_id, 'REANUDAR', central_messages)
            
            # Comando desconocido
            else:
                if CENTRAL_VERBOSE:
                    print(f"\n[CENTRAL]  Comando desconocido: {command}")
                central_messages.append(f" Comando desconocido: {command}")
                
        except EOFError:
            # Manejar el fin de archivo o Ctrl+D/Z
            time.sleep(0.1) 
        except Exception as e:
            msg = f" Error en el procesamiento de entrada: {e}"
            print(f"\n[CENTRAL] {msg}")
            central_messages.append(msg)


# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    # Paso 1: Verificar Argumentos
    # sys.argv[0] = ev_central.py
    # sys.argv[1] = 8000 (puerto)
    # sys.argv[2] = 127.0.0.1:9092 (kafka)
    if len(sys.argv) < 3:
        print("Uso: python ev_central.py <puerto_socket> <kafka_broker_ip:port>")
        sys.exit(1)

    try:
        # Paso 2: Extraer Argumentos
        SOCKET_PORT = int(sys.argv[1])       # 8000
        KAFKA_BROKER = sys.argv[2]           # 127.0.0.1:9092
        HOST = '0.0.0.0'                     # Escucha en todas las IPs    

        # --- NUEVO: SOBRESCRIBIR CON JSON (Plan A - Examen) ---
        config = get_network_config()
        
        # 1. Configurar KAFKA
        k_ip = config.get('kafka_ip')
        k_port = config.get('kafka_port')
        if k_ip and k_port:
            KAFKA_BROKER = f"{k_ip}:{k_port}"
            print(f"[INIT] 🟢 Central usando Kafka del JSON: {KAFKA_BROKER}")
        else:
            print(f"[INIT] ⚠️ Usando Kafka de consola: {KAFKA_BROKER}")

        # 2. Configurar PUERTO SOCKET (Sobrescribe el sys.argv[1])
        # Buscamos 'central_socket_port' en el JSON. Si existe, lo usamos.
        json_socket_port = config.get('central_socket_port')
        if json_socket_port:
            SOCKET_PORT = int(json_socket_port)
            print(f"[INIT] 🟢 Socket Server usará puerto del JSON: {SOCKET_PORT}")

        # 3. Configurar PUERTO API (Para Flask)
        json_api_port = config.get('central_api_port')
        API_PORT = 5000 # Valor por defecto
        if json_api_port:
            API_PORT = int(json_api_port)
            print(f"[INIT] 🟢 API Server usará puerto del JSON: {API_PORT}")
        # ------------------------------------------------------
        
        # Paso 3: Usaremos listas compartidas para que los hilos se comuniquen con el panel
        central_messages = TimestampedList()
        central_messages.append("CENTRAL system status OK")
        driver_requests = []                            #Pedidos de drivers en cola (process_kafka_requests)    

        # Paso 4: Crear un productor Kafka compartido para que lo usen varios hilos
        shared_producer = KafkaProducer(
            bootstrap_servers=[KAFKA_BROKER],                        # Dónde está Kafka: 127.0.0.1:9092
            value_serializer=lambda v: json.dumps(v).encode('utf-8') # Dict → JSON string → Bytes
        ) 
        # Guardar referencia global para acceso desde otros módulos
        shared_producer_ref = shared_producer

        #Paso 5: Iniciar HILOS Kafka en Paralelo

        #5.1. Procesar la cola de pedidos de drivers
        # → Escucha mensajes de Kafka
        # → Recibe: Pedidos de drivers, telemetría de CPs
        # → Procesa: Autoriza o deniega pedidos
        # → Envía: Respuestas a drivers
        kafka_thread = threading.Thread(target=process_kafka_requests, args=(KAFKA_BROKER, central_messages, driver_requests, shared_producer))
        kafka_thread.daemon = True # Si el programa principal termina, este hilo también termina
        kafka_thread.start()

        #5.2. Anunciar el estado de la red a los drivers
        # Cada 5 segundos:
        # 1. Obtiene todos los CPs de la BD
        # 2. Envía el estado a un topic público
        # 3. Los drivers reciben este estado
        network_broadcast_thread = threading.Thread(target=broadcast_network_status, args=(KAFKA_BROKER, shared_producer))
        network_broadcast_thread.daemon = True
        network_broadcast_thread.start()
        

        # Paso 6. Configurar la base de datos
        database.setup_database()

        # Paso 7: Marcar CPs como DESCONECTADO
        # Lee TODOS los CPs de la BD
        # Los marca como DESCONECTADO
        all_cps_on_startup = database.get_all_cps()
        if all_cps_on_startup:
            print("[CENTRAL] Restableciendo estado de CPs cargados a DESCONECTADO.")
            for cp in all_cps_on_startup:
                database.update_cp_status(cp['id'], 'DESCONECTADO')

        # Paso 8: Iniciar Servidor de Sockets
        # Escucha en puerto 8000 esperando conexiones
        # Cuando un CP se conecta, crea un hilo nuevo para él
        # Espera mensajes de:
        # `REGISTER#CP_ID#LOCATION#PRICE` → Registro
        # `FAULT#CP_ID` → Avería
        # `ACK#PARAR` → Confirmación
        server_thread = threading.Thread(target=start_socket_server, args=(HOST, SOCKET_PORT, central_messages, KAFKA_BROKER))
        server_thread.daemon = True
        server_thread.start()

        # --- NUEVO RELEASE 2: API REST ---
        # Paso 8.5: Iniciar API REST de Central (Módulo separado)
        API_PORT = 5000 # Puerto estándar para Flask
        
        # 1. INYECCIÓN DE DEPENDENCIAS
        # Pasamos nuestras variables locales al módulo API para que pueda usarlas
        EV_Central_API.configure_api(
            messages_list=central_messages,    # Para escribir logs
            drivers_set=connected_drivers,     # Para leer drivers conectados
            sockets_dict=active_cp_sockets,    # Para saber qué CPs tienen socket
            command_func=send_cp_command,      # Para poder enviar órdenes (PARAR/REANUDAR)
            kafka_broker_url=KAFKA_BROKER,
            sessions=current_sessions,      
            producer=shared_producer_ref
        )
        
        # 2. Arrancar el servidor Flask en un hilo separado
        api_thread = threading.Thread(
            target=EV_Central_API.start_api_server, 
            args=(HOST, API_PORT)
        )
        api_thread.daemon = True
        api_thread.start()
        # ---------------------------------
        
        # Paso 9: Iniciar el hilo de entrada de comandos del usuario
        # Lee comandos: P <CP_ID>, R <CP_ID>, PT (parar todos), RT (reanudar todos), Q (quit)
        input_thread = threading.Thread(target=process_user_input, args=(central_messages,))
        input_thread.daemon = True
        input_thread.start()
        
        # Paso 10: Iniciar el hilo de limpieza de drivers desconectados
        # 1. Revisa qué drivers están conectados
        # 2. Busca drivers que no han enviado peticiones en 60 segundos
        # 3. Los elimina de la lista de `connected_drivers`
        # 4. Libera sus asignaciones de CPs
        cleanup_thread = threading.Thread(target=cleanup_disconnected_drivers)
        cleanup_thread.daemon = True
        cleanup_thread.start()

        # Paso 11 bis: Iniciar reconciliador de estados Monitor/Engine
        reconcile_thread = threading.Thread(target=reconcile_cp_states, args=(central_messages,))
        reconcile_thread.daemon = True
        reconcile_thread.start()

        # Paso 11: Panel de Monitorización
        # Bucle infinito que muestra el estado del sistema
        # Se refresca cada 2 segundos
        # Muestra:
        # Tabla de CPs (con colores)
        # Drivers conectados
        # Pedidos en cola
        # Mensajes del sistema
        # Comandos disponibles
        display_panel(central_messages, driver_requests)

    except ValueError:
        print("Error: El puerto debe ser un número entero.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nServidor detenido por el usuario. Cerrando hilos...")
        # Nota: La terminación del programa principal terminará los hilos daemon.
        sys.exit(0)