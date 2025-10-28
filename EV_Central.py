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

# --- Configuración global ---
KAFKA_TOPIC_REQUESTS = 'driver_requests' # Conductores -> Central
KAFKA_TOPIC_STATUS = 'cp_telemetry'      # CP -> Central (Telemetría/Averías/Consumo)
KAFKA_TOPIC_CENTRAL_ACTIONS = 'central_actions' # Central -> CP (Parar/Reanudar)
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



# --- Funciones auxiliares ---
def push_message(msg_list, msg, maxlen=200):
    """Añade msg a msg_list y mantiene solo los últimos maxlen elementos."""
    msg_list.append(msg)
    if len(msg_list) > maxlen:
        # eliminar los más antiguos
        del msg_list[0:len(msg_list)-maxlen]

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
                            del cp_driver_assignments[cp_id]
                            database.update_cp_status(cp_id, 'ACTIVADO')
                            print(f"[CENTRAL] Driver {driver_id} desconectado. CP {cp_id} liberado.")
                
                if drivers_to_remove:
                    print(f"[CENTRAL] Drivers desconectados eliminados: {drivers_to_remove}")
                    
        except Exception as e:
            print(f"[CENTRAL] Error en limpieza de drivers: {e}")

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
        "FUERA_DE_SERVICIO": "\033[93m", # Naranja/Amarillo
        "RESERVADO": "\033[96m"      # preguntar a juanky
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
                legend = ""
                if cp['status'] == 'FUERA_DE_SERVICIO':
                    legend = " (Out of Order)"
                print(f"{cp['id']:<10} | {cp['location']:<25} | {price_str:<12} | {colored_status}{legend}")

                # Si está suministrando, mostramos datos de consumo acumulado
                if cp.get('status') == 'SUMINISTRANDO':
                    kwh = cp.get('kwh', 0.0)
                    importe = cp.get('importe', 0.0)
                    driver = cp.get('driver_id', 'N/A')
                    print(f"    -> SUMINISTRANDO: {kwh:.3f} kWh  |  {importe:.2f} €  |  driver: {driver}")
        print("="*80)

        #2. --- Sección Drivers Conectados ---
        print("\n*** DRIVERS CONECTADOS ***")
        with active_cp_lock:
            if connected_drivers:
                for driver_id in connected_drivers:
                    # Verificar si el driver está asignado a algún CP
                    assigned_cp = None
                    for cp_id, assigned_driver in cp_driver_assignments.items():
                        if assigned_driver == driver_id:
                            assigned_cp = cp_id
                            break
                    
                    if assigned_cp:
                        print(f"Driver {driver_id} -> CP {assigned_cp} (ASIGNADO)")
                    else:
                        print(f"Driver {driver_id} (DISPONIBLE)")
            else:
                print("No hay drivers conectados.")
        
        #3. --- Sección Peticiones de Conductores (Kafka) ---
        print("\n*** PETICIONES DE CONDUCTORES EN CURSO (Kafka) ***")
        #Lista todas las solicitudes Kafka del topic driver_requests
        if driver_requests:
            for req in driver_requests:
                print(f"[{req['timestamp']}] Driver {req['user_id']} solicita recarga en CP {req['cp_id']}")
        else:
            print("No hay peticiones pendientes.")
        
        #4. --- Sección de Mensajes de Aplicación (Kafka/General) ---
        print("\n*** MENSAJES DEL SISTEMA ***")
        if central_messages:
            for msg in central_messages:
                print(msg)
        
        print("="*50)
        #Instrucciones para que el operador de la Central escriba comandos para controlar los CPs.
        print("Comandos: [P]arar <CP_ID> | [R]eanudar <CP_ID> | [PT] Parar todos | [RT] Reanudar todos | [Q]uit")
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
    #Paso 1: Verificar si el driver está conectado
    with active_cp_lock:
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

            # Paso 2.1: Procesar las peticiones de drivers (driver_requests)
            if topic == KAFKA_TOPIC_REQUESTS:
                cp_id = payload.get('cp_id') # ID del CP solicitado
                user_id = payload.get('user_id') # ID del driver que solicita la recarga
                action = (payload.get('type') or '').upper() # Tipo de acción (REQUEST_CHARGE, STOP_CHARGE, etc.)
                ts = time.strftime('%H:%M:%S') # Usar 'timestamp' para compatibilizar con display_panel
                driver_requests.append({'cp_id': cp_id, 'user_id': user_id, 'timestamp': ts})
                # Log inmediato en consola para trazabilidad
                print(f"[CENTRAL] Solicitud recibida del driver {user_id} para CP {cp_id}...")
                print(f"[CENTRAL] Comprobando estado del CP...")
                #Paso 2.1.1: Registrar/actualizar que el driver está conectado
                with active_cp_lock:
                    connected_drivers.add(user_id)
                #Paso 2.1.2: Verificar si el driver ya está conectado a otro CP (por sesiones activas)
                driver_already_connected = any(sess.get('driver_id') == user_id for sess in current_sessions.values())
                if driver_already_connected:
                    notify = {"type": "AUTH_DENIED", "cp_id": cp_id, "user_id": user_id, "reason": "Driver ya conectado a otro CP"}
                    #Paso 2.1.2.1: Enviar notificación de denegación al driver
                    send_notification_to_driver(producer, user_id, notify)
                    #Paso 2.1.2.2: Agregar mensaje de denegación a la lista de mensajes
                    central_messages.append(f"DENEGADO: Driver {user_id} -> CP {cp_id} (ya conectado a otro CP)")
                    #Paso 2.1.2.3: Mostrar mensaje de denegación en la consola
                    print(f"[CENTRAL] DENEGACIÓN enviada a Driver {user_id} para CP {cp_id} (ya conectado a otro CP)")
                    #Paso 2.1.2.4: Eliminar petición procesada
                    for i, req in enumerate(driver_requests):
                        if req.get('cp_id') == cp_id and req.get('user_id') == user_id:
                            del driver_requests[i]
                            break
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
                    #Paso 2.1.3.4: Eliminar petición procesada
                    for i, req in enumerate(driver_requests):
                        if req.get('cp_id') == cp_id and req.get('user_id') == user_id:
                            del driver_requests[i]
                            break
                    continue


                
                #Paso 2.2: Cargar el estado del CP
                cp_status = database.get_cp_status(cp_id)
                print(f"[CENTRAL] Estado del CP: {cp_status}") # Mostrar el estado del CP
                

                #Paso 2.3: Autorizar solo si CP está ACTIVADO y disponible
                if cp_status == 'ACTIVADO' and (action in ['', 'REQUEST_CHARGE']):
                    print(f"[CENTRAL] Enviando START_SESSION al CP...")
                    #Paso 2.3.1: Reservar el CP inmediatamente
                    database.update_cp_status(cp_id, 'RESERVADO') # Reservamos el CP inmediatamente
                    #Paso 2.3.2: Registrar driver como conectado y abrir sesión en el CP
                    with active_cp_lock:
                        connected_drivers.add(user_id)
                        current_sessions[cp_id] = { 'driver_id': user_id, 'status': 'authorized' }
                    
                    #Paso 2.3.3: Enviar comando de autorización al Monitor del CP vía SOCKET
                    if cp_id in active_cp_sockets:
                        try:
                            cp_socket = active_cp_sockets[cp_id]
                            # Emular START_SESSION semánticamente con AUTORIZAR_SUMINISTRO hacia el CP
                            auth_command = f"AUTORIZAR_SUMINISTRO#{user_id}"
                            cp_socket.sendall(auth_command.encode('utf-8'))
                            print(f"[CENTRAL] Comando AUTORIZAR_SUMINISTRO enviado a Monitor de CP {cp_id} para Driver {user_id}")
                            print(f"[CENTRAL] Esperando confirmación del CP...")
                        except Exception as e:
                            central_messages.append(f"ERROR: No se pudo enviar comando de autorización a CP {cp_id}: {e}")
                    #Paso 2.3.4: Enviar notificación de autorización al driver
                    notify = {"type": "AUTH_OK", "cp_id": cp_id, "user_id": user_id, "message": "Autorizado"}
                    #Paso 2.3.4.1: Enviar notificación de autorización al driver
                    send_notification_to_driver(producer, user_id, notify)
                    #Paso 2.3.4.2: Agregar mensaje de autorización a la lista de mensajes
                    central_messages.append(f"AUTORIZADO: Driver {user_id} -> CP {cp_id}")
                    #Paso 2.3.4.3: Mostrar mensaje de autorización en la consola
                    print(f"[CENTRAL] AUTORIZACIÓN enviada a Driver {user_id} para CP {cp_id}")
                else:
                    #Paso 2.3.5: Enviar notificación de denegación al driver
                    print(f"[CENTRAL] Enviando DENEGACIÓN al driver...")
                    notify = {"type": "AUTH_DENIED", "cp_id": cp_id, "user_id": user_id, "reason": cp_status}
                    #Paso 2.3.5.1: Enviar notificación de denegación al driver
                    send_notification_to_driver(producer, user_id, notify)
                    #Paso 2.3.5.2: Agregar mensaje de denegación a la lista de mensajes
                    central_messages.append(f"DENEGADO: Driver {user_id} -> CP {cp_id} (estado={cp_status})")
                    #Paso 2.3.5.3: Mostrar mensaje de denegación en la consola
                    print(f"[CENTRAL] DENEGACIÓN enviada a Driver {user_id} para CP {cp_id} (estado={cp_status})")
                    # Eliminar petición procesada
                    for i, req in enumerate(driver_requests):
                        if req.get('cp_id') == cp_id and req.get('user_id') == user_id:
                            del driver_requests[i]
                            break




            #Paso 2.4: Procesar las telemetrías de los CPs (cp_telemetry)
            elif topic == KAFKA_TOPIC_STATUS:
                msg_type = payload.get('type', '').upper() # Tipo de mensaje (CONSUMO, SESSION_STARTED, SUPPLY_END, etc.)
                cp_id = payload.get('cp_id') # ID del CP

                #Paso 2.4.1: Procesar el consumo periódico (ENGINE envía cada segundo)
                if msg_type == 'CONSUMO':
                    kwh = float(payload.get('kwh', 0)) # Consumo en kWh
                    importe = float(payload.get('importe', 0)) # Importe en euros
                    driver_id = payload.get('user_id') or payload.get('driver_id') # ID del driver

                    #Paso 2.4.1.1: Si el CP no está registrado, lo creamos automáticamente
                    current_status = database.get_cp_status(cp_id)
                    if current_status == 'NO_EXISTE' or current_status is None:
                        database.register_cp(cp_id, "Desconocida")
                        database.update_cp_status(cp_id, 'ACTIVADO')
                        push_message(central_messages, f"AUTOREGISTRO: CP {cp_id} registrado automáticamente (ubicación desconocida).")

                    # Paso 2.4.1.2: Actualiza BD (esto marcará SUMINISTRANDO)
                    database.update_cp_consumption(cp_id, kwh, importe, driver_id)
                    # Paso 2.4.1.3: Actualizar estado de sesión a 'charging' si coincide driver
                    with active_cp_lock:
                        sess = current_sessions.get(cp_id)
                        if sess and sess.get('driver_id') == driver_id and sess.get('status') != 'charging':
                            current_sessions[cp_id]['status'] = 'charging'

                    # Paso 2.4.1.4: Reenviar una notificación de consumo al driver a través de su topic
                    try:
                        consumo_msg = {"type": "CONSUMO_UPDATE", "cp_id": cp_id, "user_id": driver_id, "kwh": kwh, "importe": importe}
                        #Paso 2.4.1.4.1: Enviar la notificación al driver
                        producer.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=consumo_msg)
                    except Exception as e:
                        push_message(central_messages, f"ERROR: no se pudo notificar consumo a driver {driver_id}: {e}")
                

                    # Paso 2.4.1.5: Recuperar precio real desde la BD (no calcularlo)
                    price = database.get_cp_price(cp_id)
                    price_str = f"{price:.2f} €/kWh" if price is not None else "N/A"
                    #Paso 2.4.1.5.1: Agregar mensaje de telemetría a la lista de mensajes
                    push_message(central_messages,
                        f"TELEMETRÍA: CP {cp_id} - {kwh:.3f} kWh - {importe:.2f} € - driver {driver_id} - precio {price_str}"
                    )

                # Paso 2.4.2: Procesar el inicio de sesión (opcional, informativo)
                elif msg_type == 'SESSION_STARTED':
                    #Paso 2.4.2.1: Robustez: si no viene driver_id en payload, úsalo de la sesión
                    driver_id = payload.get('user_id') or payload.get('driver_id')
                    if not driver_id:
                        with active_cp_lock:
                            #Paso 2.4.2.1.1: Obtener el driver_id de la sesión
                            sess = current_sessions.get(cp_id)
                            if sess:
                                driver_id = sess.get('driver_id')
                    #Paso 2.4.2.1.2: Actualizar el estado de sesión a 'charging' si coincide driver
                    with active_cp_lock:
                        sess = current_sessions.get(cp_id)
                        if sess and sess.get('driver_id') == driver_id:
                            current_sessions[cp_id]['status'] = 'charging'
                    #Paso 2.4.2.1.3: Agregar mensaje de inicio de sesión a la lista de mensajes
                    push_message(central_messages, f"SESIÓN INICIADA: CP {cp_id} con driver {driver_id}")

                # Paso 2.4.3: Procesar el fin de suministro: generar ticket final o notificar error si fue interrumpido
                elif msg_type == 'SUPPLY_END':
                    print(f"[CENTRAL] DEBUG: Procesando SUPPLY_END para CP {cp_id}")
                    kwh = float(payload.get('kwh', 0)) # Consumo en kWh
                    importe = float(payload.get('importe', 0)) # Importe en euros
                    driver_id = payload.get('user_id') or payload.get('driver_id')
                    current_status = database.get_cp_status(cp_id) # Estado del CP
                    print(f"[CENTRAL] DEBUG: SUPPLY_END - CP: {cp_id}, Driver: {driver_id}, kWh: {kwh}, Importe: {importe}, Estado: {current_status}")

                    # Paso 2.4.3.1: Si el CP está FUERA_DE_SERVICIO, significa que fue parado durante la carga
                    if current_status == 'FUERA_DE_SERVICIO':
                        #Paso 2.4.3.1.1: Crear el mensaje de error
                        error_msg = {
                            "type": "SUPPLY_ERROR",
                            "cp_id": cp_id,
                            "user_id": driver_id,
                            "reason": "Carga interrumpida: CP puesto fuera de servicio",
                            "kwh_partial": kwh,
                            "importe_partial": importe
                        }
                        producer.send(KAFKA_TOPIC_DRIVER_NOTIFY, value=error_msg)
                        producer.flush()
                        #Paso 2.4.3.1.2: Agregar mensaje de error a la lista de mensajes
                        central_messages.append(
                            f"CARGA INTERRUMPIDA: CP {cp_id} - driver {driver_id} - Parcial: {kwh:.3f} kWh / {importe:.2f} €"
                        )
                        print(f"[CENTRAL] Carga interrumpida en CP {cp_id} (FUERA_DE_SERVICIO). Notificando a driver {driver_id}")
                        #Paso 2.4.3.1.3: Limpiar telemetría pero mantener estado FUERA_DE_SERVICIO
                        database.clear_cp_telemetry_only(cp_id)
                        
                    else:
                        #Paso 2.4.3.2: Caso normal: generar ticket y dejar CP disponible
                        database.clear_cp_consumption(cp_id)  # Esto pone estado en ACTIVADO

                        print(f"[CENTRAL] Generando ticket final para driver {driver_id}...")
                        central_messages.append(
                            f"TICKET FINAL: CP {cp_id} - driver {driver_id} - {kwh:.3f} kWh - {importe:.2f} €"
                        )

                        #Paso 2.4.3.2.1: Notificar ticket normal al driver asignado
                        try:
                            ticket_msg = {
                                "type": "TICKET",
                                "cp_id": cp_id,
                                "user_id": driver_id,
                                "kwh": kwh,
                                "importe": importe
                            }
                            #Paso 2.4.3.2.1.1: Enviar el ticket al driver
                            print(f"[CENTRAL] Enviando ticket a Driver {driver_id}...")
                            success = send_notification_to_driver(producer, driver_id, ticket_msg)
                            if success:
                                print(f"[CENTRAL] Ticket enviado exitosamente a Driver {driver_id}: {kwh} kWh, {importe} €")
                            else:
                                print(f"[CENTRAL] ERROR: No se pudo enviar ticket a Driver {driver_id}")
                        except Exception as e:
                            central_messages.append(f"ERROR: no se pudo notificar ticket a driver {driver_id}: {e}")
                            print(f"[CENTRAL] EXCEPTION al enviar ticket: {e}")
                        
                        #Paso 2.4.3.2.1.2: Cerrar sesión y liberar la asignación del driver al CP
                        with active_cp_lock:
                            if cp_id in current_sessions:
                                del current_sessions[cp_id]
                                print(f"[CENTRAL] Sesión cerrada: CP {cp_id} liberado")

                        #Paso 2.4.3.2.1.3: Actualizar estado del CP a ACTIVADO
                        print(f"[CENTRAL] CP {cp_id} vuelve a estado ACTIVADO")
                        database.update_cp_status(cp_id, 'ACTIVADO')

                # Paso 2.4.4: Procesar los eventos de avería / pérdida de conexión
                elif msg_type in ('AVERIADO', 'CONEXION_PERDIDA', 'FAULT'):
                    #Paso 2.4.4.1: Comprobar si hay suministro en curso
                    cp_data = database.get_all_cps()
                    cp_info = next((cp for cp in cp_data if cp['id'] == cp_id), None)
                    
                    if cp_info and cp_info.get('status') == 'SUMINISTRANDO':
                        driver_id = cp_info.get('driver_id')
                        kwh = cp_info.get('kwh', 0.0)
                        importe = cp_info.get('importe', 0.0)
                        
                        #Paso 2.4.4.2: Notificar al conductor la interrupción por avería
                        error_msg = {
                            "type": "SUPPLY_ERROR",
                            "cp_id": cp_id,
                            "user_id": driver_id,
                            "reason": "Carga interrumpida: Avería detectada en el punto de recarga",
                            "kwh_partial": kwh,
                            "importe_partial": importe
                        }
                        send_notification_to_driver(producer, driver_id, error_msg)
                        #Paso 2.4.4.2.1: Agregar mensaje de error a la lista de mensajes
                        #Paso 2.4.4.2.2: Log detallado en Central
                        msg = (f"AVERÍA DURANTE SUMINISTRO en CP {cp_id}\n"
                              f"    → Estado: AVERIADO (ROJO)\n"
                              f"    → Driver: {driver_id}\n"
                              f"    → Consumo hasta avería: {kwh:.3f} kWh / {importe:.2f} €\n"
                              f"    → Notificación enviada al conductor")
                        #Paso 2.4.4.2.3: Agregar mensaje de error a la lista de mensajes
                        central_messages.append(msg)
                        print(f"[CENTRAL] {msg}")
                        
                        #Paso 2.4.4.2.4: Limpiar telemetría pero mantener estado AVERIADO
                        database.clear_cp_telemetry_only(cp_id)
                        
                    else:
                        #Paso 2.4.4.3: CP no estaba suministrando
                        msg = f"AVERÍA detectada en CP {cp_id} - Estado actualizado a ROJO"
                        central_messages.append(msg)
                        print(f"[CENTRAL] {msg}")
                    
                    #Paso 2.4.4.4: Actualizar estado a AVERIADO y cerrar sesión si existiese
                    database.update_cp_status(cp_id, 'AVERIADO')
                    with active_cp_lock:
                        if cp_id in current_sessions:
                            del current_sessions[cp_id]

        except Exception as e:
            central_messages.append(f"Error al procesar mensaje de Kafka: {e}")



