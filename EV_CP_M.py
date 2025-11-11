import socket # para tener una comunicacion por red
import sys # para usar parametros desde consola
import time # para manejar el tiempo
import threading # para poder ejecutar en paralelo
import database
from datetime import datetime
import os

# Control de verbosidad del Monitor (reduce prints en consola)
MONITOR_VERBOSE = False
def clear_screen():
    """Limpia la pantalla de la consola."""
    os.system('cls' if os.name == 'nt' else 'clear')

def push_health_message(msg):
    """Añade un mensaje de health check (máximo 5)."""
    with monitor_ui_lock:
        monitor_state['health_messages'].append(f"{datetime.now().strftime('%H:%M:%S')} - {msg}")
        if len(monitor_state['health_messages']) > 5:
            monitor_state['health_messages'].pop(0)

def push_protocol_message(msg):
    """Añade un mensaje del protocolo (máximo 5)."""
    with monitor_ui_lock:
        monitor_state['protocol_messages'].append(f"{datetime.now().strftime('%H:%M:%S')} - {msg}")
        if len(monitor_state['protocol_messages']) > 5:
            monitor_state['protocol_messages'].pop(0)


def display_monitor_panel(cp_id):
    """Muestra el panel de monitorización del CP Monitor."""
    with monitor_ui_lock:
        engine_status = monitor_state['engine_status']
        central_status = monitor_state['central_status']
        connection_info = monitor_state['connection_info'].copy()
        health_messages = monitor_state['health_messages'].copy()
        protocol_messages = monitor_state['protocol_messages'].copy()
    
    clear_screen()
    
    # --- Encabezado ---
    print("=" * 80)
    print(f"  EV CHARGING POINT MONITOR: {cp_id}")
    print("=" * 80)
    print(f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # --- Sección 1: Estado de Conexiones ---
    print("-" * 80)
    print("*** ESTADO DE CONEXIONES ***")
    print("-" * 80)
    
    # Estado Engine
    print(f"  Engine (Local):")
    print(f"    Estado: {engine_status}")
    if connection_info.get('engine'):
        print(f"    Conectado en: {connection_info['engine']}")
    
    # Estado Central
    print(f"  Central:")
    print(f"    Estado:{central_status}")
    if connection_info.get('central'):
        print(f"    Conectado en: {connection_info['central']}")
    
    print()
    
    # --- Sección 2: Health Checks Recientes ---
    print("-" * 80)
    print("*** HEALTH CHECKS RECIENTES ***")
    print("-" * 80)
    if health_messages:
        for msg in health_messages[-1:]:
            print(f"  {msg}")
    else:
        print("  No hay health checks registrados aún.")
    print()
    
    

    # --- Sección 3: Mensajes del Protocolo ---
    print("-" * 80)
    print("*** MENSAJES DEL PROTOCOLO (últimos 5) ***")
    print("-" * 80)
    if protocol_messages:
        for msg in protocol_messages[-5:]:
            print(f"  {msg}")
    else:
        print("  No hay mensajes del protocolo.")
    print()
    
    # --- Pie ---
    print("=" * 80)

# Constantes de configuracion (ajustalas segun tu necesidad de prueba)
HEARTBEAT_INTERVAL_TO_CENTRAL = 15 # Cada 15s, informamos a la Central (No usado aun)
HEARTBEAT_INTERVAL_TO_ENGINE = 1  # Cada 1s, comprobamos el Engine
ENGINE_COMMAND_GRACE_WINDOW = 3   # Segundos de gracia tras enviar comando al Engine
PANEL_UPDATE_INTERVAL = 2  # Cada 2s, actualizamos el panel visual

# --- Variables globales para UI del Monitor ---
monitor_ui_lock = threading.Lock()
monitor_state = {
    'engine_status': "Desconectado",  # "OK", "KO", "Desconectado"
    'central_status': "Desconectado",  # "Conectado", "Desconectado"
    'connection_info': {},
    'health_messages': [],  # Últimos mensajes de health check
    'protocol_messages': [],  # Últimos mensajes del protocolo
}

# Lock para proteger envíos/lecturas por el socket compartido con la Central
central_socket_lock = threading.Lock()
last_engine_command_ts = [0.0]

def recently_sent_engine_command():
    try:
        return (time.time() - last_engine_command_ts[0]) <= ENGINE_COMMAND_GRACE_WINDOW
    except Exception:
        return False

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
    if MONITOR_VERBOSE:
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
        if MONITOR_VERBOSE:
            print(f"[PROTOCOLO] ERROR: Trama demasiado corta ({len(frame_bytes)} bytes). Mínimo necesario: 4 bytes")
        return None, False
    
    #Paso 2: Verificar que el primer byte sea STX (0x02)
    if frame_bytes[0] != 0x02:
        if MONITOR_VERBOSE:
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
        if MONITOR_VERBOSE:
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
        if MONITOR_VERBOSE:
            print(f"[PROTOCOLO] ERROR: LRC no coincide. Recibido: 0x{received_lrc:02X}, Esperado: 0x{expected_lrc:02X}")
        return None, False  # LRC no coincide, hay error en la transmisión
    
    #Paso 10: Decodificar los datos a string UTF-8
    try:
        data = data_bytes.decode('utf-8')
        if MONITOR_VERBOSE:
            print(f"[PROTOCOLO] Trama parseada correctamente: '{data}' (LRC válido: 0x{received_lrc:02X})")
        return data, True
    except UnicodeDecodeError as e:
        if MONITOR_VERBOSE:
            print(f"[PROTOCOLO] ERROR: No se pudo decodificar los datos como UTF-8: {e}")
        return None, False

def parse_frame_silent(frame_bytes):
    """
    Versión silenciosa de parse_frame (solo para health checks repetitivos).
    No imprime mensajes, solo valida y devuelve resultado.
    """
    if len(frame_bytes) < 4:
        return None, False
    if frame_bytes[0] != 0x02:
        return None, False
    
    etx_pos = -1
    for i in range(1, len(frame_bytes) - 1):
        if frame_bytes[i] == 0x03:
            etx_pos = i
            break
    
    if etx_pos == -1:
        return None, False
    
    data_bytes = frame_bytes[1:etx_pos]
    received_lrc = frame_bytes[etx_pos + 1]
    
    message_with_delimiters = STX + data_bytes + ETX
    expected_lrc = calculate_lrc(message_with_delimiters)
    
    if received_lrc != expected_lrc:
        return None, False
    
    try:
        data = data_bytes.decode('utf-8')
        return data, True
    except UnicodeDecodeError:
        return None, False

def send_frame(socket_ref, data_string, silent=False):
    """
    Envía una trama completa a través de un socket usando el protocolo <STX><DATA><ETX><LRC>.
    
    Args:
        socket_ref: Referencia al socket donde enviar la trama
        data_string: String con los datos a enviar
        silent: Si es True, no imprime mensajes (útil para health checks repetitivos)
    
    Returns:
        bool: True si el envío fue exitoso, False en caso contrario
    """
    try:
        #Paso 1: Construir la trama con el protocolo
        if not silent:
            frame = build_frame(data_string)
        else:
            # Modo silencioso: construir sin imprimir
            data = data_string.encode('utf-8')
            message = STX + data + ETX
            lrc_value = calculate_lrc(message)
            frame = message + bytes([lrc_value])
        
        #Paso 2: Enviar la trama por el socket
        socket_ref.sendall(frame)
        #Paso 3: Mostrar confirmación en consola solo si no es silencioso
        if not silent and MONITOR_VERBOSE:
            print(f"[PROTOCOLO] Trama enviada correctamente: '{data_string}'")
        return True
    except Exception as e:
        #Paso 4: Manejar errores de envío (siempre mostrar errores)
        print(f"[PROTOCOLO] ERROR al enviar trama '{data_string}': {e}")
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
            if not silent and MONITOR_VERBOSE:
                print("[PROTOCOLO] Conexión cerrada por el remoto (no se recibieron datos)")
            return None, False
        
        #Paso 4: Parsear la trama recibida (modo silencioso o normal)
        if silent:
            # Parseo silencioso: no imprimir, solo validar
            data, is_valid = parse_frame_silent(frame_bytes)
        else:
            data, is_valid = parse_frame(frame_bytes)
        
        return data, is_valid
        
    except socket.timeout:
        #Paso 5: Manejar timeout (siempre mostrar errores)
        if MONITOR_VERBOSE:
            print(f"[PROTOCOLO] Timeout esperando trama (timeout={timeout}s)")
        return None, False
    except Exception as e:
        #Paso 6: Manejar otros errores (siempre mostrar errores)
        if MONITOR_VERBOSE:
            print(f"[PROTOCOLO] ERROR al recibir trama: {e}")
        return None, False

def handshake_client(socket_ref, silent=False):
    """
    Realiza el handshake inicial (ENQ/ACK) desde el lado cliente.
    El cliente envía ENQ y espera ACK o NACK del servidor.
    
    Args:
        socket_ref: Referencia al socket de conexión
        silent: Si es True, no imprime mensajes normales (solo errores)
    
    Returns:
        bool: True si el handshake fue exitoso (se recibió ACK), False en caso contrario
    """
    try:
        #Paso 1: Enviar ENQ (Enquiry) al servidor
        if not silent and MONITOR_VERBOSE:
            print("[PROTOCOLO] Enviando ENQ (handshake inicial)...")
        socket_ref.sendall(ENQ)
        
        #Paso 2: Esperar respuesta del servidor (ACK o NACK)
        response = socket_ref.recv(1)
        
        #Paso 3: Verificar la respuesta recibida
        if not response:
            print("[PROTOCOLO] ERROR: No se recibió respuesta al ENQ")
            return False
        
        #Paso 4: Decodificar la respuesta
        if response == ACK:
            if not silent and MONITOR_VERBOSE:
                print("[PROTOCOLO] Handshake exitoso: Servidor respondió ACK")
            return True
        elif response == NACK:
            if MONITOR_VERBOSE:
                print("[PROTOCOLO] Handshake fallido: Servidor respondió NACK")
            return False
        else:
            if MONITOR_VERBOSE:
                print(f"[PROTOCOLO] ERROR: Respuesta de handshake inválida (recibido: 0x{response[0]:02X})")
            return False
            
    except Exception as e:
        #Paso 5: Manejar errores durante el handshake (siempre mostrar errores)
        if MONITOR_VERBOSE:
            print(f"[PROTOCOLO] ERROR durante handshake: {e}")
        return False

def send_ack(socket_ref, silent=False):
    """Envía ACK (confirmación positiva) por el socket."""
    socket_ref.sendall(ACK)
    if not silent and MONITOR_VERBOSE:
        print("[PROTOCOLO] ACK enviado")

def send_nack(socket_ref):
    """Envía NACK (confirmación negativa) por el socket."""
    socket_ref.sendall(NACK)
    if MONITOR_VERBOSE:
        print("[PROTOCOLO] NACK enviado")

def send_eot(socket_ref):
    """Envía EOT (End of Transmission) para indicar cierre de conexión."""
    socket_ref.sendall(EOT)
    if MONITOR_VERBOSE:
        print("[PROTOCOLO] EOT enviado (fin de transmisión)") 

#HILO 1: Funciones de Conexion y Control Local (Monitorizacion del Engine)
def monitor_engine_health(engine_host, engine_port, cp_id, central_socket_ref):
    """
    Supervisa continuamente el estado del Engine
    Si el Engine no responde o devuelve un error, se notifica a la Central.
    Si la Central no está disponible, se limpia la conexión para forzar una reconexión.
    """
    #Paso 1: Inicializar variables de control
    if MONITOR_VERBOSE:
        print(f"Iniciando monitorizacion del Engine en {engine_host}:{engine_port}")
    engine_failed = False  # bandera para no repetir FAULTs innecesarios
    
    #Paso 2: Bucle principal de monitorización
    while True:
        try:
            #Paso 2.1: Crear un socket temporal para comprobar la salud del Engine
            # Usaremos una conexion temporal simple creando un nuevo socket TCP/IP
            #protocolo IP v4  #protocolo TCP (conexion orientada)
            engine_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
            #Paso 2.2: Intentar conectarse al servidor (el Engine)
            #Intenta conectarse al servidor (el Engine), usando la IP y el puerto.
            engine_socket.connect((engine_host, engine_port)) # si no hay nadie lanza excepcion ConnectionRefusedError
            #Paso 2.3: Configurar timeout para la respuesta
            #Esperar respuesta del Engine (maximo 1 segundo)
            engine_socket.settimeout(1.5) # indica que la operacion recv de engine_socket deber esperar como max 1.5s y si no hay respuesta en ese tiempo, lanza socket.timeout
            
          
            #Paso 2.4: Realizar handshake inicial (ENQ/ACK) con Engine (modo silencioso para health checks)
            if not handshake_client(engine_socket, silent=True):
                engine_socket.close()
                if recently_sent_engine_command():
                    time.sleep(0.3)
                    continue
                if MONITOR_VERBOSE:
                    print(f"[Monitor] ERROR: Handshake fallido con Engine. Reintentando...")
                engine_failed = True
                continue
            
            #Paso 2.5: Enviar mensaje de comprobacion de salud al Engine usando protocolo (modo silencioso)
            health_suffix = ""
            if recently_sent_engine_command():
                health_suffix = "#AFTER_CMD"
            health_message = f"HEALTH_CHECK#{cp_id}{health_suffix}"
            if not send_frame(engine_socket, health_message, silent=True):
                engine_socket.close()
                if recently_sent_engine_command():
                    time.sleep(0.3)
                    continue
                if MONITOR_VERBOSE:
                    print(f"[Monitor] ERROR: No se pudo enviar HEALTH_CHECK al Engine")
                engine_failed = True
                continue
            
            #Paso 2.6: Esperar ACK del Engine (sin imprimir)
            ack_response = engine_socket.recv(1)
            if ack_response != ACK:
                engine_socket.close()
                if recently_sent_engine_command():
                    time.sleep(0.3)
                    continue
                if MONITOR_VERBOSE:
                    print(f"[Monitor] ERROR: Engine no confirmó recepción de HEALTH_CHECK")
                engine_failed = True
                continue
            
            #Paso 2.7: Esperar la respuesta del Engine usando protocolo (modo silencioso)
            response_string, is_valid = receive_frame(engine_socket, timeout=1.5, silent=True)
            
            #Paso 2.8: Verificar validez de la respuesta
            if not is_valid or not response_string:
                engine_socket.close()
                if recently_sent_engine_command():
                    time.sleep(0.3)
                    continue
                if MONITOR_VERBOSE:
                    print(f"[Monitor] ERROR: Respuesta inválida del Engine")
                engine_failed = True
                continue
            
            #Paso 2.9: Enviar ACK confirmando recepción de la respuesta (modo silencioso)
            send_ack(engine_socket, silent=True)
            
            #Paso 2.10: Usar response_string en lugar de response
            response = response_string.strip()
            
            # Actualizar estado y mensajes para UI
            with monitor_ui_lock:
                monitor_state['engine_status'] = response
                monitor_state['connection_info']['engine'] = f"{engine_host}:{engine_port}"
            
            # Añadir mensaje de health check
            push_health_message(f"Health Check: Engine respondió '{response}'")
            
            #Paso 2.6: Procesar la respuesta del Engine
            # IMPORTANTE: Solo cambiar el estado si la respuesta cambia realmente
            if response != "OK":
                #Paso 2.6.1: Engine devolvió KO → reportar fallo si no se había hecho ANTES
                # Solo enviar FAULT si antes estaba en OK (primera vez que detectamos KO)
                if not engine_failed:
                    if MONITOR_VERBOSE:
                        print(f"[Monitor] Engine KO detectado → Enviando FAULT#{cp_id}")
                    push_protocol_message(f"Enviando FAULT#{cp_id} a Central")
                    #Paso 2.6.1.1: Enviar FAULT a Central si está conectada usando protocolo
                    if central_socket_ref[0]:
                        try:
                            # Enviar FAULT protegido por lock para evitar carreras con otros hilos
                            with central_socket_lock:
                                send_frame(central_socket_ref[0], f"FAULT#{cp_id}")
                                # Consumir el ACK (1 byte) que envía la Central por recibir una trama válida
                                try:
                                    central_socket_ref[0].settimeout(0.5)
                                    _ack = central_socket_ref[0].recv(1)
                                except Exception:
                                    pass
                                finally:
                                    central_socket_ref[0].settimeout(5.0)
                        except Exception as e:
                            print(f"[Monitor] Error enviando FAULT: {e}")
                    engine_failed = True  # Marcar que ahora está en fallo
                # Si engine_failed ya era True, significa que ya habíamos reportado el fallo anteriormente
                # No hacer nada más, solo mantener el estado

            else:
                #Paso 2.6.2: Engine responde OK
                # IMPORTANTE: Solo enviar RECOVER si ANTES estaba en fallo (engine_failed == True)
                # Esto asegura que solo se recupera cuando realmente cambia de KO a OK
                if engine_failed:
                    # El Engine cambió de KO a OK → es una recuperación legítima
                    if MONITOR_VERBOSE:
                        print(f"[Monitor] Engine recuperado (cambió de KO a OK) → Enviando RECOVER#{cp_id}")
                    push_protocol_message(f"Enviando RECOVER#{cp_id} a Central")
                    #Paso 2.6.2.1: Enviar RECOVER a Central si está conectada usando protocolo
                    if central_socket_ref[0]:
                        try:
                            # Enviar RECOVER protegido por lock para evitar carreras con otros hilos
                            with central_socket_lock:
                                send_frame(central_socket_ref[0], f"RECOVER#{cp_id}")
                                # Consumir el ACK (1 byte) que envía la Central por recibir una trama válida
                                try:
                                    central_socket_ref[0].settimeout(0.5)
                                    _ack = central_socket_ref[0].recv(1)
                                except Exception:
                                    pass
                                finally:
                                    central_socket_ref[0].settimeout(5.0)
                        except Exception as e:
                            print(f"[Monitor] Error enviando RECOVER: {e}")
                    engine_failed = False  # Marcar que ahora está recuperado
                # Si engine_failed ya era False, significa que ya estaba OK, no hacer nada
                
                #Paso 2.7: Manejar errores de red inesperados
        except (socket.error, ConnectionRefusedError, socket.timeout):
            #Paso 2.7.1: No se pudo contactar con el Engine
            with monitor_ui_lock:
                monitor_state['engine_status'] = "Desconectado"
            
            push_health_message("ERROR: Engine inalcanzable")
            
            if recently_sent_engine_command():
                time.sleep(0.3)
                continue

            if not engine_failed:
                print(f"[Monitor] Engine inalcanzable → Enviando FAULT#{cp_id}")
                push_protocol_message(f"Enviando FAULT#{cp_id} a Central (Engine inalcanzable)")
                #Paso 2.7.1.1: Enviar FAULT a Central si está conectada usando protocolo
                if central_socket_ref[0]:
                    try:
                        send_frame(central_socket_ref[0], f"FAULT#{cp_id}")
                        # Esperar ACK de Central
                        ack_response = central_socket_ref[0].recv(1)
                        if ack_response == ACK:
                            if MONITOR_VERBOSE:
                                print(f"[Monitor] Central confirmó recepción de FAULT")
                            push_protocol_message("Central confirmó recepción de FAULT")
                    except Exception as e:
                        if MONITOR_VERBOSE:
                            print(f"[Monitor] No se pudo enviar FAULT: {e}")
                engine_failed = True
            
        #Paso 2.8: Capturar cualquier otro error inesperado que no sea de red    
        except Exception as e:
            if recently_sent_engine_command():
                time.sleep(0.3)
                continue
            print(f"Error inesperado en la monitorizacion del Engine: {e}")
            
        finally:
            #Paso 2.9: Limpiar recursos
            #Pase lo que pase (exito o error), nos aseguramos de cerrar el socket del Engine en cada ciclo 
            try:
                engine_socket.close()
            except Exception:
                pass
            #Paso 2.10: Esperar antes del siguiente chequeo
            #Esperar un poco antes del siguiente chequeo
            time.sleep(HEARTBEAT_INTERVAL_TO_ENGINE) # Comprobamos cada 1 segundo


#HILO 2: Funcion que se conecta al servidor del engine, le manda el comando recibido desde la central, recibe un ACK/NACK y le envia la respuesta a la central
def send_command_to_engine(engine_host, engine_port, command, central_socket):
    """Envía el comando PARAR o REANUDAR al Engine local usando protocolo y responde con ACK/NACK a la Central."""
    try:
        try:
            last_engine_command_ts[0] = time.time()
        except Exception:
            pass
        #Paso 1: Crear conexión con el Engine
        engine_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        #Paso 1.1: Conectar al Engine
        engine_socket.connect((engine_host, engine_port))
        
        #Paso 1.2: Realizar handshake inicial (ENQ/ACK) con Engine
        push_protocol_message(f"Conectando a Engine para enviar: {command}")
        if not handshake_client(engine_socket):
            print(f"[Monitor] ERROR: Handshake fallido con Engine")
            push_protocol_message(f"✗ Handshake fallido para comando {command}")
            engine_socket.close()
            send_frame(central_socket, f"NACK#{command.split('#')[0]}")
            return
        
        #Paso 1.3: Enviar comando al Engine usando protocolo
        push_protocol_message(f"→ Enviando a Engine: {command}")
        if not send_frame(engine_socket, command):
            print(f"[Monitor] ERROR: No se pudo enviar comando al Engine")
            push_protocol_message(f"✗ Error enviando {command} al Engine")
            engine_socket.close()
            send_frame(central_socket, f"NACK#{command.split('#')[0]}")
            return
        
        #Paso 1.4: Esperar ACK del Engine
        ack_response = engine_socket.recv(1)
        if ack_response != ACK:
            print(f"[Monitor] ERROR: Engine no confirmó recepción del comando")
            push_protocol_message(f"✗ Engine no confirmó recepción de {command}")
            engine_socket.close()
            send_frame(central_socket, f"NACK#{command.split('#')[0]}")
            return
        
        #Paso 1.5: Recibir respuesta del Engine usando protocolo
        response_string, is_valid = receive_frame(engine_socket)
        
        #Paso 1.6: Cerrar conexión con el Engine
        engine_socket.close()
        
        #Paso 1.7: Verificar validez de la respuesta
        if not is_valid or not response_string:
            print(f"[Monitor] ERROR: Respuesta inválida del Engine")
            push_protocol_message(f"✗ Respuesta inválida del Engine para {command}")
            send_frame(central_socket, f"NACK#{command.split('#')[0]}")
            return

        #Paso 2: Procesar respuesta del Engine
        push_protocol_message(f"← Respuesta del Engine: {response_string}")
        if response_string.startswith("ACK"):
            #Paso 2.1: Engine confirmó comando → enviar ACK a Central usando protocolo
            # Extraer solo el comando base (sin parámetros) para el ACK a Central
            command_base = command.split('#')[0]
            push_protocol_message(f"→ Enviando ACK#{command_base} a Central")
            # IMPORTANTE: Enviar ACK#PARAR/ACK#REANUDAR a la Central
            # La Central procesará este mensaje en su bucle de receive_frame y actualizará el estado
            # NO esperamos ACK de confirmación aquí porque el ACK#PARAR YA ES la respuesta al comando PARAR
            # La Central enviará su ACK después de procesar el mensaje en su bucle principal
            try:
                with central_socket_lock:
                    send_frame(central_socket, f"ACK#{command_base}")
                    # Consumir el ACK de la Central (1 byte) para no dejarlo suelto en el socket
                    try:
                        central_socket.settimeout(0.5)
                        _ack = central_socket.recv(1)
                    except Exception:
                        pass
                    finally:
                        central_socket.settimeout(5.0)
                push_protocol_message(f"ACK#{command_base} enviado a Central (procesando...)")
            except Exception as e:
                print(f"[Monitor] ERROR al enviar ACK a Central: {e}")
                push_protocol_message(f"Error enviando ACK#{command_base}: {e}")
        else:
            #Paso 2.2: Engine rechazó comando → enviar NACK a Central usando protocolo
            command_base = command.split('#')[0]
            push_protocol_message(f"→ Enviando NACK#{command_base} a Central")
            # IMPORTANTE: Enviar NACK#PARAR/NACK#REANUDAR a la Central
            # La Central procesará este mensaje en su bucle de receive_frame
            # NO esperamos ACK de confirmación aquí porque el NACK#PARAR YA ES la respuesta al comando PARAR
            try:
                with central_socket_lock:
                    send_frame(central_socket, f"NACK#{command_base}")
                    # Consumir el ACK de la Central (1 byte) para no dejarlo suelto en el socket
                    try:
                        central_socket.settimeout(0.5)
                        _ack = central_socket.recv(1)
                    except Exception:
                        pass
                    finally:
                        central_socket.settimeout(5.0)
                print(f"[Monitor] Engine rechazó comando {command}. NACK#{command_base} enviado a Central.")
                push_protocol_message(f"✓ NACK#{command_base} enviado a Central (procesando...)")
            except Exception as e:
                print(f"[Monitor] ERROR al enviar NACK a Central: {e}")
                push_protocol_message(f"Error enviando NACK#{command_base}: {e}")

    except Exception as e:
        #Paso 3: Manejar errores de comunicación
        if MONITOR_VERBOSE:
            print(f"[Monitor] Error al contactar con Engine ({command}): {e}")
        push_protocol_message(f"Error al contactar con Engine: {e}")
        #Paso 3.1: Enviar NACK a Central por error usando protocolo
        try:
            command_base = command.split('#')[0] if '#' in command else command
            send_frame(central_socket, f"NACK#{command_base}")
        except:
            pass  # Si falla, no podemos hacer nada más

#HILO 3: Funcion para manejar la comunicación con el Engine
def handle_engine_communication(engine_host, engine_port, cp_id, central_ip, central_port):
    """Maneja la comunicación con el Engine para verificar asignación de driver."""
    #Paso 1: Crear servidor de comunicación con Engine
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        #Paso 1.1: Configurar servidor en puerto diferente para evitar conflictos
        server_socket.bind(('127.0.0.1', engine_port + 1000))  # Puerto diferente para evitar conflictos
        #Paso 1.2: Poner servidor a escuchar conexiones
        server_socket.listen(1)
        if MONITOR_VERBOSE:
            print(f"[Monitor] Servidor de comunicación con Engine escuchando en puerto {engine_port + 1000}")
        
        #Paso 2: Bucle principal de comunicación con Engine
        while True:
            try:
                #Paso 2.1: Aceptar conexión del Engine
                client_socket, address = server_socket.accept()
                #Paso 2.2: Recibir datos del Engine
                data = client_socket.recv(1024).decode('utf-8').strip()
                
                #Paso 2.3: Procesar comandos del Engine
                #Paso 2.3.1: Comando CHECK_DRIVER_ASSIGNMENT
                if data.startswith("CHECK_DRIVER_ASSIGNMENT"):
                    #Paso 2.3.1.1: Verificar asignación de driver consultando a la Central
                    try:
                        #Paso 2.3.1.1.1: Crear conexión con Central
                        central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        central_socket.connect((central_ip, central_port))
                        # Handshake protocolo y consulta con frames
                        if not handshake_client(central_socket, silent=True):
                            raise Exception("handshake fallido")
                        send_frame(central_socket, f"CHECK_DRIVER#{cp_id}", silent=True)
                        _ = central_socket.recv(1)  # ACK de Central
                        response, ok = receive_frame(central_socket, timeout=2.0, silent=True)
                        try:
                            send_ack(central_socket, silent=True)
                        except Exception:
                            pass
                        central_socket.close()
                        response = response if ok and response else "NO_DRIVER"
                        #Paso 2.3.1.1.5: Enviar respuesta al Engine
                        client_socket.sendall(response.encode('utf-8'))
                    except Exception as e:
                        print(f"[Monitor] Error verificando asignación de driver: {e}")
                        #Paso 2.3.1.1.6: Enviar respuesta de error al Engine
                        client_socket.sendall(b"NO_DRIVER")
                        
                #Paso 2.3.2: Comando CHECK_SESSION
                elif data.startswith("CHECK_SESSION"):
                    #Paso 2.3.2.1: Verificar sesión activa autorizada consultando a la Central
                    try:
                        #Paso 2.3.2.1.1: Crear conexión con Central
                        central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        central_socket.connect((central_ip, central_port))
                        # Handshake protocolo y consulta con frames
                        if not handshake_client(central_socket, silent=True):
                            raise Exception("handshake fallido")
                        send_frame(central_socket, f"CHECK_SESSION#{cp_id}", silent=True)
                        _ = central_socket.recv(1)  # ACK de Central
                        response, ok = receive_frame(central_socket, timeout=2.0, silent=True)
                        try:
                            send_ack(central_socket, silent=True)
                        except Exception:
                            pass
                        central_socket.close()
                        response = response if ok and response else "NO_SESSION"
                        #Paso 2.3.2.1.5: Enviar respuesta al Engine
                        client_socket.sendall(response.encode('utf-8'))
                    except Exception as e:
                        print(f"[Monitor] Error verificando sesión activa: {e}")
                        #Paso 2.3.2.1.6: Enviar respuesta de error al Engine
                        client_socket.sendall(b"NO_SESSION")
                        
                #Paso 2.3.3: Comando desconocido
                else:
                    #Paso 2.3.3.1: Enviar respuesta de comando desconocido
                    client_socket.sendall(b"UNKNOWN_COMMAND")
                    
                #Paso 2.4: Cerrar conexión con Engine
                client_socket.close()
            except Exception as e:
                if MONITOR_VERBOSE:
                    print(f"[Monitor] Error en comunicación con Engine: {e}")
                
    except Exception as e:
        if MONITOR_VERBOSE:
            print(f"[Monitor] Error iniciando servidor de comunicación: {e}")
    finally:
        #Paso 3: Limpiar recursos
        server_socket.close()



#HILO 4: Funciones de Conexion y Reporte a la Central
def start_central_connection(central_host, central_port, cp_id, location, engine_host, engine_port):
    """Maneja la conexion principal con la Central (registro y gestion de comandos).
    Si la conexión se pierde (por fallo de red o porque el monitor lo marca como None),
    se intenta reconectar automáticamente tras unos segundos."""
    
    #Paso 1: Inicializar variables de control
    #Se crea una lista mutable que guarda el socket con la Central.
    #Asi puede ser compartido con el hilo del vigilante(el que comprueba la vida del engine).
    #Usar lista permite que ambos hilos trabajen sobre la misma referencia al socket.
    central_socket_ref = [None] 
    parado = [False]  # bandera para indicar que el CP ha sido parado
    
    #Paso 2: Bucle infinito de reconexion
    while True:
        try:
            #Paso 2.1: Crear socket y conectar con la Central
            # socket() - "Creo un socket" → Intentar conexión con la Central
            central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if MONITOR_VERBOSE:
                print(f"Intentando conectar CP '{cp_id}' a Central {central_host}:{central_port}...")
            #Paso 2.1.1: connect() - "Me conecto a 127.0.0.1:8000", es decir, a la Central
            central_socket.connect((central_host, central_port))
            
            #Paso 2.1.2: Realizar handshake inicial (ENQ/ACK)
            if MONITOR_VERBOSE:
                print("[Monitor] Realizando handshake con Central...")
            push_protocol_message("Conectado a Central. Realizando handshake...")
            if not handshake_client(central_socket):
                if MONITOR_VERBOSE:
                    print("[Monitor] ERROR: Handshake fallido con Central. Cerrando conexión.")
                push_protocol_message("ERROR: Handshake fallido con Central")
                central_socket.close()
                with monitor_ui_lock:
                    monitor_state['central_status'] = "Desconectado"
                time.sleep(5)  # Esperar antes de reintentar
                continue
            
            push_protocol_message("Handshake exitoso con Central")
            with monitor_ui_lock:
                monitor_state['central_status'] = "Conectado"
                monitor_state['connection_info']['central'] = f"{central_host}:{central_port}"
            
            #Paso 2.1.3: Guardar referencia a la conexión para compartir con otros hilos
            # Guardamos la referencia a la conexion en la lista para que el otro hilo (monitor vigilante) la pueda usar.
            central_socket_ref[0] = central_socket 
            if MONITOR_VERBOSE:
                print("¡Conectado al servidor central! Enviando registro.")
            

            #Paso 2.2: Enviar mensaje de registro a la Central usando protocolo
            # sendall() - "Envío un mensaje de registro al servidor Central"
            default_price = 0.25  # €/kWh por defecto
            register_message = f"REGISTER#{cp_id}#{location}#{default_price}"
            push_protocol_message(f"Enviando REGISTER#{cp_id} a Central")
            with central_socket_lock:
                if send_frame(central_socket, register_message):
                    if MONITOR_VERBOSE:
                        print(f"[Monitor] Enviado REGISTER (with price={default_price}): {register_message}")
                    # Esperar ACK de confirmación de Central y consumirlo
                    try:
                        central_socket.settimeout(1.0)
                        ack_response = central_socket.recv(1)
                    except Exception:
                        ack_response = None
                    finally:
                        central_socket.settimeout(5)
                    if ack_response == ACK:
                        if MONITOR_VERBOSE:
                            print("[Monitor] Central confirmó recepción de REGISTER")
                        push_protocol_message("Central confirmó recepción de REGISTER")
                        # Intentar recibir un frame informativo REGISTER_RESULT#FIRST|RECONNECT (opcional)
                        try:
                            received_mode = None
                            for _ in range(2):  # dos intentos por si llega un poco más tarde
                                resp_string, is_valid = receive_frame(central_socket, timeout=2.0, silent=True)
                                if not is_valid or not resp_string:
                                    continue
                                if resp_string.startswith("REGISTER_RESULT"):
                                    received_mode = resp_string.split('#')[1] if '#' in resp_string else ""
                                    # Enviar ACK de confirmación del frame informativo
                                    try:
                                        send_ack(central_socket, silent=True)
                                    except Exception:
                                        pass
                                    break
                            if received_mode == 'FIRST':
                                push_protocol_message("Registro inicial confirmado por Central")
                            elif received_mode == 'RECONNECT':
                                push_protocol_message("Reconexión confirmada por Central")
                        except Exception:
                            pass
                    else:
                        if MONITOR_VERBOSE:
                            print("[Monitor] WARNING: Central no confirmó recepción de REGISTER")
            #Paso 2.2.1: Configurar timeout para recibir datos
            # Aseguramos modo blocking normal (sin timeout de conexión)
            # IMPORTANTE: Timeout de 5 segundos permite recibir comandos de la Central sin bloquear indefinidamente
            central_socket.settimeout(5) # Timeout para recv (espera max 5s por datos)
            #Paso 2.2.2: Esperar para que Central procese el registro
            time.sleep(3) # Esperar un momento para que CENTRAL procese el registro antes de iniciar monitoreo
            


            #Paso 2.3: Iniciar el hilo de Monitorizacion Local si aun no esta activo
            #Si no existe ya un hilo llamado EngineMonitor, lo creamos.
                                                      #devuelve una lista de todos los hilos activos en este momento  
            if not any(t.name == "EngineMonitor" for t in threading.enumerate()):
                #Paso 2.3.1: Verificar que el Engine esté listo antes de iniciar monitoreo
                try:
                    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    test_socket.settimeout(2)
                    test_socket.connect((engine_host, engine_port))
                    test_socket.close()
                    if MONITOR_VERBOSE:
                        print("[Monitor] Engine verificado como listo. Iniciando monitoreo.")
                except Exception as e:
                    if MONITOR_VERBOSE:
                        print(f"[Monitor] Engine no está listo aún: {e}. Esperando...")
                    time.sleep(5)  # Esperar más tiempo si el Engine no está listo
                
                #Paso 2.3.2: Crear hilo de monitorización del Engine
                #Creamos un nuevo hilo que corre monitor_engine_health(...).
                monitor_thread = threading.Thread(
                    target=monitor_engine_health, 
                    args=(engine_host, engine_port, cp_id, central_socket_ref),
                    name="EngineMonitor"
                )
                #Paso 2.3.2.1: Marcar hilo como daemon
                #Marcamos el hilo como daemon: se cierra cuando el programa principal termina.
                monitor_thread.daemon = True
                #Paso 2.3.2.2: Iniciar hilo de monitorización
                monitor_thread.start()
                if MONITOR_VERBOSE:
                    print("[Monitor] Hilo de monitorización del Engine iniciado.")
                
                #Paso 2.3.3: Crear hilo de comunicación con Engine
                # Iniciar servidor de comunicación con Engine
                comm_thread = threading.Thread(
                    target=handle_engine_communication,
                    args=(engine_host, engine_port, cp_id, central_host, central_port),
                    name="EngineCommunication"
                )
                #Paso 2.3.3.1: Marcar hilo como daemon
                comm_thread.daemon = True
                #Paso 2.3.3.2: Iniciar hilo de comunicación
                comm_thread.start()
                if MONITOR_VERBOSE:
                    print("[Monitor] Servidor de comunicación con Engine iniciado.")
                

            #Paso 2.4: Bucle de escucha de comandos de la Central (Parar/Reanudar)
            empty_reads = 0
            while True:
                #Paso 2.4.1: Verificar si la conexión sigue activa
                # Si el monitor ha puesto central_socket_ref[0] = None → reconexión necesaria
                if central_socket_ref[0] is None:
                    if MONITOR_VERBOSE:
                        print("[Monitor] La conexión con la Central fue marcada como caída. Reconectando...")
                    break  # Salimos al while True externo para reintentar conexión
                
            #Paso 2.4.2: Esperar datos de la Central usando protocolo
                try:
                    #Paso 2.4.2.1: La central puede enviar comandos en cualquier momento, hay que estar escuchando
                    # Recibir trama usando protocolo
                    # IMPORTANTE: usamos timeout explícito para no bloquear indefinidamente y evitar
                    # falsos cierres por lecturas vacías intermitentes. Si no llega nada, seguimos.
                    # Si la Central envía un comando PARAR/REANUDAR, lo recibiremos aquí
                    data_string, is_valid = receive_frame(central_socket, timeout=5)

                    #Paso 2.4.2.2: Gestionar timeouts explícitos sin afectar la conexión
                    if data_string == "__TIMEOUT__":
                        empty_reads = 0
                        continue

                    #Paso 2.4.2.3: Si no se recibieron datos, continuar esperando (posible cierre remoto transitorio)
                    if not data_string:
                        # No asumimos cierre por una lectura vacía/timeout aislado; continuamos
                        empty_reads = 0
                        continue
                    
                    #Paso 2.4.2.4: Verificar validez de la trama
                    if not is_valid:
                        if MONITOR_VERBOSE:
                            print("[Monitor] Trama inválida recibida de Central. Ignorando y esperando siguiente trama...")
                        continue  # Evitar enviar NACK crudo para no cerrar la conexión
                    
                    #Paso 2.4.2.5: Enviar ACK confirmando recepción válida
                    send_ack(central_socket)
                    empty_reads = 0
                    
                    #Paso 2.4.3: Procesar el comando recibido (ya viene parseado como string)
                    #El split('#')[0] separa el codigo del comando: PARAR#CP01 → "PARAR"     REANUDAR#CP01 → "REANUDAR" 
                    command = data_string.strip().split('#')[0]
                    if MONITOR_VERBOSE:
                        print(f"[Monitor] Comando recibido desde Central: '{command}'")
                    # Registrar comando en mensajes del protocolo
                    push_protocol_message(f"← Recibido desde Central: {data_string}")

                    #Paso 2.4.4: Verificar estado del CP
                    # Si el CP está parado, solo permitimos REANUDAR
                    if parado[0] and command != 'REANUDAR':
                        if MONITOR_VERBOSE:
                            print(f"[Monitor] Ignorando comando '{command}' mientras CP está parado.")
                        continue

                    #Paso 2.4.5: Procesar comandos específicos
                    #Paso 2.4.5.1: Comando PARAR
                    if command == 'PARAR':
                        if MONITOR_VERBOSE:
                            print("[Monitor] COMANDO CENTRAL: 'PARAR' recibido → enviando a Engine...")
                        push_protocol_message("→ Enviando PARAR a Engine")
                        #Paso 2.4.5.1.1: Conectar al Engine para ejecutar el comando
                        send_command_to_engine(engine_host, engine_port, "PARAR", central_socket)
                        #Paso 2.4.5.1.2: Después de PARAR, marcamos que no se debe reconectar
                        parado[0] = True
                        if MONITOR_VERBOSE:
                            print("[Monitor] CP marcado como parado. Esperando REANUDAR...")
                    
                    #Paso 2.4.5.2: Comando REANUDAR
                    elif command == 'REANUDAR':
                        if MONITOR_VERBOSE:
                            print("[Monitor] COMANDO CENTRAL: 'REANUDAR' recibido → enviando a Engine...")
                        push_protocol_message("→ Enviando REANUDAR a Engine")
                        #Paso 2.4.5.2.1: Conectar al Engine para ejecutar el comando
                        send_command_to_engine(engine_host, engine_port, "REANUDAR", central_socket)
                        #Paso 2.4.5.2.2: Marcar CP como reanudado
                        parado[0] = False
                        if MONITOR_VERBOSE:
                            print("[Monitor] CP reanudado. Reanudando operaciones.")
                        #Paso 2.4.5.2.3: Si el Engine está en KO, intentar RECOVER automáticamente
                        try:
                            with monitor_ui_lock:
                                current_engine_status = monitor_state.get('engine_status')
                            if current_engine_status and current_engine_status != 'OK':
                                push_protocol_message("Engine en KO → intentando RECOVER")
                                send_command_to_engine(engine_host, engine_port, "RECOVER", central_socket)
                        except Exception:
                            pass

                    #Paso 2.4.5.3: Comando AUTORIZAR_SUMINISTRO
                    elif command == 'AUTORIZAR_SUMINISTRO':
                        if MONITOR_VERBOSE:
                            print(f"[Monitor] COMANDO CENTRAL: 'AUTORIZAR_SUMINISTRO' recibido → enviando a Engine...")
                        #Paso 2.4.5.3.1: Conectar al Engine para ejecutar el comando (usar data_string en lugar de data)
                        send_command_to_engine(engine_host, engine_port, data_string, central_socket)
                        if MONITOR_VERBOSE:
                            print("[Monitor] Comando de autorización de suministro enviado al Engine.")

                    #Paso 2.4.5.4: Comando desconocido
                    else:
                        print(f"Comando desconocido recibido: {command}")
                    
                except socket.timeout:
                    continue
                except socket.error as e:
                    if MONITOR_VERBOSE:
                        print(f"[Monitor] Error de comunicación con la Central: {e}")
                    break  # Reconexión

        #Paso 2.5: Manejar errores de conexión
        #Si falla la conexion, esperamos 10 segundos y lo intentamos de nuevo.        
        except (ConnectionRefusedError, socket.error, ConnectionResetError) as e:
            if MONITOR_VERBOSE:
                print(f"[Monitor] Error de conexion con Central: {e}. \nReintentando en 10s...")
            time.sleep(10)
        except Exception as e:
            if MONITOR_VERBOSE:
                print(f"Error inesperado en el socket de la Central: {e}")
            break
        #Paso 2.6: Limpiar recursos
        #Asegura cerrar el socket si algo sale mal
        finally:
            if central_socket_ref[0]:
                try:
                    central_socket_ref[0].close()
                except Exception:
                        pass
                central_socket_ref[0] = None # Limpiamos la referencia antes de reintentar
            time.sleep(5)  # Esperar un poco antes de reintentar

# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    #Paso 1: Verificar argumentos de línea de comandos
    if len(sys.argv) != 6:
        print("Uso: py EV_CP_M.py <IP_CENTRAL> <PUERTO_CENTRAL> <IP_ENGINE> <PUERTO_ENGINE> <ID_CP>")
        print("Ejemplo: py EV_CP_M.py localhost 8000 localhost 8001 MAD-01")
        sys.exit(1)

    #Paso 2: Extraer argumentos de línea de comandos
    #Extraemos los argumentos
    CENTRAL_IP = sys.argv[1]
    ENGINE_IP = sys.argv[3]
    CP_ID = sys.argv[5]
    
    #Paso 2.1: Convertir puertos desde texto a número entero
    #Convertimos los puertos desde texto a número entero.
    try:
        CENTRAL_PORT = int(sys.argv[2])
        ENGINE_PORT = int(sys.argv[4])
    #Paso 2.2: Manejar errores de conversión
    #Si alguien escribe letras → lanza error y sale
    except ValueError:
        print("Error: Los puertos deben ser numeros enteros.")
        sys.exit(1)

    #Paso 3: Configurar ubicación del CP
    locations = {"MAD-01": "C/ Serrano 10", "VAL-03": "Plaza del Ayuntamiento 1", "BCN-05": "Las Ramblas 55"}
    LOCATION = locations.get(CP_ID, "Ubicacion Desconocida")

    #Paso 4: Iniciar conexión con la Central en hilo separado
    central_thread = threading.Thread(target=start_central_connection, args=(CENTRAL_IP, CENTRAL_PORT, CP_ID, LOCATION, ENGINE_IP, ENGINE_PORT), daemon=True)
    central_thread.start()
    
    #Paso 5: Iniciar monitorización del Engine en hilo separado
    engine_thread = threading.Thread(target=monitor_engine_health, args=(ENGINE_IP, ENGINE_PORT, CP_ID, [None]), daemon=True)
    engine_thread.start()
    
    #Paso 6: Hilo para actualizar panel visual periódicamente
    def update_panel_periodically():
        """Actualiza el panel visual cada PANEL_UPDATE_INTERVAL segundos."""
        while True:
            try:
                display_monitor_panel(CP_ID)
                time.sleep(PANEL_UPDATE_INTERVAL)
            except Exception as e:
                if MONITOR_VERBOSE:
                    print(f"Error actualizando panel: {e}")
                time.sleep(PANEL_UPDATE_INTERVAL)
    
    panel_thread = threading.Thread(target=update_panel_periodically, daemon=True)
    panel_thread.start()
    
    if MONITOR_VERBOSE:
        print(f"\n[Monitor] Monitor del CP {CP_ID} iniciado correctamente.")
        print(f"[Monitor] Conectando con Central en {CENTRAL_IP}:{CENTRAL_PORT}...")
        print(f"[Monitor] Monitorizando Engine en {ENGINE_IP}:{ENGINE_PORT}...")
        print("[Monitor] El sistema está en ejecución. Presiona Ctrl+C para detener.")
    
    #Paso 7: Mantener el programa en ejecución indefinidamente
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        if MONITOR_VERBOSE:
            print("\n[Monitor] Deteniendo Monitor...")
        sys.exit(0)