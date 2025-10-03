# Fichero: ev_central.py
import socket
import threading
import sys
import time
import os
from kafka import KafkaConsumer, KafkaProducer
import json
import database 

# --- Configuración global (ajusta los topics) ---
KAFKA_TOPIC_REQUESTS = 'driver_requests' # Conductores -> Central
KAFKA_TOPIC_STATUS = 'cp_telemetry'      # CP -> Central (Telemetría/Averías/Consumo)
KAFKA_TOPIC_CENTRAL_ACTIONS = 'central_actions' # Central -> CP (Parar/Reanudar)
KAFKA_TOPIC_DRIVER_NOTIFY = 'driver_notifications' # Central -> Drivers

# Diccionario para almacenar la referencia a los sockets de los CPs activos
# Esto es para que la Central pueda enviar comandos síncronos si es necesario.
active_cp_sockets = {} 

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
        "FUERA_DE_SERVICIO": "\033[93m" # Naranja
    }
    END_COLOR = "\033[0m"
    return f"{colors.get(status, '')}{status}{END_COLOR}"

def display_panel(central_messages, driver_requests):
    """Muestra el estado de todos los CPs y mensajes en un panel."""
    while True:
        clear_screen()
        print("--- PANEL DE MONITORIZACIÓN DE EV CHARGING ---")
        print("="*50)
        
        # --- Sección de Puntos de Recarga (CPs) ---
        all_cps = database.get_all_cps()
        if not all_cps:
            print("No hay Puntos de Recarga registrados.")
        else:
            print(f"{'ID':<10} | {'UBICACIÓN':<25} | {'ESTADO':<20}")
            print("-"*50)
            for cp in all_cps:
                colored_status = get_status_color(cp['status'])
                print(f"{cp['id']:<10} | {cp['location']:<25} | {colored_status}")

        print("="*50)

        # --- Sección Peticiones de Conductores (Kafka) ---
        print("\n*** PETICIONES DE CONDUCTORES EN CURSO (Kafka) ***")
        if driver_requests:
            for req in driver_requests:
                print(f"[{req['timestamp']}] Driver {req['user_id']} solicita recarga en CP {req['cp_id']}")
        else:
            print("No hay peticiones pendientes.")
        
        # --- Sección de Mensajes de Aplicación (Kafka/General) ---
        print("\n*** MENSAJES DEL SISTEMA ***")
        if central_messages:
            for msg in central_messages:
                print(msg)
        
        print("="*50)
        print("Comandos: [P]arar <CP_ID> | [R]eanudar <CP_ID> | [Q]uit")
        print(f"Última actualización: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(2) # El panel se refresca cada 2 segundos

# --- Funciones de Kafka ---

# jk --> escucha 2 topics (de petición de conductores(request), de los puntos de cargas(status))
def process_kafka_requests(kafka_broker, central_messages, driver_requests):
    """
    Consumidor Kafka: Recibe peticiones de suministro de los Drivers 
    y mensajes de estado de los CPs.
    """
    try:
        consumer = KafkaConsumer(
            KAFKA_TOPIC_REQUESTS,
            KAFKA_TOPIC_STATUS,
            bootstrap_servers=[kafka_broker],
            auto_offset_reset='latest', # Solo lee mensajes nuevos
            group_id='central-processor',
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )
        central_messages.append(f"Kafka Consumer: Conectado al broker {kafka_broker}")
    except Exception as e:
        central_messages.append(f"ERROR: No se pudo conectar a Kafka ({kafka_broker}): {e}")
        return 

    for message in consumer:
        try:
            payload = message.value
            topic = message.topic
            
            if topic == KAFKA_TOPIC_REQUESTS:
                # Un conductor solicita servicio
                driver_requests.append({'cp_id': payload['cp_id'], 'user_id': payload['user_id'], 'timestamp': time.strftime('%H:%M:%S')})
                
                # --- Lógica de Gobierno ---
                # 1. Comprobar disponibilidad del CP
                cp_status = database.get_cp_status(payload['cp_id'])
                
                if cp_status == 'ACTIVADO':
                    # 2. Si está disponible, solicitar autorización al CP (via socket, ya que es bidireccional y síncrono)
                    # Aquí se usaría active_cp_sockets para enviar la solicitud de autorización
                    # Por simplicidad, solo notificamos
                    central_messages.append(f"Petición OK. Autorizando a Driver {payload['user_id']} en CP {payload['cp_id']}.")
                    # TO-DO: Implementar la lógica de sockets para la autorización al Engine
                    # TO-DO: Usar KafkaProducer para notificar al conductor (KAFKA_TOPIC_DRIVER_NOTIFY)
                else:
                    central_messages.append(f"Petición RECHAZADA. CP {payload['cp_id']} no disponible ({cp_status}).")
                    # TO-DO: Usar KafkaProducer para notificar la denegación al conductor
                
                # Limpiamos la petición después de procesar (en un sistema real se procesaría de forma más robusta)
                if driver_requests and driver_requests[0]['cp_id'] == payload['cp_id']:
                    driver_requests.pop(0)

            elif topic == KAFKA_TOPIC_STATUS:
                # Telemetría o estado del CP (por ejemplo, CONSUMO)
                
                # Lógica existente para CONSUMO
                if payload.get('type') == 'CONSUMO':
                    # 1. ACTUALIZAR CONSUMO (como ya lo tenías)
                    database.update_cp_consumption(payload['cp_id'], payload['kwh'], payload['importe'], payload['user_id'])
                    
                    # 2. AÑADIR CAMBIO DE ESTADO A SUMINISTRANDO AQUÍ
                    #    La Central debe actualizar la BD a SUMINISTRANDO.
                    if database.get_cp_status(payload['cp_id']) != 'SUMINISTRANDO':
                        database.update_cp_status(payload['cp_id'], 'SUMINISTRANDO')
                        central_messages.append(f"INFO: CP {payload['cp_id']} ha comenzado el suministro (Driver {payload['user_id']}).")
                # NUEVA LÓGICA: FIN DE SUMINISTRO
                if payload.get('type') == 'SUPPLY_END': 
                    # El CP ha notificado el fin de la recarga
                    database.update_cp_status(payload['cp_id'], 'ACTIVADO') # <--- CAMBIA A VERDE
                    central_messages.append(f"INFO: Suministro finalizado en CP {payload['cp_id']} por Driver {payload['user_id']}.")
                    # TO-DO: Enviar "ticket" final al conductor.

                # Lógica existente para AVERIADO
                if payload.get('type') == 'AVERIADO' or payload.get('type') == 'CONEXION_PERDIDA':
                    database.update_cp_status(payload['cp_id'], 'AVERIADO')
                    central_messages.append(f"ALARMA: CP {payload['cp_id']} ha reportado una AVERÍA crítica.")

        except Exception as e:
            central_messages.append(f"Error al procesar mensaje de Kafka: {e}")

# --- Funciones del Servidor de Sockets ---
# jk -->
def process_socket_data(data, cp_id, address, client_socket, central_messages):
    """Procesa mensajes recibidos del CP (aparte del registro inicial)."""
    message = data.decode('utf-8').strip().upper() # <-- Aseguramos mayúsculas
    central_messages.append(f"CP {cp_id} -> CENTRAL: {message}")
    
    parts = message.split('#')
    command = parts[0]
    
    if 'FAULT' in message:
        # Reporte de avería por el Monitor
        database.update_cp_status(cp_id, 'AVERIADO')
        # Este mensaje ya aparece en tu log, lo que confirma que se ejecuta:
        central_messages.append(f"ALARMA: CP {cp_id} reporta avería (Monitor). Estado actualizado a ROJO.") 

    elif 'RESUMED' in message:
        # Reporte de recuperación de avería
        database.update_cp_status(cp_id, 'ACTIVADO')
        central_messages.append(f"INFO: CP {cp_id} reanuda servicio. Estado actualizado a VERDE.")
    # TO-DO: Añadir más comandos como 'SUPPLY_END' (fin de suministro) o 'AUTH_ACK' (confirmación de autorización)

def handle_client(client_socket, address, central_messages):
    """Maneja la conexión de un único CP."""
    cp_id = None
    try:
        # 1. Registro (Mensaje síncrono inicial)
        message = client_socket.recv(1024).decode('utf-8')
        parts = message.strip().split('#')

        if len(parts) == 3 and parts[0] == 'REGISTER':
            cp_id = parts[1]
            location = parts[2]
            
            # Registrar en la BD y actualizar estado a ACTIVADO
            database.register_cp(cp_id, location)
            database.update_cp_status(cp_id, 'ACTIVADO')
            central_messages.append(f"CP '{cp_id}' registrado/actualizado desde {address}. Estado: ACTIVADO")
            
            # Guardamos la referencia del socket para envíos síncronos (autorización/órdenes)
            active_cp_sockets[cp_id] = client_socket 
            
            # 2. Bucle de escucha para mensajes de control/telemetría síncrona
            while True:
                data = client_socket.recv(1024)
                if not data:
                    break # Conexión cerrada por el cliente
                
                # Procesar mensajes de control/averías
                process_socket_data(data, cp_id, address, client_socket, central_messages)
                
        else:
            central_messages.append(f"ERROR: Mensaje de registro inválido de {address}. Cerrando conexión.")
            
    except Exception as e:
        central_messages.append(f"Error con el CP {cp_id} ({address}): {e}")
    finally:
        # 3. Desconexión
        if cp_id:
            central_messages.append(f"Conexión con CP '{cp_id}' perdida.")
            database.update_cp_status(cp_id, 'DESCONECTADO')
            if cp_id in active_cp_sockets:
                 del active_cp_sockets[cp_id] # Eliminamos la referencia
        client_socket.close()

def start_socket_server(host, port, central_messages):
    """Inicia el servidor de sockets para escuchar a los CPs."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((host, port))
    server_socket.listen(5)
    central_messages.append(f"EV_Central escuchando sockets en {host}:{port}")

    while True:
        client_socket, address = server_socket.accept()
        client_thread = threading.Thread(target=handle_client, args=(client_socket, address, central_messages))
        client_thread.daemon = True
        client_thread.start()

# --- Funciones de Comandos de CENTRAL (Punto 13) ---
def send_cp_command(cp_id, command, central_messages):
    """Envía un comando (Parar/Reanudar) a un CP específico a través del socket síncrono."""
    if cp_id not in active_cp_sockets:
        central_messages.append(f"ERROR: CP {cp_id} no está conectado por socket para recibir comandos.")
        return
    
    try:
        socket_ref = active_cp_sockets[cp_id]
        
        # Formato de mensaje recomendado: <STX><REQUEST><ETX><LRC> (no implementado aquí)
        # Usamos un formato simple: "COMMAND#PARAMETRO"
        message = f"{command.upper()}#CENTRAL".encode('utf-8')
        socket_ref.sendall(message)
        central_messages.append(f"Comando '{command}' enviado a CP {cp_id}.")
        
        # Actualizar estado de forma optimista (el CP confirmará después)
        if command == 'PARAR':
             database.update_cp_status(cp_id, 'FUERA_DE_SERVICIO') # Naranja
        elif command == 'REANUDAR':
             database.update_cp_status(cp_id, 'ACTIVADO') # Verde
             
    except Exception as e:
        central_messages.append(f"ERROR al enviar comando a CP {cp_id}: {e}")
        database.update_cp_status(cp_id, 'DESCONECTADO')
        del active_cp_sockets[cp_id]
        

def process_user_input(central_messages):
    """Maneja los comandos de la interfaz de CENTRAL (punto 13 de la mecánica)."""
    while True:
        try:
            # Esperamos el input del usuario en la terminal
            command_line = input("> ").strip().upper()
            
            if command_line == 'QUIT' or command_line == 'Q':
                raise KeyboardInterrupt
            
            parts = command_line.split()
            command = parts[0]
            
            if command == 'P' or command == 'PARAR':
                if len(parts) == 2:
                    cp_id = parts[1]
                    central_messages.append(f"Iniciando comando PARAR para {cp_id}...")
                    send_cp_command(cp_id, 'PARAR', central_messages)
                else:
                    central_messages.append("Uso: P <CP_ID> o PARAR <CP_ID>")
            
            elif command == 'R' or command == 'REANUDAR':
                if len(parts) == 2:
                    cp_id = parts[1]
                    central_messages.append(f"Iniciando comando REANUDAR para {cp_id}...")
                    send_cp_command(cp_id, 'REANUDAR', central_messages)
                else:
                    central_messages.append("Uso: R <CP_ID> o REANUDAR <CP_ID>")
            
            else:
                central_messages.append(f"Comando desconocido: {command}")
                
        except EOFError:
            # Manejar el fin de archivo o Ctrl+D/Z
            time.sleep(0.1) 
        except Exception as e:
            central_messages.append(f"Error en el procesamiento de entrada: {e}")


# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python ev_central.py <puerto_socket> <kafka_broker_ip:port>")
        sys.exit(1)

    try:
        SOCKET_PORT = int(sys.argv[1])
        KAFKA_BROKER = sys.argv[2]
        HOST = '0.0.0.0'
        
        # Usaremos listas compartidas para que los hilos se comuniquen con el panel
        central_messages = ["CENTRAL system status OK"]
        driver_requests = []

        # 1. Configurar la base de datos
        database.setup_database()

        # 2. Iniciar el servidor de Sockets para CPs (registro y control síncrono)
        server_thread = threading.Thread(target=start_socket_server, args=(HOST, SOCKET_PORT, central_messages))
        server_thread.daemon = True
        server_thread.start()
        
        # 3. Iniciar el consumidor Kafka (peticiones de driver y telemetría asíncrona)
        kafka_thread = threading.Thread(target=process_kafka_requests, args=(KAFKA_BROKER, central_messages, driver_requests))
        kafka_thread.daemon = True
        kafka_thread.start()
        
        # 4. Iniciar el hilo de entrada de comandos del usuario
        input_thread = threading.Thread(target=process_user_input, args=(central_messages,))
        input_thread.daemon = True
        input_thread.start()

        # 5. Iniciar el panel de monitorización en el hilo principal
        display_panel(central_messages, driver_requests)

    except ValueError:
        print("Error: El puerto debe ser un número entero.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nServidor detenido por el usuario. Cerrando hilos...")
        # Nota: La terminación del programa principal terminará los hilos daemon.
        sys.exit(0)