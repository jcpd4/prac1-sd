import socket
import threading
import sys
import time
import os
import json
import database
from kafka import KafkaProducer # Usado para enviar telemetría a Central (asíncrono)
from kafka.errors import NoBrokersAvailable

# --- Funciones del Protocolo de Sockets <STX><DATA><ETX><LRC> ---

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

def build_frame(data_string, silent=False):
    """
    Construye una trama completa siguiendo el protocolo <STX><DATA><ETX><LRC>.
    Esta función toma un string de datos y lo empaqueta con los delimitadores y checksum.
    
    Args:
        data_string: String con los datos a enviar (ej: "OK", "ACK#PARAR")
        silent: Si es True, no imprime mensajes (útil para health checks)
    
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
    #Paso 5: Devolver la trama completa
    return frame

def add_protocol_message(msg):
    """Añade un mensaje del protocolo a la lista circular (máximo 5 mensajes)."""
    with protocol_lock:
        protocol_messages.append(msg)
        if len(protocol_messages) > 5:
            protocol_messages.pop(0)  # Mantener solo los últimos 5

def parse_frame(frame_bytes, silent=False):
    """
    Parsea una trama recibida y valida el LRC para detectar errores de transmisión.
    Esta función extrae los datos del mensaje y verifica la integridad mediante el LRC.
    
    Args:
        frame_bytes: Bytes recibidos del socket (debe contener STX + DATA + ETX + LRC)
        silent: Si es True, no imprime mensajes normales (solo errores)
    
    Returns:
        tuple: (data_string, is_valid) donde:
            - data_string: String con los datos extraídos o None si hay error
            - is_valid: True si el LRC es válido, False en caso contrario
    """
    #Paso 1: Verificar que la trama tenga el tamaño mínimo (STX + al menos 1 byte DATA + ETX + LRC)
    if len(frame_bytes) < 4:
        error_msg = f"[PROTOCOLO ENGINE] ERROR: Trama demasiado corta ({len(frame_bytes)} bytes)"
        print(error_msg)
        add_protocol_message(error_msg)
        return None, False
    
    #Paso 2: Verificar que el primer byte sea STX (0x02)
    if frame_bytes[0] != 0x02:
        error_msg = f"[PROTOCOLO ENGINE] ERROR: Primer byte no es STX (recibido: 0x{frame_bytes[0]:02X})"
        print(error_msg)
        add_protocol_message(error_msg)
        return None, False
    
    #Paso 3: Buscar la posición del byte ETX (0x03) en la trama
    etx_pos = -1
    for i in range(1, len(frame_bytes) - 1):  # -1 porque después del ETX debe venir el LRC
        if frame_bytes[i] == 0x03:  # ETX encontrado
            etx_pos = i
            break
    
    #Paso 4: Verificar que se encontró el ETX
    if etx_pos == -1:
        error_msg = "[PROTOCOLO ENGINE] ERROR: No se encontró ETX en la trama recibida"
        print(error_msg)
        add_protocol_message(error_msg)
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
        error_msg = f"[PROTOCOLO ENGINE] ERROR: LRC no coincide (recibido: 0x{received_lrc:02X}, esperado: 0x{expected_lrc:02X})"
        print(error_msg)
        add_protocol_message(error_msg)
        return None, False  # LRC no coincide, hay error en la transmisión
    
    #Paso 10: Decodificar los datos a string UTF-8
    try:
        data = data_bytes.decode('utf-8')
        if not silent:
            success_msg = f"[PROTOCOLO ENGINE] Recibido: '{data}' (LRC válido)"
            print(success_msg)
            add_protocol_message(success_msg)
        return data, True
    except UnicodeDecodeError as e:
        error_msg = f"[PROTOCOLO ENGINE] ERROR: No se pudo decodificar los datos como UTF-8: {e}"
        print(error_msg)
        add_protocol_message(error_msg)
        return None, False

def send_frame(socket_ref, data_string, silent=False):
    """
    Envía una trama completa a través de un socket usando el protocolo <STX><DATA><ETX><LRC>.
    
    Args:
        socket_ref: Referencia al socket donde enviar la trama
        data_string: String con los datos a enviar
        silent: Si es True, no imprime mensajes (útil para health checks)
    
    Returns:
        bool: True si el envío fue exitoso, False en caso contrario
    """
    try:
        #Paso 1: Construir la trama con el protocolo
        frame = build_frame(data_string, silent=silent)
        #Paso 2: Enviar la trama por el socket
        socket_ref.sendall(frame)
        return True
    except Exception as e:
        #Paso 4: Manejar errores de envío (siempre mostrar errores)
        error_msg = f"[PROTOCOLO ENGINE] ERROR al enviar '{data_string}': {e}"
        print(error_msg)
        add_protocol_message(error_msg)
        return False

def receive_frame(socket_ref, timeout=None, silent=False):
    """
    Recibe una trama completa desde un socket y la parsea según el protocolo <STX><DATA><ETX><LRC>.
    
    Args:
        socket_ref: Referencia al socket de donde recibir la trama
        timeout: (Opcional) Timeout en segundos para la recepción
        silent: Si es True, no imprime mensajes normales (solo errores)
    
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
            return None, False
        
        #Paso 4: Parsear la trama recibida
        data, is_valid = parse_frame(frame_bytes, silent=silent)
        return data, is_valid
        
    except socket.timeout:
        return None, False
    except Exception as e:
        #Paso 6: Manejar otros errores (siempre mostrar errores)
        return None, False

