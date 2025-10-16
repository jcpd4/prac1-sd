# Fichero: ev_central.py
import socket
import threading
import sys
import time
import os
# Asegúrate de tener instalado: pip install kafka-python
from kafka import KafkaConsumer, KafkaProducer
import json
import database # Módulo de base de datos (se asume implementado)

# --- Configuración global (ajusta los topics) ---
KAFKA_TOPIC_REQUESTS = 'driver_requests' # Conductores -> Central
KAFKA_TOPIC_STATUS = 'cp_telemetry'      # CP -> Central (Telemetría/Averías/Consumo)
KAFKA_TOPIC_CENTRAL_ACTIONS = 'central_actions' # Central -> CP (Parar/Reanudar)
KAFKA_TOPIC_DRIVER_NOTIFY = 'driver_notifications' # Central -> Drivers

# Diccionario para almacenar la referencia a los sockets de los CPs activos
active_cp_sockets = {} 

# Lock para proteger active_cp_sockets y lista de mensajes compartidos
active_cp_lock = threading.Lock()

def push_message(msg_list, msg, maxlen=200):
    """Añade msg a msg_list y mantiene solo los últimos maxlen elementos."""
    msg_list.append(msg)
    if len(msg_list) > maxlen:
        # eliminar los más antiguos
        del msg_list[0:len(msg_list)-maxlen]

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
        print("="*80)
        
        #1. --- Sección de Puntos de Recarga (CPs) ---
        #Recupera todos los CPs registrados en la base de datos.
        all_cps = database.get_all_cps()
        if not all_cps:
            print("No hay Puntos de Recarga registrados.")
        else:
            # Añadimos columna de precio
            print(f"{'ID':<10} | {'UBICACIÓN':<25} | {'PRECIO':<12} | {'ESTADO':<20}")
            print("-"*80)
            for cp in all_cps:
                price = database.get_cp_price(cp['id'])
                price_str = f"{price:.2f} €/kWh" if price is not None else "N/A"
                colored_status = get_status_color(cp['status'])
                print(f"{cp['id']:<10} | {cp['location']:<25} | {price_str:<12} | {colored_status}")

                # Si está suministrando, mostramos datos de consumo acumulado
                if cp.get('status') == 'SUMINISTRANDO':
                    kwh = cp.get('kwh', 0.0)
                    importe = cp.get('importe', 0.0)
                    driver = cp.get('driver_id', 'N/A')
                    print(f"    -> SUMINISTRANDO: {kwh:.3f} kWh  |  {importe:.2f} €  |  driver: {driver}")
        print("="*80)

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
            auto_offset_reset='latest',
            group_id='central-processor',
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )

        # Productor para enviar notificaciones a drivers (autorización / ticket)
        producer = KafkaProducer(
            bootstrap_servers=[kafka_broker],
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        central_messages.append(f"Kafka Consumer: Conectado al broker {kafka_broker}")
    except Exception as e:
        central_messages.append(f"ERROR: No se pudo conectar a Kafka ({kafka_broker}): {e}")
        return



    for message in consumer:
        try:
            payload = message.value
            topic = message.topic

            # ---- Peticiones de drivers (driver_requests) ----
            if topic == KAFKA_TOPIC_REQUESTS:
                cp_id = payload.get('cp_id')
                user_id = payload.get('user_id')
                # Usar 'timestamp' para compatibilizar con display_panel
                ts = time.strftime('%H:%M:%S')
                driver_requests.append({'cp_id': cp_id, 'user_id': user_id, 'timestamp': ts})
                # Log inmediato en consola para trazabilidad
                print(f"[CENTRAL][KAFKA] Recibida petición de Driver {user_id} para CP {cp_id} a las {ts}")

                # Autorizar solo si CP está ACTIVADO
                cp_status = database.get_cp_status(cp_id)
                if cp_status == 'ACTIVADO':
                    notify = {"type": "AUTH_OK", "cp_id": cp_id, "user_id": user_id, "message": "Autorizado"}
                    producer.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=notify)
                    producer.flush()
                    central_messages.append(f"AUTORIZADO: Driver {user_id} -> CP {cp_id}")
                    print(f"[CENTRAL] AUTORIZACIÓN enviada a Driver {user_id} para CP {cp_id}")
                else:
                    notify = {"type": "AUTH_DENIED", "cp_id": cp_id, "user_id": user_id, "reason": cp_status}
                    producer.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=notify)
                    producer.flush()
                    central_messages.append(f"DENEGADO: Driver {user_id} -> CP {cp_id} (estado={cp_status})")
                    print(f"[CENTRAL] DENEGACIÓN enviada a Driver {user_id} para CP {cp_id} (estado={cp_status})")

                # opcional: eliminar petición procesada de la lista (primera coincidencia)
                for i, req in enumerate(driver_requests):
                    if req.get('cp_id') == cp_id and req.get('user_id') == user_id:
                        del driver_requests[i]
                        break

            elif topic == KAFKA_TOPIC_STATUS:
                msg_type = payload.get('type', '').upper()
                cp_id = payload.get('cp_id')

                # Consumo periódico (ENGINE envía cada segundo)
                if msg_type == 'CONSUMO':
                    kwh = float(payload.get('kwh', 0))
                    importe = float(payload.get('importe', 0))
                    driver_id = payload.get('user_id') or payload.get('driver_id')

                    # Si el CP no está registrado, lo creamos automáticamente
                    current_status = database.get_cp_status(cp_id)
                    if current_status == 'NO_EXISTE' or current_status is None:
                        database.register_cp(cp_id, "Desconocida")
                        database.update_cp_status(cp_id, 'ACTIVADO')
                        push_message(central_messages, f"AUTOREGISTRO: CP {cp_id} registrado automáticamente (ubicación desconocida).")

                    # Actualiza BD (esto marcará SUMINISTRANDO)
                    database.update_cp_consumption(cp_id, kwh, importe, driver_id)

                    # Recuperar precio real desde la BD (no calcularlo)
                    price = database.get_cp_price(cp_id)
                    price_str = f"{price:.2f} €/kWh" if price is not None else "N/A"

                    push_message(central_messages,
                        f"TELEMETRÍA: CP {cp_id} - {kwh:.3f} kWh - {importe:.2f} € - driver {driver_id} - precio {price_str}"
                    )

                # Fin de suministro: generar ticket final y limpiar consumo
                elif msg_type == 'SUPPLY_END':
                    kwh = float(payload.get('kwh', 0))
                    importe = float(payload.get('importe', 0))
                    driver_id = payload.get('user_id') or payload.get('driver_id')

                    # Limpiar campos de consumo y dejar CP disponible (clear_cp_consumption hace ACTIVADO)
                    database.clear_cp_consumption(cp_id)

                    central_messages.append(
                        f"TICKET FINAL: CP {cp_id} - driver {driver_id} - {kwh:.3f} kWh - {importe:.2f} €"
                    )

                    # Notificar ticket al driver si corresponde
                    try:
                        ticket_msg = {"type": "TICKET", "cp_id": cp_id, "user_id": driver_id, "kwh": kwh, "importe": importe}
                        producer.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=ticket_msg)
                        producer.flush()
                    except Exception as e:
                        central_messages.append(f"ERROR: no se pudo notificar ticket a driver {driver_id}: {e}")
               # Eventos de avería / pérdida de conexión
                elif msg_type in ('AVERIADO', 'CONEXION_PERDIDA', 'FAULT'):
                    database.update_cp_status(cp_id, 'AVERIADO')
                    central_messages.append(f"ALERTA: CP {cp_id} reporta AVERÍA/CONEXIÓN. Estado -> AVERIADO")

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
    raw = data.decode('utf-8').strip()
    # Normalizamos comando (solo la parte del comando), pero mantenemos el resto
    parts = raw.split('#')
    command = parts[0].upper() if parts else ""
    print(f"[DEBUG CENTRAL] Recibido raw: {raw} -> command: {command}")
    push_message(central_messages, f"CP {cp_id} -> CENTRAL: {raw}")

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
        central_messages.append(f"CP {cp_id} rechazó el comando: {raw}")

    # --- Otros mensajes no reconocidos ---
    else:
        central_messages.append(f"INFO: Mensaje no reconocido de {cp_id}: {raw}")



