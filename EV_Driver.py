# Fichero: ev_driver.py (Aplicación del Conductor - Módulo Cliente Final)
import sys
import time
import json
from kafka import KafkaConsumer, KafkaProducer
import threading
import os
from collections import deque

# --- Configuración (Debe coincidir con ev_central.py) ---
KAFKA_TOPIC_REQUESTS = 'driver_requests'
KAFKA_TOPIC_NOTIFY = 'driver_notifications'
CLIENT_ID = "" # Se asigna desde los argumentos

# <<<--- CORRECCIÓN AQUÍ: Faltaban estas líneas ---<<<(11)
KAFKA_TOPIC_NETWORK_STATUS = 'network_status'
network_status = {}
network_status_lock = threading.Lock()
# >>>----------------------------------------------->>>

# === INICIO CAMBIO 1: Almacenamiento del estado de la recarga ===
# Usaremos un diccionario para guardar la información de la recarga activa
active_charge_info = {}
charge_lock = threading.Lock()
# === FIN CAMBIO 1 ===

def clear_screen():
    """Limpia la pantalla de la terminal."""
    os.system('cls' if os.name == 'nt' else 'clear')

def process_central_notifications(kafka_broker, client_id, messages):
    """Consumidor Kafka: Recibe notificaciones de la Central (autorización/ticket)."""
    try:
        # El consumidor debe escuchar el topic de notificaciones
        consumer = KafkaConsumer(
            KAFKA_TOPIC_NOTIFY,
            bootstrap_servers=[kafka_broker],
            auto_offset_reset='latest',
            group_id=f'driver-{client_id}-notifications', 
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )
        messages.append(f"[NOTIFICACIÓN] Conectado a Kafka para recibir respuestas.")
    except Exception as e:
        messages.append(f"[ERROR KAFKA] No se pudo conectar al consumidor: {e}")
        return

    # Bucle principal de consumo
    for message in consumer:
        try:
            payload = message.value
            msg_type = payload.get('type')
            # === ESTE ES EL FILTRO INTELIGENTE Y CORRECTO ===(11)
            # Los mensajes de autorización/denegación DEBEN ser para nuestro user_id.
            if msg_type in ['AUTH_OK', 'AUTH_DENIED']:
                if payload.get('user_id') != client_id:
                    continue # Ignorar si la autorización no es para mí.

            # Los mensajes de consumo/ticket DEBEN ser de un CP en el que estemos cargando activamente.
            elif msg_type in ['CONSUMO_UPDATE', 'TICKET']:
                cp_id_del_mensaje = payload.get('cp_id')
                with charge_lock:
                    # Si el CP del mensaje no está en nuestra lista de recargas activas, lo ignoramos.
                    if cp_id_del_mensaje not in active_charge_info:
                        continue
            # === FIN DEL FILTRO INTELIGENTEl ===

            # === INICIO CAMBIO 2: Manejar nuevos tipos de mensajes ===
            with charge_lock:
                if payload.get('type') == 'AUTH_OK':
                    messages.append(f" [AUTORIZADO] Recarga autorizada en CP {payload['cp_id']}.")
                    # Preparamos el diccionario para recibir datos de consumo
                    active_charge_info[payload['cp_id']] = {'kwh': 0.0, 'importe': 0.0}
                    
                elif payload.get('type') == 'AUTH_DENIED':
                    messages.append(f" [DENEGADO] Recarga RECHAZADA en CP {payload['cp_id']}. Razón: {payload.get('reason', 'CP no disponible')}")
                
                elif payload.get('type') == 'CONSUMO_UPDATE':
                    # ¡NUEVO! Actualizamos los datos de la recarga activa
                    cp_id = payload['cp_id']
                    if cp_id in active_charge_info:
                        active_charge_info[cp_id]['kwh'] = payload['kwh']
                        active_charge_info[cp_id]['importe'] = payload['importe']

                elif payload.get('type') == 'TICKET':
                    messages.append(f" [TICKET] Recarga finalizada en CP {payload['cp_id']}. Consumo: {payload['kwh']} kWh. Coste final: {payload['importe']} €")
                    # Limpiamos la información de la recarga activa
                    if payload['cp_id'] in active_charge_info:
                        del active_charge_info[payload['cp_id']]
                
                else:
                    messages.append(f"[MENSAJE CENTRAL] {payload.get('message', 'Mensaje desconocido')}")
            # === FIN CAMBIO 2 ===        
        except Exception as e:
            messages.append(f"[ERROR] Procesando notificación: {e}")