def handshake_server(socket_ref, silent=False):
    """
    Realiza el handshake inicial (ENQ/ACK) desde el lado servidor.
    El servidor espera ENQ del cliente y responde con ACK.
    
    Args:
        socket_ref: Referencia al socket de conexión (cliente conectado)
        silent: Si es True, no imprime mensajes normales (solo errores)
    
    Returns:
        bool: True si el handshake fue exitoso, False en caso contrario
    """
    try:
        #Paso 1: Configurar timeout para el handshake
        socket_ref.settimeout(5)  # Esperar máximo 5 segundos por el ENQ
        
        #Paso 2: Esperar ENQ del cliente
        if not silent:
            msg = "[PROTOCOLO ENGINE] Esperando ENQ..."
            print(msg)
            add_protocol_message(msg)
        
        try:
            enq = socket_ref.recv(1)
        except (ConnectionResetError, ConnectionAbortedError, OSError) as e:
            # Conexión cerrada antes de recibir ENQ - esto es normal, no es error
            if not silent:
                print(f"[PROTOCOLO ENGINE] Conexión cerrada antes del handshake (normal)")
            return False
        
        #Paso 3: Verificar que se recibió ENQ
        if not enq:
            # Socket cerrado sin datos - normal en reconexiones
            if not silent:
                print(f"[PROTOCOLO ENGINE] Conexión cerrada (sin ENQ recibido)")
            return False
        
        if enq != ENQ:
            error_msg = f"[PROTOCOLO ENGINE] ERROR: ENQ inválido (recibido: {enq.hex()})"
            print(error_msg)
            if not silent:
                add_protocol_message(error_msg)
            return False
        
        #Paso 4: Responder con ACK al cliente
        if not silent:
            msg = "[PROTOCOLO ENGINE] ENQ recibido. ACK enviado"
            print(msg)
            add_protocol_message(msg)
        socket_ref.sendall(ACK)
        
        #Paso 5: Restaurar timeout normal (None = blocking)
        socket_ref.settimeout(None)
        return True
        
    except socket.timeout:
        #Paso 6: Manejar timeout esperando ENQ (siempre mostrar errores)
        error_msg = "[PROTOCOLO ENGINE] ERROR: Timeout esperando ENQ"
        print(error_msg)
        if not silent:
            add_protocol_message(error_msg)
        return False
    except (ConnectionResetError, ConnectionAbortedError, OSError) as e:
        # Conexión cerrada - normal en algunos casos
        if not silent:
            print(f"[PROTOCOLO ENGINE] Conexión interrumpida durante handshake: {e}")
        return False
    except Exception as e:
        #Paso 7: Manejar otros errores (siempre mostrar errores)
        error_msg = f"[PROTOCOLO ENGINE] ERROR en handshake: {e}"
        print(error_msg)
        if not silent:
            add_protocol_message(error_msg)
        return False

def send_ack(socket_ref, silent=False):
    """Envía ACK (confirmación positiva) por el socket."""
    socket_ref.sendall(ACK)
    if not silent:
        print("[PROTOCOLO ENGINE] ACK enviado")

def send_nack(socket_ref):
    """Envía NACK (confirmación negativa) por el socket."""
    socket_ref.sendall(NACK)
    print("[PROTOCOLO ENGINE] NACK enviado")

# --- Variables de Estado del Engine ---
ENGINE_STATUS = {"health": "OK", "is_charging": False, "driver_id": None}
KAFKA_PRODUCER = None 
TELEMETRY_BUFFER = []  # Buffer de resiliencia cuando el broker no está disponible
LAST_MONITOR_TS = 0.0   # Marca de tiempo del último latido/orden del Monitor
CP_ID = ""
BROKER = None 
# Creamos un Lock global para proteger el acceso concurrente a ENGINE_STATUS.
# Un Lock (cerrojo) garantiza que solo un hilo a la vez puede modificar el estado compartido.
# Así evitamos condiciones de carrera si llegan comandos simultáneos (p. e.j, PARAR y REANUDAR al mismo tiempo).
status_lock = threading.Lock()

# Topic que espera la CENTRAL
KAFKA_TOPIC_TELEMETRY = "cp_telemetry"