def handle_client(client_socket, address, central_messages):
    """Maneja la conexión de un único CP."""
    #Inicializa cp_id para identificar qué CP se conecta (se sabrá tras el REGISTER#...)
    cp_id = None
    try:
        # 1. Leer mensaje de registro (Mensaje síncrono inicial)
        message = client_socket.recv(1024).decode('utf-8')
        parts = message.strip().split('#')

        if len(parts) >= 3 and parts[0] == 'REGISTER':
            cp_id = parts[1]
            location = parts[2]
            # Si se envía precio opcional: REGISTER#CP_ID#LOCATION#PRICE
            price = None
            if len(parts) >= 4:
                try:
                    price = float(parts[3])
                except Exception:
                    price = None

            # Registrar en la BD y actualizar estado a ACTIVADO
            database.register_cp(cp_id, location, price_per_kwh=price)
            database.update_cp_status(cp_id, 'ACTIVADO')
            push_message(central_messages, f"CP '{cp_id}' registrado/actualizado desde {address}. Estado: ACTIVADO (price={price})")

            # Guardamos la referencia del socket para envíos síncronos (autorización/órdenes)
            with active_cp_lock:
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
            push_message(central_messages, f"Conexión con CP '{cp_id}' perdida.")
            # Solo marcar DESCONECTADO si NO estaba ya AVERIADO
            current_status = database.get_cp_status(cp_id)
            if current_status not in ['AVERIADO', 'FUERA_DE_SERVICIO']:
                database.update_cp_status(cp_id, 'DESCONECTADO')
            else:
                push_message(central_messages, f"INFO: CP {cp_id} cerró conexión estando en estado '{current_status}'.")

            with active_cp_lock:
                if cp_id in active_cp_sockets:
                    del active_cp_sockets[cp_id]
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