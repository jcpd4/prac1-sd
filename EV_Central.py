# Fichero: ev_central.py
import socket
import threading
import sys
import time
import os
from kafka import KafkaConsumer
import json
import database # Módulo de base de datos (se asume implementado)

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
        "FUERA_DE_SERVICIO": "\033[91m" # Rojo
    }
    END_COLOR = "\033[0m"
    return f"{colors.get(status, '')}{status}{END_COLOR}"

def display_panel(central_messages, driver_requests):
    """Muestra el estado de todos los CPs y mensajes en un panel."""
    
    while True:
        #Limpia el terminal
        clear_screen()
        #Imprime la cabecera del panel.
        print("--- PANEL DE MONITORIZACIÓN DE EV CHARGING ---")
        print("="*50)
        
        #1. --- Sección de Puntos de Recarga (CPs) ---
        #Recupera todos los CPs registrados en la base de datos.
        all_cps = database.get_all_cps()
        if not all_cps:
            print("No hay Puntos de Recarga registrados.")
        else:
            print(f"{'ID':<10} | {'UBICACIÓN':<25} | {'ESTADO':<20}")
            print("-"*50)
            for cp in all_cps:   #para imprimir el estado con color ANSI
                colored_status = get_status_color(cp['status'])
                print(f"{cp['id']:<10} | {cp['location']:<25} | {colored_status}")

        print("="*50)

        #2. --- Sección Peticiones de Conductores (Kafka) ---
        print("\n*** PETICIONES DE CONDUCTORES EN CURSO (Kafka) ***")
        #Lista todas las solicitudes Kafka del topic driver_requests
        if driver_requests:
            for req in driver_requests:
                print(f"[{req['timestamp']}] Driver {req['user_id']} solicita recarga en CP {req['cp_id']}")
        else:
            print("No hay peticiones pendientes.")
        
        #3. --- Sección de Mensajes de Aplicación (Kafka/General) ---
        print("\n*** MENSAJES DEL SISTEMA ***")
        if central_messages:
            for msg in central_messages:
                print(msg)
        
        print("="*50)
        #Instrucciones para que el operador de la Central escriba comandos para controlar los CPs.
        print("Comandos: [P]arar <CP_ID> | [R]eanudar <CP_ID> | [Q]uit")
        print(f"Última actualización: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(2) # El panel se refresca cada 2 segundos

# --- Funciones de Kafka ---
def process_kafka_requests(kafka_broker: str, central_messages: List[str], driver_requests: List[Dict[str, Any]]):
    """
    Consumidor Kafka: Recibe peticiones de suministro de los Drivers y mensajes de estado de los CPs.
    """
    global KAFKA_PRODUCER # <--- La función process_kafka_requests necesita acceder a KAFKA_PRODUCER para enviar los mensajes AUTH_OK, AUTH_DENIED, y el TICKET.
    consumer = None
    max_attempts = 10
    attempt = 0
    
    # Bucle de reintento para la conexión inicial (Resiliencia)
    while consumer is None and attempt < max_attempts:
        attempt += 1
        central_messages.append(f"INFO: Conectando a Kafka (Intento {attempt}/{max_attempts})...")
        try:
            # Crea el consumidor de Kafka
            consumer = KafkaConsumer(
                KAFKA_TOPIC_REQUESTS,
                KAFKA_TOPIC_STATUS,
                bootstrap_servers=[kafka_broker],
                auto_offset_reset='latest',
                group_id='central-processor',
                value_deserializer=lambda x: json.loads(x.decode('utf-8'))
            )
            central_messages.append(f"Kafka Consumer: CONEXIÓN ESTABLECIDA con {kafka_broker}")
            break
        except Exception as e:
            central_messages.append(f"ERROR: Falló la conexión a Kafka: {e}. Esperando 5s para reintentar.")
            time.sleep(5)  # Espera antes de reintentar

    if consumer is None:
        central_messages.append("ERROR FATAL: El sistema no pudo establecer conexión con Kafka después de todos los reintentos.")
        return # Termina el hilo si falla permanentemente

    # Bucle principal de consumo
    for message in consumer:
        try:
            payload = message.value
            topic = message.topic
            
            if topic == KAFKA_TOPIC_REQUESTS:
                # Un conductor solicita servicio
                driver_requests.append({'cp_id': payload['cp_id'], 'user_id': payload['user_id'], 'timestamp': time.strftime('%H:%M:%S')})
                
                # --- Lógica de Gobierno ---
                cp_id = payload['cp_id']
                user_id = payload['user_id']
                cp_status = database.get_cp_status(cp_id)
                
                if cp_status == 'ACTIVADO':
                    central_messages.append(f"Petición OK. Autorizando a Driver {user_id} en CP {cp_id}.")
                    
                    # === IMPLEMENTACIÓN 1: Notificar Autorización (AUTH_OK) ===
                    response_message = {"type": "AUTH_OK", "cp_id": cp_id, "user_id": user_id}
                    if KAFKA_PRODUCER:
                        KAFKA_PRODUCER.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=response_message)
                        
                    # TO-DO: Implementar la lógica de sockets para la autorización al Engine
                    
                else:
                    central_messages.append(f"Petición RECHAZADA. CP {cp_id} no disponible ({cp_status}).")
                    
                    # === IMPLEMENTACIÓN 2: Notificar Denegación (AUTH_DENIED) ===
                    response_message = {"type": "AUTH_DENIED", "cp_id": cp_id, "user_id": user_id, "reason": cp_status}
                    if KAFKA_PRODUCER:
                        KAFKA_PRODUCER.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=response_message)
                
                # Limpiamos la petición después de procesar
                if driver_requests and driver_requests[0]['cp_id'] == cp_id:
                    driver_requests.pop(0)

            elif topic == KAFKA_TOPIC_STATUS:
                # Telemetría o estado del CP
                cp_id = payload['cp_id']
                
                # Lógica de SUMINISTRANDO
                if payload.get('type') == 'CONSUMO':
                    database.update_cp_consumption(cp_id, payload['kwh'], payload['importe'], payload['user_id'])
                    if database.get_cp_status(cp_id) != 'SUMINISTRANDO':
                        database.update_cp_status(cp_id, 'SUMINISTRANDO')
                        central_messages.append(f"INFO: CP {cp_id} ha comenzado el suministro (Driver {payload['user_id']}).")
                
                # LÓGICA DE FIN DE SUMINISTRO
                if payload.get('type') == 'SUPPLY_END': 
                    
                    # === IMPLEMENTACIÓN 3: Enviar Ticket Final (TICKET) ===
                    # Recuperar el consumo final de la BD (asumimos que la BD guarda el último estado)
                    cp_data = database.get_cp_data(cp_id) 
                    final_kwh = cp_data.get('kwh', 0)
                    final_cost = cp_data.get('importe', 0)
                    
                    ticket_message = {
                        "type": "TICKET",
                        "cp_id": cp_id,
                        "user_id": payload['user_id'],
                        "kwh": final_kwh,
                        "importe": final_cost
                    }
                    if KAFKA_PRODUCER:
                        KAFKA_PRODUCER.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=ticket_message)
                        central_messages.append(f"INFO: Ticket final ({final_cost}€) enviado a Driver {payload['user_id']}.")

                    # Después de enviar el ticket, actualizar a ACTIVADO
                    database.update_cp_status(cp_id, 'ACTIVADO') 

                # Lógica de AVERIADO
                if payload.get('type') == 'AVERIADO' or payload.get('type') == 'CONEXION_PERDIDA':
                    if database.get_cp_status(cp_id) == 'NO_EXISTE':
                        database.register_cp(cp_id, 'Ubicación desconocida')
                    database.update_cp_status(cp_id, 'AVERIADO')
                    central_messages.append(f"ALARMA: CP {cp_id} ha reportado una AVERÍA crítica.")

        except Exception as e:
            central_messages.append(f"Error al procesar mensaje de Kafka: {e}")



