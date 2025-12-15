import socket # para tener una comunicacion por red
import sys # para usar parametros desde consola
import time # para manejar el tiempo
import threading # para poder ejecutar en paralelo
import database
from datetime import datetime
import os
import requests # Para hacer peticiones HTTPS
import urllib3 # Para silenciar las alertas de certificado autofirmado
# Desactivar advertencias de certificado inseguro (porque usamos adhoc)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
# Seva: funcion para enviar logs a la Central para el Fronted
def enviar_log_monitor(central_ip, msg):
    try:
        url = f"http://{central_ip}:5000/api/log"
        requests.post(url, json={"source": "MONITOR", "msg": msg}, timeout=1)
    except:
        pass


# --- Función de Registro HTTPS (Release 2) ---
def register_in_registry_https(cp_id, location):
    """
    Se conecta al EV_Registry vía HTTPS para obtener el token de seguridad y la clave simétrica.
    Retorna (token, symmetric_key) o (None, None) si falla.
    """
    # URL del Registry (Asumimos que está en el puerto 6000 de la IP local o configurada)
    # En un despliegue real, esta IP debería ser un argumento o config.
    registry_url = "https://127.0.0.1:6000/register"
    
    payload = {
        "id": cp_id,
        "location": location
    }
    
    print(f"\n[Monitor] Iniciando registro seguro en {registry_url}...")
    
    try:
        # Enviamos POST seguro. verify=False es necesario para certificados autofirmados
        response = requests.post(registry_url, json=payload, verify=False, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            token = data.get('token')
            symmetric_key = data.get('symmetric_key') # Seva: Obtener la clave simétrica del Registry
            print(f"[Monitor] ¡REGISTRO EXITOSO! Token recibido: {token}")
            return token, symmetric_key
        else:
            print(f"[Monitor] Error en registro: {response.status_code} - {response.text}")
            return None, None
            
    except requests.exceptions.ConnectionError:
        print(f"[Monitor] ERROR: No se puede conectar al Registry en {registry_url}.")
        print("[Monitor] Asegúrate de que EV_Registry.py esté ejecutándose.")
        return None, None
    except Exception as e:
        print(f"[Monitor] Error inesperado en registro: {e}")
        return None, None


# Seva: --- Función de Baja HTTPS (Release 2) ---
def unregister_from_registry_https(cp_id, token):
    """Solicita la baja del CP al Registry."""

    registry_url = "https://127.0.0.1:6000/unregister"
    
    payload = {
        "id": cp_id,
        "token": token
    }
    
    print(f"\n[Monitor] Solicitando BAJA en {registry_url}...")
    
    try:
        response = requests.post(registry_url, json=payload, verify=False, timeout=5)
        
        if response.status_code == 200:
            print(f"[Monitor] BAJA EXITOSA: {response.json().get('message')}")
            # Opcional: Borrar claves locales de la BD
            # database.revoke_cp_keys(cp_id) # Si quisieras borrarlo localmente también
            return True
        else:
            print(f"[Monitor] Error en baja: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"[Monitor] Error conectando con Registry para baja: {e}")
        return False

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

# Seva: Bandera para detener la actualización de la interfaz
STOP_UI = False

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
        return "__TIMEOUT__", False
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
                    # --- NUEVO: AVISAR A LA WEB DEL FALLO ---
                    # Necesitamos la IP de la central. Como esta función no la recibe, 
                    # usaremos una variable global o pasaremos el argumento.
                    # TRUCO RÁPIDO: Usa la variable global CENTRAL_IP que se define en el main.
                    try:
                        enviar_log_monitor(CENTRAL_IP, f"[{cp_id}] 🚨 DETECTADO FALLO EN ENGINE (KO). Enviando alerta.")
                    except: pass
                    # ----------------------------------------
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
                    # --- NUEVO: AVISAR A LA WEB DE LA RECUPERACIÓN ---
                    try:
                        enviar_log_monitor(CENTRAL_IP, f"[{cp_id}] ✅ ENGINE RECUPERADO AUTOMÁTICAMENTE.")
                    except: pass
                    # -------------------------------------------------
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


#Seva: Funcion para forzar la parada del engine (revocación durante el suministro)
def trigger_emergency_stop(engine_host, engine_port):
    """Fuerza la parada del Engine local en caso de pérdida de conexión con Central."""
    print(f"[Monitor] EMERGENCIA: Enviando orden de PARADA al Engine...")
    try:
        # Abrimos conexión efímera con Engine
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect((engine_host, engine_port))
        
        # Usamos el protocolo existente para enviar "PARAR"
        if handshake_client(sock, silent=True):
            send_frame(sock, "PARAR") # El Engine ya sabe procesar "PARAR"
            # No esperamos respuesta elaborada, solo aseguramos el envío
        
        sock.close()
        print(f"[Monitor] Orden de parada enviada.")
    except Exception as e:
        print(f"[Monitor] Fallo al intentar parar Engine: {e}")



#HILO 4: Funciones de Conexion y Reporte a la Central
# Seva: Cambaiado para incluir resiliencia y seguridad
def start_central_connection(central_host, central_port, cp_id, location, engine_host, engine_port, initial_token=None, initial_key=None):
    """
    Maneja la conexión principal con la Central.
    LOGICA DE RESILIENCIA Y SEGURIDAD (RELEASE 2):
    - Ciclo de auto-recuperación ante revocación.
    - Notificación de eventos de seguridad al log visual de Central.
    """
    central_socket_ref = [None] 
    parado = [False]
    
    # Bucle infinito de conexión/reconexion
    while True:
        try:
            # Bandera para saber si venimos de una renovación de seguridad
            security_recovery_mode = False

            # PASO 1: AUTORREPARACIÓN DE CREDENCIALES
            if MONITOR_VERBOSE:
                print(f"[Monitor] Iniciando protocolo de conexión segura...")
            
            if initial_token and initial_key:
                new_token, new_key = initial_token, initial_key
                # Las consumimos para que si hay una reconexión futura, sí pida nuevas
                initial_token = None 
                initial_key = None
            else:
                # Si no (o es una reconexión posterior), llamamos al Registry
                new_token, new_key = register_in_registry_https(cp_id, location)
            
            if new_token and new_key:
                # PASO 2: INYECCIÓN DE CLAVE EN ENGINE
                with monitor_ui_lock:
                    old_key = monitor_state.get('symmetric_key')
                    
                    # DETECCIÓN DE CAMBIO DE CLAVES (AUDITORÍA VISUAL)
                    if old_key != new_key:
                        security_recovery_mode = True
                        print(f"[Monitor] ⚠️ Nuevas credenciales. Sincronizando Engine...")
                        enviar_log_monitor(central_host, f"[{cp_id}] Nuevas claves recibidas. Actualizando Engine.")

                        try:
                            # Conexión efímera al Engine para inyectar la clave
                            sock_eng = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock_eng.settimeout(2)
                            sock_eng.connect((engine_host, engine_port))
                            
                            if handshake_client(sock_eng, silent=True):
                                if send_frame(sock_eng, f"SET_KEY#{new_key}"):
                                    # Esperar ACK
                                    resp, _ = receive_frame(sock_eng, timeout=2, silent=True)
                                    if resp and "ACK" in resp:
                                        msg_ok = f"[{cp_id}] ✅ Engine resincronizado con nueva clave."
                                        print(f"[Monitor] {msg_ok}")
                                        enviar_log_monitor(central_host, msg_ok)
                            sock_eng.close()
                        except Exception as e:
                            print(f"[Monitor] ❌ Error crítico sincronizando Engine: {e}")
                        
                        # Actualizar memoria local
                        monitor_state['symmetric_key'] = new_key
            else:
                print("[Monitor] Error: Registry no disponible. Reintentando en 5s...")
                time.sleep(5)
                continue 

            # PASO 3: CONEXIÓN CON CENTRAL
            central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if MONITOR_VERBOSE:
                print(f"Conectando a Central {central_host}:{central_port}...")
            
            central_socket.connect((central_host, central_port))
            
            if not handshake_client(central_socket):
                print("[Monitor] Handshake rechazado. Reintentando...")
                central_socket.close()
                time.sleep(5)
                continue
            
            # Registro
            default_price = 0.25
            register_msg = f"REGISTER#{cp_id}#{location}#{default_price}"
            
            if send_frame(central_socket, register_msg):
                try:
                    central_socket.settimeout(2.0)
                    ack = central_socket.recv(1)
                    if ack == ACK:
                        # CONEXIÓN EXITOSA
                        with monitor_ui_lock:
                            monitor_state['central_status'] = "Conectado"
                            monitor_state['connection_info']['central'] = f"{central_host}:{central_port}"
                        
                        central_socket_ref[0] = central_socket
                        print(f"[Monitor] 🟢 CONEXIÓN ESTABLECIDA con Central.")
                        enviar_log_monitor(central_host, f"[{cp_id}] 🟢 Conexión establecida con Central.")
                        
                        # Si venimos de un cambio de claves (Revocación), forzamos ACTIVAR.
                        # Si es un reinicio normal (Mantenimiento), respetamos el estado de la Central.
                        if security_recovery_mode:
                            print("[Monitor] Enviando señal de RECUPERACIÓN DE SEGURIDAD...")
                            # Enviamos RECOVER para sacar al CP del estado FUERA_DE_SERVICIO
                            send_frame(central_socket, f"RECOVER#{cp_id}")
                            security_recovery_mode = False

                        # Iniciar hilos auxiliares si no existen
                        if not any(t.name == "EngineMonitor" for t in threading.enumerate()):
                             monitor_thread = threading.Thread(
                                target=monitor_engine_health, 
                                args=(engine_host, engine_port, cp_id, central_socket_ref),
                                name="EngineMonitor"
                            )
                             monitor_thread.daemon = True
                             monitor_thread.start()
                             
                             comm_thread = threading.Thread(
                                target=handle_engine_communication,
                                args=(engine_host, engine_port, cp_id, central_host, central_port),
                                name="EngineCommunication"
                            )
                             comm_thread.daemon = True
                             comm_thread.start()

                except Exception:
                    pass
            
            central_socket.settimeout(5) 

            # 4. BUCLE DE ESCUCHA (CORREGIDO)
            empty_reads = 0
            while True:
                if central_socket_ref[0] is None: break 
                
                try:
                    # Usamos timeout de 2s para ser ágiles
                    data_str, is_valid = receive_frame(central_socket, timeout=2.0)
                    
                    # CORRECCIÓN PRINCIPAL: Si es timeout, no es error, solo silencio.
                    if data_str == "__TIMEOUT__": 
                        continue 
                    
                    # Si es None o vacío, la Central cerró la conexión (REVOCACIÓN o CAÍDA)
                    if not data_str:
                        empty_reads += 1
                        if empty_reads > 2:
                            msg_loss = f"[{cp_id}] Conexión perdida con Central (Posible Revocación)."
                            print(f"[Monitor] {msg_loss}")
                            enviar_log_monitor(central_host, msg_loss)

                            # Antes de dormir o desconectar, paramos el Engine físicamente
                            trigger_emergency_stop(engine_host, engine_port)
                            
                            print(f"[Monitor] Esperando 4s antes de iniciar recuperación de seguridad...")
                            enviar_log_monitor(central_host, f"[{cp_id}] Iniciando protocolo de auto-recuperación (4s)...")
                            time.sleep(4) 
                           
                            break 
                        continue
                    
                    if is_valid:
                        send_ack(central_socket)
                        empty_reads = 0 # Reseteamos contador de errores
                        
                        # Procesar comandos
                        cmd = data_str.strip().split('#')[0]
                        if cmd == 'PARAR':
                            send_command_to_engine(engine_host, engine_port, "PARAR", central_socket)
                            parado[0] = True
                        elif cmd == 'REANUDAR':
                            send_command_to_engine(engine_host, engine_port, "REANUDAR", central_socket)
                            parado[0] = False
                        elif cmd == 'AUTORIZAR_SUMINISTRO':
                            send_command_to_engine(engine_host, engine_port, data_str, central_socket)

                except socket.error:
                    print("[Monitor] Error de socket. Reiniciando...")
                    trigger_emergency_stop(engine_host, engine_port)
                    break 

        except Exception as e:
            print(f"[Monitor] Fallo en ciclo: {e}. Reintentando en 5s...")
            time.sleep(5)
        
        finally:
            if central_socket_ref[0]:
                try: central_socket_ref[0].close()
                except: pass
                central_socket_ref[0] = None
                with monitor_ui_lock:
                    monitor_state['central_status'] = "Desconectado"
            time.sleep(5)

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
    locations = {"MAD-01": "C/ Serrano 10, Madrid", "VAL-03": "Plaza del Ayuntamiento 1, Valencia", "BCN-02": "Las Ramblas 55, Barcelona", "PAR-01": "Torre Eiffel, Paris"}
    LOCATION = locations.get(CP_ID, "Ubicacion Desconocida")

    # --- NUEVO BLOQUE RELEASE 2: REGISTRO ---
    #Intentamos obtener el token y la clave antes de lanzar los hilos
    TOKEN_SEGURIDAD, CLAVE_SIMETRICA = register_in_registry_https(CP_ID, LOCATION)
    
    if TOKEN_SEGURIDAD and CLAVE_SIMETRICA:
        print("[Monitor] Sistema autenticado. Iniciando conexión con Central...")
        enviar_log_monitor(CENTRAL_IP, f"[{CP_ID}] AUTENTICADO.\n   >> Token recibido: {TOKEN_SEGURIDAD}\n   >> Clave recibida: {CLAVE_SIMETRICA}")
        # Seva: Guardamos la clave simétrica en el estado del monitor para su uso en cifrado
        with monitor_ui_lock:
             monitor_state['symmetric_key'] = CLAVE_SIMETRICA
        
        # --- Seva : ENVIAR CLAVE AL ENGINE LOCAL ---
        try:
            print("[Monitor] Enviando clave simétrica al Engine...")
            # Esperamos un poco para asegurar que el Engine haya arrancado su servidor de sockets
            time.sleep(2) 
            
            # Creamos un socket temporal solo para configurar la clave
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((ENGINE_IP, ENGINE_PORT))
            
            # 1. Handshake inicial
            if handshake_client(sock, silent=True):
                # 2. Enviar la clave usando el protocolo
                # Comando: SET_KEY#<CLAVE>
                if send_frame(sock, f"SET_KEY#{CLAVE_SIMETRICA}"):
                    # 3. Esperar confirmación (ACK#SET_KEY)
                    resp, _ = receive_frame(sock, timeout=2, silent=True)
                    if resp and "ACK" in resp:
                        print("[Monitor] Clave configurada en el Engine correctamente.")
                        enviar_log_monitor(CENTRAL_IP, f"[{CP_ID}] 🔐 Clave inyectada en Engine. Cifrado E2E LISTO.")
                    else:
                        # El 'else' tiene que estar alineado con el 'if', NO pegado a la línea de arriba
                        print(f"[Monitor] Engine no confirmó recepción de clave: {resp}")
            
            sock.close()
            
        except Exception as e:
            err_msg = f"Error enviando clave al Engine: {e}"
            print(f"[Monitor] {err_msg}")
            print("Asegúrate de que el Engine (EV_CP_E.py) esté corriendo antes que el Monitor.")
            enviar_log_monitor(CENTRAL_IP, f"[{CP_ID}] FALLO al inyectar clave en Engine.")
        # --------------------------------------------------
    else:
        print("[Monitor] ADVERTENCIA: No se obtuvo token/clave. El sistema funcionará en modo Release 1 (inseguro).")
        TOKEN_SEGURIDAD = None
        CLAVE_SIMETRICA = None
    # ----------------------------------------

    #Paso 4: Iniciar conexión con la Central en hilo separado
    central_thread = threading.Thread(target=start_central_connection, args=(CENTRAL_IP, CENTRAL_PORT, CP_ID, LOCATION, ENGINE_IP, ENGINE_PORT, TOKEN_SEGURIDAD, CLAVE_SIMETRICA), daemon=True)
    central_thread.start()
    
    #Paso 5: Iniciar monitorización del Engine en hilo separado
    engine_thread = threading.Thread(target=monitor_engine_health, args=(ENGINE_IP, ENGINE_PORT, CP_ID, [None]), daemon=True)
    engine_thread.start()
    
    #Paso 6: Hilo para actualizar panel visual periódicamente
    def update_panel_periodically():
        """Actualiza el panel visual cada PANEL_UPDATE_INTERVAL segundos."""
        while not STOP_UI:
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
        STOP_UI = True
        time.sleep(0.5) # Esperar un momento para que el hilo del panel termine su ciclo actual
        clear_screen()  # Limpiar pantalla para mostrar solo el menú de cierre

        print("\n[Monitor] Deteniendo hilos...")
        
        # Seva: OPCIÓN DE DAR DE BAJA AL SALIR ---
        if 'TOKEN_SEGURIDAD' in locals() and TOKEN_SEGURIDAD:
            print("-" * 50)
            print("      OPCIONES DE CIERRE      ")
            print("-" * 50)
            try:
                resp = input("¿Deseas dar de BAJA este CP del Registry antes de apgar el monitor? (s/N): ").strip().lower()
                if resp == 's':
                    unregister_from_registry_https(CP_ID, TOKEN_SEGURIDAD)
            except EOFError:
                pass
        # ---------------------------------------------
        if MONITOR_VERBOSE:
            print("\n[Monitor] Deteniendo Monitor...")
        sys.exit(0)