# Funcion Productor Kafka para mandar la telemetria ---
def init_kafka_producer(broker):
    """Inicializa el productor Kafka para enviar telemetría a Central."""
    global KAFKA_PRODUCER
    #Paso 1: Verificar si ya existe un productor
    if KAFKA_PRODUCER is None:
        try:
            #Paso 1.1: Crear el productor Kafka
            KAFKA_PRODUCER = KafkaProducer(
                bootstrap_servers=[broker],
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
            )
            #Paso 1.2: Imprimir mensaje de éxito
            print(f"[ENGINE] Kafka producer conectado a {broker}")
            # Intentar vaciar buffer pendiente
            try:
                if TELEMETRY_BUFFER:
                    pending = list(TELEMETRY_BUFFER)
                    TELEMETRY_BUFFER.clear()
                    for payload in pending:
                        try:
                            KAFKA_PRODUCER.send(KAFKA_TOPIC_TELEMETRY, value=payload)
                        except Exception:
                            TELEMETRY_BUFFER.append(payload)
                            break
                    try:
                        KAFKA_PRODUCER.flush()
                    except Exception:
                        pass
            except Exception:
                pass
        except NoBrokersAvailable:
            #Paso 1.3: Manejar error de broker no disponible
            print(f"[ENGINE] WARNING: broker {broker} no disponible. Los mensajes se descartarán hasta reconexión.")
            #Paso 1.4: Lanzar hilo de reintento automático
            def _reconnect_loop(broker, interval=5):
                global KAFKA_PRODUCER
                while KAFKA_PRODUCER is None:
                    try:
                        #Paso 1.4.1: Intentar crear nuevo productor
                        tmp = KafkaProducer(
                            bootstrap_servers=[broker],
                            value_serializer=lambda v: json.dumps(v).encode('utf-8')
                        )
                        #Paso 1.4.2: Asignar el productor si es exitoso
                        KAFKA_PRODUCER = tmp
                        #Paso 1.4.3: Imprimir mensaje de éxito
                        print(f"[ENGINE] Reconectado a Kafka broker {broker}")
                        break
                    except Exception as e:
                        #Paso 1.4.4: Manejar error de reconexión
                        print(f"[ENGINE] Reconexión fallida a {broker}: {e} - reintentando en {interval}s")
                        time.sleep(interval)
            #Paso 1.5: Lanzar hilo de reintento en background
            threading.Thread(target=_reconnect_loop, args=(broker,), daemon=True).start()
        except Exception as e:
            #Paso 1.6: Manejar otros errores de inicialización
            print(f"[ENGINE] ERROR inicializando Kafka producer: {e}")
            #Paso 1.7: Lanzar hilo de reintento para reconectar en background
            threading.Thread(target=lambda: time.sleep(1) or init_kafka_producer(broker), daemon=True).start()

#Funcion para enviar la telemetria a la central
def send_telemetry_message(payload):
    """Envía payload JSON al topic cp_telemetry. No lanza si producer no existe."""
    global KAFKA_PRODUCER
    
    #Paso 1: Validar tipos de mensajes que pueden enviarse sin carga activa
    msg_type = payload.get('type', '').upper()
    if msg_type in ['AVERIADO', 'CONEXION_PERDIDA', 'FAULT', 'SESSION_STARTED', 'SUPPLY_END']:
        #Paso 1.1: Estos mensajes pueden enviarse sin estar cargando
        pass
    else:
        #Paso 1.2: Para mensajes de consumo, validar que hay driver activo
        with status_lock:
            if not ENGINE_STATUS.get('is_charging') or not ENGINE_STATUS.get('driver_id'):
                #Paso 1.2.1: Bloquear telemetría si no hay conductor activo
                print(f"[ENGINE] AVISO: Telemetría de consumo bloqueada - no hay conductor activo autorizado.")
                return
    
    
    #Paso 2: Verificar que el productor esté inicializado
    if KAFKA_PRODUCER is None:
        TELEMETRY_BUFFER.append(payload)
        print(f"[ENGINE] AVISO: Broker no disponible. Telemetría en buffer ({len(TELEMETRY_BUFFER)} pendientes).")
        return
    try:
        # Enviar pendientes primero
        if TELEMETRY_BUFFER:
            for p in list(TELEMETRY_BUFFER):
                try:
                    KAFKA_PRODUCER.send(KAFKA_TOPIC_TELEMETRY, value=p)
                    TELEMETRY_BUFFER.remove(p)
                except Exception:
                    break
        KAFKA_PRODUCER.send(KAFKA_TOPIC_TELEMETRY, value=payload)
        KAFKA_PRODUCER.flush()
    except Exception as e:
        print(f"[ENGINE] Error enviando telemetry: {e}. Guardando en buffer.")
        TELEMETRY_BUFFER.append(payload)

# --- Funciones de Utilidad ---
def clear_screen():
    """Limpia la pantalla de la terminal."""
    os.system('cls' if os.name == 'nt' else 'clear')




# Variable global para almacenar mensajes del protocolo
protocol_messages = []  # Lista circular de últimos mensajes
protocol_lock = threading.Lock()