# --- Funciones del Servidor de Sockets ---

# Funcion Socket para procesar los mensajes que llegan desde el CP (Monitor)
def process_socket_data2(data, cp_id, address, client_socket, central_messages, kafka_broker):
    """
    Procesa los mensajes que llegan desde el CP (Monitor).
    """
    #FASE 1: Decodificar el mensaje recibido
    raw = data.decode('utf-8').strip()
    # Normalizar el comando (solo la parte del comando), pero mantenemos el resto
    parts = raw.split('#')
    #Extraer el comando
    command = parts[0].upper() if parts else ""
    # Mostrar el mensaje recibido
    print(f"[CENTRAL] Recibido de CP {cp_id}: {raw}")
    #Mostrar el mensaje recibido en el panel de estado
    push_message(central_messages, f"CP {cp_id} -> CENTRAL: {raw}")




    #FASE 2: Procesar el mensaje recibido
    

    #FASE 2.1: Reporte de avería desde el Monitor
    if command == 'FAULT':
        print(f"[CENTRAL] CP {cp_id} reporta AVERÍA. Actualizando estado a ROJO.")
        
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
                        "reason": "Carga interrumpida: Avería detectada en el punto de recarga",
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
            print(f"[CENTRAL] {msg}")
        
        # Actualizar estado a AVERIADO
        database.update_cp_status(cp_id, 'AVERIADO')



    #FASE 2.2: Recuperación de avería desde el Monitor
    elif command == 'RECOVER':
        print(f"[CENTRAL]  CP {cp_id} reporta RECUPERACIÓN. Actualizando estado a VERDE.")
        # 2.2.1 Actualizar estado a ACTIVADO
        database.update_cp_status(cp_id, 'ACTIVADO')
        # 2.2.2 Log del mensaje
        msg = f" CP {cp_id} se ha recuperado de la avería - Estado actualizado a VERDE"
        central_messages.append(msg)
        print(f"[CENTRAL] {msg}")

    
    
    #FASE 2.3: Confirmaciones ACK/NACK de comandos
    elif command == 'ACK':
        if len(parts) > 1:
            action = parts[1]
            # 2.3.1 Reanudar el CP
            if action == 'REANUDAR':
                print(f"[CENTRAL]  CP {cp_id} confirmó REANUDAR. Actualizando a VERDE.")
                database.update_cp_status(cp_id, 'ACTIVADO')
                central_messages.append(
                    f"CP {cp_id} confirmó REANUDAR. Estado actualizado a VERDE."
                )
            # 2.3.2 Parar el CP
            elif action == 'PARAR':
                print(f"[CENTRAL]  CP {cp_id} confirmó PARAR. Actualizando a NARANJA (Out of Order).")
                database.update_cp_status(cp_id, 'FUERA_DE_SERVICIO')
                central_messages.append(
                    f" CP {cp_id} confirmó PARAR. Estado actualizado a NARANJA (Out of Order)."
                )

    elif command == 'NACK':
        print(f"[CENTRAL]  CP {cp_id} RECHAZÓ el comando: {raw}")
        central_messages.append(f" CP {cp_id} rechazó el comando: {raw}")

    
    
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
                    client_socket.sendall(assigned_driver.encode('utf-8'))
                    print(f"[CENTRAL] Sesión válida para CP {requested_cp_id} con Driver {assigned_driver} (status={sess.get('status')})")
                else:
                    client_socket.sendall(b"NO_DRIVER")
                    if assigned_driver:
                        print(f"[CENTRAL] Sesión encontrada pero driver no conectado para CP {requested_cp_id}")
                    else:
                        print(f"[CENTRAL] No hay sesión activa para CP {requested_cp_id}")
        else:
            client_socket.sendall(b"NO_DRIVER")

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
                    # 2.5.6 Enviar el driver asignado al CP
                    client_socket.sendall(assigned_driver.encode('utf-8'))
                    print(f"[CENTRAL] Sesión autorizada confirmada para CP {requested_cp_id} con Driver {assigned_driver} (status={sess.get('status')})")
                else:
                    client_socket.sendall(b"NO_SESSION")
                    print(f"[CENTRAL] No hay sesión autorizada para CP {requested_cp_id}")
        else:
            client_socket.sendall(b"NO_SESSION")

    # --- Otros mensajes no reconocidos ---
    else:
        print(f"[CENTRAL]  Mensaje no reconocido de CP {cp_id}: {raw}")
        central_messages.append(f" Mensaje no reconocido de CP {cp_id}: {raw}")