# --- Funciones del Servidor de Sockets ---
def process_socket_data2(data, cp_id, address, client_socket, central_messages):
    """
    Procesa los mensajes que llegan desde el CP (Monitor).
    Puede recibir:
        - FAULT#CP_ID → indica avería del Engine.
        - ACK#PARAR o ACK#REANUDAR → confirmaciones de comandos.
        - Otros mensajes informativos.
    """
    message = data.decode('utf-8').strip().upper()  # Normalizamos
    print(f"[DEBUG CENTRAL] Recibido: {message}")
    central_messages.append(f"CP {cp_id} -> CENTRAL: {message}")

    parts = message.split('#')
    command = parts[0] if parts else ""

    # --- Reporte de avería desde el Monitor ---
    if command == 'FAULT':
        print(f"[DEBUG CENTRAL] Ejecutando update_cp_status({cp_id}, 'AVERIADO')")
        database.update_cp_status(cp_id, 'AVERIADO')
        central_messages.append(
            f" ALARMA: CP {cp_id} reporta avería (Monitor). Estado actualizado a ROJO."
        )

    elif command == 'RECOVER':
        print(f"[DEBUG CENTRAL] Ejecutando update_cp_status({cp_id}, 'ACTIVADO')")
        database.update_cp_status(cp_id, 'ACTIVADO')
        central_messages.append(
            f"INFO: CP {cp_id} reporta recuperación. Estado actualizado a VERDE."
        )

    # --- Confirmaciones ACK/NACK de comandos ---
    elif command == 'ACK':
        if len(parts) > 1:
            action = parts[1]
            if action == 'REANUDAR':
                database.update_cp_status(cp_id, 'ACTIVADO')
                central_messages.append(
                    f" CP {cp_id} confirmó REANUDAR. Estado actualizado a VERDE."
                )
            elif action == 'PARAR':
                database.update_cp_status(cp_id, 'FUERA_DE_SERVICIO')
                central_messages.append(
                    f" CP {cp_id} confirmó PARAR. Estado actualizado a NARANJA."
                )

    elif command == 'NACK':
        central_messages.append(f"CP {cp_id} rechazó el comando: {message}")

    # --- Otros mensajes no reconocidos ---
    else:
        central_messages.append(f"INFO: Mensaje no reconocido de {cp_id}: {message}")