def display_status():
    """Muestra el estado actual del Engine en pantalla con mensajes del protocolo sobre el menú."""
    clear_screen()
    
    # Sección superior: Mensajes del protocolo (últimos 5, similar al Monitor)
    with protocol_lock:
        protocol_msgs = protocol_messages.copy()
    
    if protocol_msgs:
        print("─" * 50)
        print("MENSAJES PROTOCOLO (últimos 5):")
        # Mostrar solo los últimos 5 mensajes
        for msg in protocol_msgs[-5:]:
            print(f"  {msg}")
        print("─" * 50)
        print()
    
    # Sección principal: Estado del Engine
    print(f"--- EV CHARGING POINT ENGINE: {CP_ID} ---")
    print("="*50)
    print(f"  ESTADO DE SALUD: {ENGINE_STATUS['health']}")
    print(f"  CARGANDO: {'SÍ' if ENGINE_STATUS['is_charging'] else 'NO'}")
    if ENGINE_STATUS['driver_id']:
        print(f"  DRIVER: {ENGINE_STATUS['driver_id']}")
    print("  Información de suministro: disponible en la CENTRAL (topic cp_telemetry)")
    print("="*50)
    print("Comandos: [F]AIL para simular AVERÍA | [R]ECOVER para recuperar")
    print("          [I]NIT para simular ENCHUFAR vehículo (Iniciar Suministro)")
    print("          [E]ND para simular DESENCHUFAR vehículo (Finalizar Suministro)")
    print("-" * 50)
    print("> ", end='', flush=True)  # Prompt para input


# --- Funciones de Comunicación con Monitor---

#HILO 1: Espera mensajes del Monitor cada 1 segundo y le responde con OK/KO usando protocolo
def handle_monitor_connection(monitor_socket, monitor_address):
    """Maneja la conexión síncrona con el Monitor local (EC_CP_M) usando protocolo <STX><DATA><ETX><LRC>."""
    global ENGINE_STATUS
    
    try:
        #Paso 1: Realizar handshake inicial (ENQ/ACK)
        # El Monitor ya envió ENQ, esperamos recibirlo y responder ACK
        try:
            monitor_socket.settimeout(2.0)  # Timeout corto para health checks rápidos
            enq = monitor_socket.recv(1)
            if not enq or enq != ENQ:
                # Si no es ENQ válido o la conexión se cerró, es normal en reconexiones
                return
            # Responder ACK al handshake
            monitor_socket.sendall(ACK)
            monitor_socket.settimeout(None)  # Restaurar timeout normal
        except (ConnectionResetError, ConnectionAbortedError, OSError, socket.timeout):
            # Conexión cerrada o timeout - normal en reconexiones frecuentes
            return
        
        #Paso 2: Recibir mensaje del Monitor usando protocolo (modo silencioso por defecto)
        data_string, is_valid = receive_frame(monitor_socket, silent=True)
        
        #Paso 2.1: Verificar que la trama es válida
        if not is_valid or not data_string:
            print(f"[ENGINE] ERROR: Trama inválida recibida del Monitor. Cerrando conexión.")
            send_nack(monitor_socket)  # Informar al Monitor que hubo error
            return
        
        #Paso 2.2: Determinar si es health check (para usar modo silencioso o no)
        is_health_check = data_string.startswith("HEALTH_CHECK")
        
        #Paso 2.3: Si NO es health check, mostrar información del protocolo en el panel
        if not is_health_check:
            # Solo añadir a mensajes del protocolo (no imprimir directamente para no interferir con el menú)
            add_protocol_message(f"← Recibido desde Monitor: '{data_string}'")
            # No imprimir aquí, se mostrará en el display_status
        
        #Paso 2.4: Enviar ACK confirmando recepción válida (modo silencioso para health checks)
        send_ack(monitor_socket, silent=is_health_check)
        # Actualizar último latido del Monitor
        try:
            global LAST_MONITOR_TS
            LAST_MONITOR_TS = time.time()
        except Exception:
            pass
        
        #Paso 3: Bloquear el acceso a ENGINE_STATUS mientras procesamos este comando
        #   Esto impide que otro hilo lo modifique al mismo tiempo
        with status_lock:
            
            #Paso 4: Procesar los comandos recibidos desde el monitor (ya viene parseado como string)
            #Paso 4.1: Comando HEALTH_CHECK
            if data_string.startswith("HEALTH_CHECK"):
                #Paso 4.1.1: Responder con el estado de salud actual usando protocolo (modo silencioso)
                response = ENGINE_STATUS['health'] 
                send_frame(monitor_socket, response, silent=is_health_check)
                # Esperar ACK del Monitor (sin mostrar mensaje para health checks)
                ack_response = monitor_socket.recv(1)
                # No imprimir confirmación para health checks repetitivos

            #Paso 4.2: Comando PARAR
            elif data_string.startswith("PARAR"):
                #Paso 4.2.1: Detener la carga si estaba en curso
                ENGINE_STATUS['is_charging'] = False
                #Paso 4.2.2: Confirmar acción al Monitor usando protocolo
                send_frame(monitor_socket, "ACK#PARAR", silent=is_health_check)
                # Añadir mensaje al panel del protocolo
                if not is_health_check:
                    add_protocol_message("→ Enviado a Monitor: ACK#PARAR")
                # Esperar ACK del Monitor
                ack_response = monitor_socket.recv(1)
                if ack_response == ACK and not is_health_check:
                    add_protocol_message("✓ Monitor confirmó recepción de ACK#PARAR")
            
            #Paso 4.3: Comando REANUDAR
            elif data_string.startswith("REANUDAR"):
                #Paso 4.3.1: REANUDAR solo cambia el estado operativo, NO el estado de salud
                # El estado de salud (OK/KO) solo cambia con comandos F/R del usuario
                # REANUDAR solo permite que el CP vuelva a operar si está fuera de servicio
                # No modifica ENGINE_STATUS['health'] porque eso es controlado manualmente por el usuario
                #Paso 4.3.2: Confirmar acción al Monitor usando protocolo
                send_frame(monitor_socket, "ACK#REANUDAR", silent=is_health_check)
                # Añadir mensaje al panel del protocolo
                if not is_health_check:
                    add_protocol_message("→ Enviado a Monitor: ACK#REANUDAR")
                # Esperar ACK del Monitor
                ack_response = monitor_socket.recv(1)
                if ack_response == ACK and not is_health_check:
                    add_protocol_message("✓ Monitor confirmó recepción de ACK#REANUDAR")

            #Paso 4.3b: Comando RECOVER (forzar salud a OK desde Central/Monitor)
            elif data_string.startswith("RECOVER"):
                #Paso 4.3b.1: Cambiar estado de salud a OK
                ENGINE_STATUS['health'] = 'OK'
                #Paso 4.3b.2: Confirmar al Monitor
                send_frame(monitor_socket, "ACK#RECOVER", silent=is_health_check)
                if not is_health_check:
                    add_protocol_message("→ Enviado a Monitor: ACK#RECOVER")
                # Esperar ACK del Monitor
                ack_response = monitor_socket.recv(1)
                if ack_response == ACK and not is_health_check:
                    add_protocol_message("✓ Monitor confirmó recepción de ACK#RECOVER")
            
            #Paso 4.4: Comando AUTORIZAR_SUMINISTRO
            elif data_string.startswith("AUTORIZAR_SUMINISTRO"):
                parts = data_string.split('#')
                if len(parts) >= 2:
                    #Paso 4.4.1: Extraer ID del driver
                    driver_id = parts[1]
                    # No imprimir aquí, se mostrará en el panel del protocolo
                    add_protocol_message(f"← AUTORIZAR_SUMINISTRO recibido: Driver {driver_id}")
                    #Paso 4.4.2: Registrar autorización sin iniciar carga aún
                    ENGINE_STATUS['driver_id'] = driver_id
                    #Paso 4.4.3: Confirmar autorización al Monitor usando protocolo
                    send_frame(monitor_socket, "ACK#AUTORIZAR_SUMINISTRO", silent=is_health_check)
                    add_protocol_message("→ Enviado a Monitor: ACK#AUTORIZAR_SUMINISTRO")
                    # Esperar ACK del Monitor
                    ack_response = monitor_socket.recv(1)
                    if ack_response == ACK:
                        add_protocol_message("✓ Monitor confirmó recepción de ACK#AUTORIZAR_SUMINISTRO")
                else:
                    #Paso 4.4.4: Manejar formato inválido
                    print(f"[ENGINE] ERROR: Formato de autorización inválido")
                    send_frame(monitor_socket, "NACK#AUTORIZAR_SUMINISTRO")
                    # Esperar ACK del Monitor
                    ack_response = monitor_socket.recv(1)
            
            #Paso 4.5: Comando desconocido
            else:
                #Paso 4.5.1: Responder con error para comandos no reconocidos usando protocolo
                add_protocol_message(f"⚠ Comando desconocido recibido: '{data_string}'")
                send_frame(monitor_socket, "ERROR", silent=is_health_check)
                # Esperar ACK del Monitor
                ack_response = monitor_socket.recv(1)

        #Paso 5: El Lock se libera automáticamente al salir del bloque 'with'

    except Exception as e:
        #Paso 6: Manejar errores de conexión con el Monitor
        print(f"\n[Engine] Conexión con Monitor local ({monitor_address}) perdida: {e}")
    finally:
        #Paso 7: Cerrar siempre el socket tras atender la petición
        monitor_socket.close()