# Funcion Socket para manejar la conexión de un único CP
def handle_client(client_socket, address, central_messages, kafka_broker):
    """Maneja la conexión de un único CP."""
    #Inicializa cp_id para identificar qué CP se conecta (se sabrá tras el REGISTER#...)
    cp_id = None
    try:
        # FASE 1: Leer primer mensaje desde el CP/cliente
        #¿Qué puede recibir?**
        # `REGISTER#CP_ID#LOCATION#PRICE` → Registro de CP
        # `CHECK_SESSION#CP_ID` → Consulta de sesión
        # `CHECK_DRIVER#CP_ID` → Consulta de driver
        # `FAULT#CP_ID` → Avería
        # `ACK#COMANDO` → Confirmación
        # `NACK#COMANDO` → Rechazo de comando
        # `RECOVER#CP_ID` → Recuperación de avería
        # Espera hasta 1024 bytes de datos
        # Decodifica: b'REGISTER#MAD-01#...' → 'REGISTER#MAD-01#... (cadena de texto)'
        message = client_socket.recv(1024).decode('utf-8')
        parts = message.strip().split('#')

        
        
        # FASE 2: Procesar el mensaje recibido
        # FASE 2.1: Soportar consultas rápidas CHECK_SESSION / CHECK_DRIVER en nuevas conexiones (sin REGISTER)
        if parts and parts[0] in ['CHECK_SESSION', 'CHECK_DRIVER']:
            process_socket_data2(message.encode('utf-8'), None, address, client_socket, central_messages, KAFKA_BROKER)
            return

        # FASE 2.2: Registrar el CP si se envía un mensaje REGISTER
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

            #Fase2.2.1: Registrar en la BD 
            database.register_cp(cp_id, location, price_per_kwh=price)
            #Fase2.2.2: Actualizar estado a ACTIVADO
            database.update_cp_status(cp_id, 'ACTIVADO')
            push_message(central_messages, f"CP '{cp_id}' registrado/actualizado desde {address}. Estado: ACTIVADO (price={price})")
            #Fase2.2.3: Guardamos la referencia del socket para envíos síncronos (autorización/órdenes)
            with active_cp_lock:
                #¿Por qué guardar el socket?
                # Central necesita comunicarse con CP más tarde
                # Ejemplo: Operador escribe "P MAD-01" → Parar MAD-01
                # 
                # active_cp_sockets['MAD-01'].sendall(b"PARAR#CENTRAL")
                active_cp_sockets[cp_id] = client_socket 
            
            
            
            #Fase 3: Bucle de Escucha de mensajes del CP
            while True:
                #**¿Qué puede recibir mientras está conectado?**
                # `FAULT#MAD-01` → Reporte de avería
                # `RECOVER#MAD-01` → CP recuperado
                # `ACK#COMANDO` → Confirmación de comando
                # `NACK#COMANDO` → Rechazo de comando
                data = client_socket.recv(1024)
                #Si el socket se cierra, rompe el bucle
                if not data:
                    break # Conexión cerrada por el cliente
                # Procesar mensajes de control/averías
                process_socket_data2(data, cp_id, address, client_socket, central_messages, KAFKA_BROKER)
                
        else:
            central_messages.append(f"ERROR: Mensaje de registro inválido de {address}. Cerrando conexión.")
            
    except Exception as e:
        central_messages.append(f"Error con el CP {cp_id} ({address}): {e}")
    finally:
        # FASE 4: Desconexión
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
    """Envía un comando (Parar/Reanudar) a un CP específico a través del socket síncrono.
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
        
        # 3. Enviamos el comando al CP
        # Usamos formato simple: "COMMAND#PARAMETRO". Ej: "PARAR#CENTRAL"
        message = f"{command.upper()}#CENTRAL".encode('utf-8') 
        print(f"[CENTRAL]  Enviando comando {command} a CP {cp_id}...")
        socket_ref.sendall(message)
       
        # 4. Registramos la acción en los logs
        central_messages.append(f" Comando '{command}' enviado a CP {cp_id}. Esperando ACK/NACK...")
        
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
        


# Funcion para procesar los comandos de la interfaz de CENTRAL
def process_user_input(central_messages):
    """Maneja los comandos de la interfaz de CENTRAL (punto 13 de la mecánica)."""
    while True:
        try:
            # Mostrar prompt con comandos disponibles
            print("\nComandos disponibles:")
            print("  P <CP_ID>  o  PARAR <CP_ID>    - Parar un CP específico")
            print("  R <CP_ID>  o  REANUDAR <CP_ID> - Reanudar un CP específico")
            print("  PT o PARAR_TODOS               - Parar todos los CPs")
            print("  RT o REANUDAR_TODOS            - Reanudar todos los CPs")
            print("  Q  o  QUIT                     - Salir")
            
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
                    print(f"\n[CENTRAL] Iniciando comando PARAR para CP {cp_id}...")
                    central_messages.append(f" Iniciando comando PARAR para CP {cp_id}...")
                    send_cp_command(cp_id, 'PARAR', central_messages)
                else:
                    print("\n[CENTRAL]  Error: Uso correcto es: P <CP_ID> o PARAR <CP_ID>")
                    central_messages.append(" Error: Uso correcto es: P <CP_ID> o PARAR <CP_ID>")
            
            elif command in ['R', 'REANUDAR']:
                if len(parts) == 2:
                    cp_id = parts[1]
                    print(f"\n[CENTRAL]  Iniciando comando REANUDAR para CP {cp_id}...")
                    central_messages.append(f" Iniciando comando REANUDAR para CP {cp_id}...")
                    send_cp_command(cp_id, 'REANUDAR', central_messages)
                else:
                    print("\n[CENTRAL]  Error: Uso correcto es: R <CP_ID> o REANUDAR <CP_ID>")
                    central_messages.append(" Error: Uso correcto es: R <CP_ID> o REANUDAR <CP_ID>")
            
            # --- Comandos para TODOS los CPs ---
            elif command in ['PA', 'PT', 'PARAR_TODOS']:
                print("\n[CENTRAL]  Enviando comando PARAR a todos los CPs conectados...")
                central_messages.append(" Iniciando comando PARAR para TODOS los CPs...")
                with active_cp_lock:
                    for cp_id in list(active_cp_sockets.keys()):
                        send_cp_command(cp_id, 'PARAR', central_messages)
            
            elif command in ['RA', 'RT', 'REANUDAR_TODOS']:
                print("\n[CENTRAL]  Enviando comando REANUDAR a todos los CPs conectados...")
                central_messages.append(" Iniciando comando REANUDAR para TODOS los CPs...")
                with active_cp_lock:
                    for cp_id in list(active_cp_sockets.keys()):
                        send_cp_command(cp_id, 'REANUDAR', central_messages)
            
            # Comando desconocido
            else:
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
        
        # Paso 3: Usaremos listas compartidas para que los hilos se comuniquen con el panel
        central_messages = ["CENTRAL system status OK"] #Lo que muestra el panel de estado (display_panel)
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