def handle_client(client_socket, address, central_messages):
    """Maneja la conexión de un único CP."""
    #Inicializa cp_id para identificar qué CP se conecta (se sabrá tras el REGISTER#...)
    cp_id = None
    try:
        # 1. Leer mensaje de registro (Mensaje síncrono inicial)
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
            
            # 2. Empieza a escuchar continuamente mensajes del CP
            while True:
                data = client_socket.recv(1024)
                #Si el socket se cierra, rompe el bucle
                if not data:
                    break # Conexión cerrada por el cliente
                
                # Procesar mensajes de control/averías
                process_socket_data2(data, cp_id, address, client_socket, central_messages)
                
        else:
            central_messages.append(f"ERROR: Mensaje de registro inválido de {address}. Cerrando conexión.")
            
    except Exception as e:
        central_messages.append(f"Error con el CP {cp_id} ({address}): {e}")
    finally:
        # 3. Desconexión
        if cp_id:
            central_messages.append(f"Conexión con CP '{cp_id}' perdida.")

            # Solo marcar DESCONECTADO si NO estaba ya AVERIADO
            current_status = database.get_cp_status(cp_id)
            if current_status not in ['AVERIADO', 'FUERA_DE_SERVICIO']:
                database.update_cp_status(cp_id, 'DESCONECTADO')
            else:
                central_messages.append(f"INFO: CP {cp_id} cerró conexión estando en estado '{current_status}'.")


            if cp_id in active_cp_sockets:
                del active_cp_sockets[cp_id]  # Eliminamos la referencia

        client_socket.close()