#HILO 2: Levantamos el Servidor TCP local y su propósito es responder a los chequeos de salud del Monitor
def start_health_server(host, port):
    """Inicia el servidor de sockets que escucha los HEALTH_CHECK del Monitor."""
    #Paso 1: Crear el socket del servidor
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        #Paso 1.1: Asociar el socket a una IP y puerto
        server_socket.bind((host, port))
        #Paso 1.2: Poner el socket a escuchar conexiones entrantes
        server_socket.listen(1) # Solo necesitamos una conexión (la del Monitor)
        #Paso 1.3: Imprimir mensaje de éxito
        print(f"[Engine] Servidor de salud local escuchando en {host}:{port}")
    #Paso 1.4: Manejar errores de bind() o listen()
    except Exception as e:
        print(f"[Engine] ERROR: No se pudo iniciar el servidor de salud en {port}: {e}")
        sys.exit(1)
    
    
        #Paso 2: Bucle principal del servidor
    while True:
        try:
            #Paso 2.1: Esperar una conexión del Monitor
            #monitor_socket: el socket activo para hablar con ese cliente
            #address: la IP y puerto del cliente (Monitor)
            monitor_socket, address = server_socket.accept()
            # Marca latido del monitor (conexión entrante)
            global LAST_MONITOR_TS
            LAST_MONITOR_TS = time.time()
            #Paso 2.2: Crear un nuevo hilo para manejar la conexión
            monitor_thread = threading.Thread(
                target=handle_monitor_connection, 
                #el socket y la IP del Monitor como argumentos
                args=(monitor_socket, address)) 
            #Paso 2.3: Marcar el hilo como daemon para que se cierre automáticamente
            monitor_thread.daemon = True
            #Paso 2.4: Iniciar la ejecución del hilo
            monitor_thread.start()
        #Paso 2.5: Manejar interrupciones del teclado
        except KeyboardInterrupt:
            break
        #Paso 2.6: Manejar otros errores de conexión
        except Exception as e:
            print(f"[Engine] Error aceptando conexión del Monitor: {e}")
            time.sleep(1)
            