# === AÑADIDO: Nueva función para procesar el estado de la red === (11)
def process_network_updates(kafka_broker):
    """Consumidor que escucha el estado general de la red de CPs."""
    try:
        consumer = KafkaConsumer(
            KAFKA_TOPIC_NETWORK_STATUS,
            bootstrap_servers=[kafka_broker],
            auto_offset_reset='latest',
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )
    except Exception:
        # No mostramos error para no ensuciar la consola del driver
        return

    for message in consumer:
        payload = message.value
        if payload.get('type') == 'NETWORK_STATUS_UPDATE':
            with network_status_lock:
                network_status.clear()
                for cp in payload.get('cps', []):
                    network_status[cp['id']] = {'status': cp['status'], 'location': cp['location']}
# ============================================================= 



# === MODIFICADO: El panel ahora incluye la lista de CPs disponibles ===(11)
def display_driver_panel(messages):
    """Muestra el panel de mensajes del conductor de forma continua."""
    while True:
        clear_screen()
        print(f"--- EV DRIVER APP: CLIENTE {CLIENT_ID} ---")
        print("="*50)
        
        # Estado de la recarga personal (esto ya lo tenías bien)
        with charge_lock:
            if not active_charge_info:
                print("ESTADO: Listo para solicitar recarga.")
            else:
                for cp_id, data in active_charge_info.items():
                    print(f"ESTADO: 🔋 Suministrando en {cp_id}...")
                    print(f"   Consumo: {data['kwh']:.3f} kWh")
                    print(f"   Coste actual: {data['importe']:.2f} €")
        
        # --- PUNTOS DE RECARGA DISPONIBLES ---
        print("\n--- PUNTOS DE RECARGA DISPONIBLES ---")
        with network_status_lock:
            # Filtramos para mostrar solo los que están 'ACTIVADO'
            available_cps = {cp_id: data for cp_id, data in network_status.items() if data['status'] == 'ACTIVADO'}
            if not available_cps:
                print("Buscando puntos de recarga en la red...")
            else:
                for cp_id, data in available_cps.items():
                    print(f"  -> {cp_id:<10} ({data['location']})")

        print("="*50)
        print("COMANDOS: SOLICITAR <CP_ID> | [Q]UIT")
        print("\n*** LOG DE COMUNICACIONES (últimas) ***")
        for msg in list(messages):
            print(msg)
        time.sleep(1)
# ===================================================================