def start_socket_server(host, port, central_messages):
    """Inicia el servidor de sockets para escuchar a los CPs."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    #Asocia el socket a una dirección IP y puerto concretos.
    server_socket.bind((host, port))#Ej: ('0.0.0.0', 8000)
    #El socket entra en modo de escucha.
    server_socket.listen(5)#Puede aceptar hasta 5 conexiones en cola
    #Añade un mensaje al log del sistema para indicar que está escuchando correctamente.
    central_messages.append(f"EV_Central escuchando sockets en {host}:{port}")#Este mensaje se muestra luego en el panel de estado (display_panel).

    while True:
        #Espera que un CP se conecte. #Cuando lo hace, devuelve:
        #el canal de comunicación con ese CP
                       #IP/puerto del CP
        client_socket, address = server_socket.accept()
        client_thread = threading.Thread(target=handle_client, args=(client_socket, address, central_messages))
        client_thread.daemon = True
        client_thread.start()

# --- Funciones de Comandos de CENTRAL (Punto 13) ---
def send_cp_command(cp_id, command, central_messages):
    """Envía un comando (Parar/Reanudar) a un CP específico a través del socket síncrono.
    La confirmación ACK/NACK la procesará handle_client() en segundo plano."""
    
    ## 1. Verificamos que el CP esté conectado
    #Si el CP no está en la lista de sockets activos, muestra error. No puede mandarle nada
    if cp_id not in active_cp_sockets:
        central_messages.append(f"ERROR: CP {cp_id} no está conectado por socket para recibir comandos.")
        return
    
    try:
        # 2. Recuperamos el socket activo
        #Recupera el socket del CP a partir del diccionario active_cp_sockets.
        socket_ref = active_cp_sockets[cp_id]
        
        # 3. Enviamos el comando a la CP
        # Formato de mensaje recomendado: <STX><REQUEST><ETX><LRC> (no implementado aquí)
        # Usamos un formato simple: "COMMAND#PARAMETRO". #"PARAR#CENTRAL"
        message = f"{command.upper()}#CENTRAL".encode('utf-8') 
        socket_ref.sendall(message)
       
         # 4. Registramos la acción en los logs
        central_messages.append(f"Comando '{command}' enviado a CP {cp_id}.")
        
           
    except Exception as e:
        #Se informa al panel.
        central_messages.append(f"ERROR al enviar comando a CP {cp_id}: {e}")
        #Se marca el CP como desconectado.
        database.update_cp_status(cp_id, 'DESCONECTADO')
        #Se borra su socket del diccionario active_cp_sockets.
        if cp_id in active_cp_sockets:
            del active_cp_sockets[cp_id]
        

def process_user_input(central_messages):
    """Maneja los comandos de la interfaz de CENTRAL (punto 13 de la mecánica)."""
    while True:
        try:
            # Esperamos el input del usuario en la terminal
            command_line = input("> ").strip().upper()
            #Si el usuario escribe QUIT o Q, lanza una excepción para salir del bucle.
            if command_line == 'QUIT' or command_line == 'Q':
                raise KeyboardInterrupt
            
            parts = command_line.split()
            command = parts[0]
            
            if command == 'P' or command == 'PARAR':    #Ejemplo: P MAD-01 → ['P', 'MAD-01']
                if len(parts) == 2:
                    #Si el usuario escribió P o PARAR, verifica que haya un segundo parámetro (CP_ID).
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
            #comando desconocido
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
        print("Uso: python EV_Central.py <puerto_socket> <kafka_broker_ip:port>")
        sys.exit(1)

    try:
        SOCKET_PORT = int(sys.argv[1])
        KAFKA_BROKER = sys.argv[2]
        HOST = '0.0.0.0'
        
        central_messages = ["CENTRAL system status OK"]
        driver_requests = []

        # 1. Configurar la base de datos
        database.setup_database()
        
        # 2. Inicializar el Productor de Kafka (CRÍTICO)
        KAFKA_PRODUCER = KafkaProducer(
            bootstrap_servers=[KAFKA_BROKER],
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        central_messages.append(f"Kafka Producer inicializado.")

        # 3. Iniciar el servidor de Sockets (Registro y Control Síncrono)
        server_thread = threading.Thread(target=start_socket_server, args=(HOST, SOCKET_PORT, central_messages), daemon=True)
        server_thread.start()
        
        # 4. Iniciar el Consumidor Kafka (Peticiones y Telemetría)
        kafka_thread = threading.Thread(target=process_kafka_requests, args=(KAFKA_BROKER, central_messages, driver_requests), daemon=True)
        kafka_thread.start()
        
        # 5. Iniciar la entrada de comandos del usuario
        input_thread = threading.Thread(target=process_user_input, args=(central_messages,), daemon=True)
        input_thread.start()

        # 6. Iniciar el panel de monitorización en el hilo principal
        display_panel(central_messages, driver_requests)


    except ValueError:
        print("Error: El puerto debe ser un número entero.")
        sys.exit(1)
    except Exception as e:
        # Esto captura errores al inicializar KafkaProducer si el broker no está
        print(f"Error fatal al iniciar la Central: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nServidor detenido por el usuario. Cerrando hilos...")
        sys.exit(0)