# --- Funciones de Simulación de Lógica de Negocio (Suministro) ---

#HILO 3: Simulación de Suministro / Producción Kafka
def simulate_charging(cp_id, broker, driver_id, price_per_kwh=0.20, step_kwh=0.1):
    """
    Simula el suministro:
    - Inicializa producer si es necesario.
    - Envía mensajes CONSUMO cada segundo con campos: type, cp_id, user_id, kwh, importe
    - Al terminar (usuario), envía SUPPLY_END con totales.
    - price_per_kwh se usa para calcular importe (opcional; la CENTRAL leerá su precio desde BD).
    """
    #Paso 1: Inicializar el productor Kafka
    init_kafka_producer(broker)
    #Paso 1.1: Inicializar variables de acumulación
    total_kwh = 0.0
    total_importe = 0.0
    aborted_due_to_fault = False

    #Paso 2: Actualizar estado del Engine
    with status_lock:
        ENGINE_STATUS['is_charging'] = True
        ENGINE_STATUS['driver_id'] = driver_id

    #Paso 2.1: Imprimir mensaje de inicio
    print(f"[ENGINE] Inicio suministro CP={cp_id} driver={driver_id}")
    try:
        #Paso 3: Bucle principal de simulación de carga
        while True:
            with status_lock:
                #Paso 3.1: Verificar si se debe detener la carga
                if not ENGINE_STATUS['is_charging']:
                    break
                #Paso 3.2: Verificar si hay avería durante la carga
                if ENGINE_STATUS['health'] != "OK":
                    #Paso 3.2.1: Crear mensaje de avería
                    payload_fault = {
                        "type": "AVERIADO",
                        "cp_id": cp_id,
                        "user_id": driver_id,
                        "kwh": round(total_kwh, 3),
                        "importe": round(total_importe, 2)
                    }
                    #Paso 3.2.2: Enviar mensaje de avería
                    send_telemetry_message(payload_fault)
                    aborted_due_to_fault = True
                    return

            #Paso 3.2.5: Verificar latidos del Monitor (resiliencia R1)
            try:
                if LAST_MONITOR_TS and (time.time() - LAST_MONITOR_TS) > 6:
                    # === INICIO DE LA CORRECCIÓN ===
                    msg = "[ENGINE] Monitor no responde. Finalizando suministro."
                    print(msg) # Lo dejamos por si acaso
                    add_protocol_message(msg) # Añadimos el mensaje al panel
                    # === FIN DE LA CORRECCIÓN ===
                    with status_lock:
                        ENGINE_STATUS['is_charging'] = False
                    # Enviar fin normal con los acumulados actuales
                    send_telemetry_message({
                        "type": "SUPPLY_END",
                        "cp_id": cp_id,
                        "user_id": driver_id,
                        "kwh": round(total_kwh, 3),
                        "importe": round(total_importe, 2)
                    })
                    break
            except Exception:
                pass

            #Paso 3.3: Incrementar consumo cada segundo
            total_kwh += step_kwh
            total_importe = total_kwh * price_per_kwh

            #Paso 3.4: Crear mensaje de consumo
            payload = {
                "type": "CONSUMO",
                "cp_id": cp_id,
                "user_id": driver_id,
                "kwh": round(total_kwh, 3),
                "importe": round(total_importe, 2)
            }
            # === INICIO DE LA MODIFICACIÓN (Persistencia de Suministro) ===
            try:
                # Guardar el estado actual en un fichero por si hay un "crash"
                current_charge_state = {
                    "driver_id": driver_id,
                    "total_kwh": total_kwh,
                    "total_importe": total_importe
                }
                # Usamos el CP_ID en el nombre del fichero para hacerlo único
                with open(f"cp_state_{cp_id}.json", "w") as f:
                    json.dump(current_charge_state, f)
            except Exception as e:
                add_protocol_message(f"ERROR: No se pudo guardar estado: {e}")
            # === FIN DE LA MODIFICACIÓN ===
            #Paso 3.5: Enviar telemetría de consumo
            send_telemetry_message(payload)
            #Paso 3.6: Esperar un segundo antes del siguiente incremento
            time.sleep(1)
    except KeyboardInterrupt:
        #Paso 4: Manejar interrupción del teclado
        pass
    finally:
       #Paso 5: Finalizar la simulación de carga
       #Paso 5.1: En caso de aborto por avería no enviamos SUPPLY_END; en caso normal sí
        if not aborted_due_to_fault:
            #Paso 5.1.1: Crear mensaje de fin de suministro
            payload_end = {
                "type": "SUPPLY_END",
                "cp_id": cp_id,
                "user_id": driver_id,
                "kwh": round(total_kwh, 3),
                "importe": round(total_importe, 2)
            }
            #Paso 5.1.2: Enviar mensaje de fin de suministro
            send_telemetry_message(payload_end)
        # === AÑADIR ESTO ===
        try:
            if os.path.exists(f"cp_state_{cp_id}.json"):
                os.remove(f"cp_state_{cp_id}.json")
        except Exception as e:
            print(f"[ENGINE] WARNING: No se pudo borrar el fichero de estado: {e}")
        # === FIN AÑADIDO ===
        #Paso 5.2: Limpiar estado del Engine
        with status_lock:
            ENGINE_STATUS['is_charging'] = False
            ENGINE_STATUS['driver_id'] = None