def start_driver_interactive_logic(producer, messages):
    """
    Lógica interactiva del conductor. Lee comandos y envía solicitudes a Kafka.
    """
    messages.append("Modo interactivo activo. Escribe 'SOLICITAR <CP_ID>' para pedir recarga.")
    
    while True:
        try:
            # Solo una lectura de input()
            command_line = input("DRIVER> ").strip()
            if not command_line:
                continue
            if command_line.upper() in ('QUIT', 'Q'):
                raise KeyboardInterrupt 

            parts = command_line.split()
            command = parts[0].upper() if parts else ""

            if command == 'SOLICITAR':
                if len(parts) != 2:
                    messages.append("Uso: SOLICITAR <CP_ID>")
                    continue
                cp_id = parts[1]

                request_message = {
                    "user_id": CLIENT_ID,
                    "cp_id": cp_id,
                    "timestamp": time.time()
                }

                try:
                    fut = producer.send(KAFKA_TOPIC_REQUESTS, value=request_message)
                    fut.add_callback(lambda rec, rm=request_message: print(f"[DRIVER] enviado -> {rm} (ack={rec})"))
                    fut.add_errback(lambda exc, rm=request_message: print(f"[DRIVER] fallo al enviar -> {rm} : {exc}"))
                    messages.append(f"-> Petición enviada a Central para CP {cp_id}. Esperando autorización...")
                except Exception as e:
                    messages.append(f"[ERROR KAFKA] No se pudo enviar la petición: {e}")
                    print(f"[DRIVER] fallo al enviar -> {request_message} : {e}")

            # Envío por lotes desde un archivo: cada línea contiene un ID de CP
            elif command == 'BATCH' and len(parts) == 2:
                file_path = parts[1]
                try:
                    with open(file_path, 'r', encoding='utf-8') as fh:
                        lines = [l.strip() for l in fh.readlines()]
                except Exception as e:
                    messages.append(f"[ERROR] No se pudo leer el archivo de batch: {e}")
                    continue

                messages.append(f"Iniciando envíos en lote desde {file_path} ({len(lines)} entradas)")
                print(f"[DRIVER][BATCH] Iniciando envíos desde {file_path} ({len(lines)} entradas)")
                for line in lines:
                    if not line:
                        continue
                    cp_id = line
                    request_message = {
                        "user_id": CLIENT_ID,
                        "cp_id": cp_id,
                        "timestamp": time.time()
                    }
                    try:
                        fut = producer.send(KAFKA_TOPIC_REQUESTS, value=request_message)
                        fut.add_callback(lambda rec, rm=request_message: print(f"[DRIVER][BATCH] enviado -> {rm} (ack={rec})"))
                        fut.add_errback(lambda exc, rm=request_message: print(f"[DRIVER][BATCH] fallo -> {rm} : {exc}"))
                        messages.append(f"-> (BATCH) Petición enviada a Central para CP {cp_id}")
                    except Exception as e:
                        messages.append(f"[ERROR KAFKA] No se pudo enviar la petición (CP {cp_id}): {e}")
                        print(f"[DRIVER] fallo al enviar (BATCH) -> {request_message} : {e}")
                    # pequeño retardo para no saturar
                    time.sleep(0.3)

            else:
                messages.append("Comando inválido. Uso: SOLICITAR <CP_ID> o BATCH <FILE>")

        except EOFError:
            time.sleep(0.1)
        except KeyboardInterrupt:
            # Propagar para que main capture y cierre limpio
            raise
        except Exception as e:
            messages.append(f"Error en el procesamiento de comandos del Driver: {e}")


# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Uso: py ev_driver.py <kafka_broker_ip:port> <ID_CLIENTE>")
        print(f"Ejemplo: py ev_driver.py localhost:9092 101")
        sys.exit(1)

    KAFKA_BROKER = sys.argv[1]
    CLIENT_ID = sys.argv[2]
    
    # Lista compartida para los logs y notificaciones
    driver_messages = deque(maxlen=200)
    driver_messages.append(f"Driver {CLIENT_ID} iniciado.")
    driver_messages.append(f"Broker: {KAFKA_BROKER}")

    try:
        # 1. Inicializar el Productor Kafka
        try:
            # Configure producer for lower latency: acks=1, small linger_ms, limited retries
            kafka_producer = KafkaProducer(
                bootstrap_servers=[KAFKA_BROKER],
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                acks=1,
                linger_ms=5,
                retries=2
            )
            driver_messages.append(f"[KAFKA] Producer inicializado en {KAFKA_BROKER}")
        except Exception as e:
            driver_messages.append(f"[KAFKA-ERROR] No se pudo inicializar producer: {e}")
            raise
        
        # 2. Iniciar el Consumidor de Notificaciones en un hilo
        notify_thread = threading.Thread(
            target=process_central_notifications, 
            args=(KAFKA_BROKER, CLIENT_ID, driver_messages), 
            daemon=True
        )
        notify_thread.start()

        # --- AÑADIDO: Iniciar el nuevo hilo para el estado de la red ---(11)
        network_thread = threading.Thread(
            target=process_network_updates,
            args=(KAFKA_BROKER,),
            daemon=True
        )
        network_thread.start()
        # -----------------------------------------------------------

        # 3. Iniciar el Panel de Visualización en un hilo
        panel_thread = threading.Thread(
            target=display_driver_panel, 
            args=(driver_messages,),
            daemon=True
        )
        panel_thread.start()
        
        # 4. El Hilo Principal se dedica a la lógica interactiva (input)
        start_driver_interactive_logic(kafka_producer, driver_messages)

    except KeyboardInterrupt:
        print("\nDriver detenido por el usuario.")
        sys.exit(0)
    except Exception as e:
        print(f"Error fatal: {e}")
        sys.exit(1)