#HILO 4: Funciones de Entrada de Usuario (Simulación de Teclas)
def process_user_input():
    """Maneja la entrada del usuario para simular averías/eventos."""
    global ENGINE_STATUS
    #Paso 1: Bucle principal de entrada de usuario
    while True:
        try:
            #Paso 1.1: Leer comando del usuario
            #input() espera a que el usuario escriba algo
            #Mientras tanto, el hilo principal se detiene completamente
            #Nada más se ejecuta en ese hilo hasta que se pulse Enter
            # Nota: El display se actualiza en otro hilo, pero el input() aquí bloquea hasta Enter
            command = input().strip().upper()
            
            #Paso 2: Procesar comandos del usuario
            #Paso 2.1: Comando FAIL - Simular Avería (KO)
            if command == 'F' or command == 'FAIL':
                # Siempre cambiar a KO si se presiona F (independientemente del estado actual)
                with status_lock:
                    ENGINE_STATUS['health'] = 'KO'
                # Los mensajes se mostrarán en el panel del protocolo (no imprimir aquí para no interferir)
                add_protocol_message("Usuario: Estado cambiado a KO (AVERÍA)")
                # No llamar display_status aquí, se actualizará automáticamente
                
            #Paso 2.2: Comando RECOVER - Simular Recuperación
            elif command == 'R' or command == 'RECOVER':
                # Siempre cambiar a OK si se presiona R (permitir recuperación incluso si ya está OK)
                with status_lock:
                    old_health = ENGINE_STATUS['health']
                    ENGINE_STATUS['health'] = 'OK'
                if old_health == 'KO':
                    add_protocol_message("Usuario: Estado recuperado de KO a OK")
                else:
                    add_protocol_message("Usuario: Estado ya estaba en OK")
                # No llamar display_status aquí, se actualizará automáticamente
                
            #Paso 2.3: Comando INIT - Iniciar Suministro
            elif command == 'I' or command == 'INIT':
                print(f"\n[Engine] *** COMANDO INIT RECIBIDO ***")
                #Paso 2.3.1: Consultar a CENTRAL directamente si hay sesión activa autorizada
                try:
                    helper_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    helper_socket.connect(('127.0.0.1', ENGINE_PORT + 1000))
                    helper_socket.sendall(f"CHECK_SESSION#{CP_ID}".encode('utf-8'))
                    resp = helper_socket.recv(1024).decode('utf-8').strip()
                    helper_socket.close()
                except Exception as e:
                    print(f"[Engine] Error consultando sesión activa: {e}")
                    resp = "NO_SESSION"

                #Paso 2.3.2: Verificar respuesta de sesión
                if resp == "NO_SESSION":
                    print("[ENGINE] ERROR: No hay conductor autorizado para iniciar el suministro.")
                    add_protocol_message("No hay sesión: conecta el coche desde la app del conductor (SOLICITAR)")
                else:
                    #Paso 2.3.3: Extraer ID del driver
                    driver_id = resp
                    print(f"[ENGINE] Sesión validada para driver {driver_id}")
                    #Paso 2.3.4: Verificar que se puede iniciar
                    with status_lock:
                        can_start = not ENGINE_STATUS['is_charging'] and ENGINE_STATUS['health'] == 'OK'
                    if not can_start:
                        print("[ENGINE] No se puede iniciar: ya está cargando o estado KO.")
                    else:
                        #Paso 2.3.5: Iniciar suministro
                        print(f"[ENGINE] Iniciando suministro para driver {driver_id}...")
                        with status_lock:
                            ENGINE_STATUS['is_charging'] = True
                            ENGINE_STATUS['driver_id'] = driver_id
                        #Paso 2.3.6: Avisar a CENTRAL que la sesión comienza
                        send_telemetry_message({
                            "type": "SESSION_STARTED",
                            "cp_id": CP_ID,
                            "user_id": driver_id
                        })
                        #Paso 2.3.7: Iniciar hilo de simulación de carga
                        threading.Thread(
                            target=simulate_charging,
                            args=(CP_ID, BROKER, driver_id),
                            daemon=True
                        ).start()
                        print(f"[ENGINE] Carga iniciada tras validar sesión para driver {driver_id}.")
                #Paso 2.3.8: Actualizar display
                display_status()
                
            #Paso 2.4: Comando END - Finalizar Suministro
            elif command == 'E' or command == 'END':
                with status_lock:
                    if ENGINE_STATUS['is_charging']:
                        #Paso 2.4.1: Finalizar suministro
                        print(f"[ENGINE] Finalizando suministro...")
                        ENGINE_STATUS['is_charging'] = False
                        print(f"[ENGINE] *** SUMINISTRO FINALIZADO ***. Notificando a Central...")
                    else:
                        #Paso 2.4.2: No hay suministro activo
                        print(f"[ENGINE] No hay suministro activo para finalizar.")
                #Paso 2.4.3: Actualizar display
                display_status()
              #Paso 3: Manejar errores de entrada
              #End Of File (fin de entrada) 
              #Este error ocurre cuando input() intenta leer del teclado, pero no hay más entrada disponible 
        except EOFError:
            pass 
        except Exception as e:
            print(f"Error en la entrada de usuario: {e}")

# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    #Paso 1: Obtener los argumentos de la línea de comandos
    if len(sys.argv) != 4:
        print("Uso: py ev_cp_e.py <PUERTO_ESCUCHA_ENGINE> <KAFKA_BROKER_IP:PORT> <ID_CP>")
        print("Ejemplo: py ev_cp_e.py 8001 localhost:9092 MAD-01")
        sys.exit(1)
    #Paso 2: Extraer los argumentos
    try:
        ENGINE_PORT = int(sys.argv[1])
        KAFKA_BROKER = sys.argv[2]
        CP_ID = sys.argv[3]
        ENGINE_HOST = '0.0.0.0' #significa que el servidor acepta conexiones desde cualquier IP local
        

        # Paso 3: Guardamos broker global para que otros hilos lo usen al arrancar simulate_charging
        BROKER = KAFKA_BROKER
        # Paso 4: Inicializar el Productor Kafka (una sola vez)
        init_kafka_producer(KAFKA_BROKER)
        print(f"[Engine] Productor Kafka inicializado para {KAFKA_BROKER}")
        # === INICIO DE LA MODIFICACIÓN (Persistencia de Suministro) ===
        STATE_FILE = f"cp_state_{CP_ID}.json"
        if os.path.exists(STATE_FILE):
            try:
                print(f"[ENGINE] (!) Detectado fichero de estado: {STATE_FILE}.")
                print("[ENGINE] (!) Intentando recuperar suministro interrumpido...")
                with open(STATE_FILE, "r") as f:
                    previous_state = json.load(f)
                
                # Enviar el estado final (ticket) del suministro interrumpido
                payload_end = {
                    "type": "SUPPLY_END",
                    "cp_id": CP_ID,
                    "user_id": previous_state.get('driver_id'),
                    "kwh": previous_state.get('total_kwh', 0),
                    "importe": previous_state.get('total_importe', 0)
                }
                send_telemetry_message(payload_end)
                print(f"[ENGINE] (!) Estado final del suministro anterior enviado a Central.")
                
                # Limpiar el fichero de estado
                os.remove(STATE_FILE)
                print(f"[ENGINE] (!) Fichero de estado {STATE_FILE} limpiado.")
            except Exception as e:
                print(f"[ENGINE] ERROR: No se pudo procesar el fichero de estado: {e}")
        # === FIN DE LA MODIFICACIÓN ===
        
        # Paso 5: Iniciar los hilos
        # Paso 5.1: Iniciar el servidor TCP de sockets para el Monitor (hilo de salud)
        threading.Thread(target=start_health_server, args=(ENGINE_HOST, ENGINE_PORT), daemon=True).start()

        # Paso 5.2: Hilo para actualizar panel visual periódicamente (cada 2 segundos)
        def update_display_periodically():
            """Actualiza el panel visual cada 2 segundos."""
            while True:
                try:
                    display_status()
                    time.sleep(2)
                except Exception as e:
                    # Si hay error, esperar y continuar
                    time.sleep(2)
        
        display_thread = threading.Thread(target=update_display_periodically, daemon=True)
        display_thread.start()

        # Paso 5.3: Iniciar el hilo principal para la entrada de comandos del usuario
        print("Engine iniciado. Listo para comandos. Presiona Ctrl+C para detener.")
        process_user_input()
           
           #Si alguien pone letras donde debería ir un número
    except ValueError:
        print("Error: El puerto debe ser un número entero.")
        sys.exit(1)
    except Exception as e:
        print(f"Error fatal al iniciar el Engine: {e}")
        sys.exit(1)
    #Permite que si el usuario pulsa Ctrl + C, el programa se cierre limpiamente.
    except KeyboardInterrupt:
        print("\nEngine detenido por el usuario.")
        sys.exit(0)