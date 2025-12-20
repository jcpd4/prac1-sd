import sys
import time
import json
from kafka import KafkaConsumer, KafkaProducer
import threading
import os
import socket
from collections import deque
import requests

KAFKA_TOPIC_REQUESTS = 'driver_requests' 
KAFKA_TOPIC_NOTIFY = 'driver_notifications' 
CLIENT_ID = "" 
KAFKA_TOPIC_NETWORK_STATUS = 'network_status' 

network_status = {} 
network_status_lock = threading.Lock() 
active_charge_info = {} 
charge_lock = threading.Lock() 
last_supply_errors = {}  
# --- Funciones ---
def get_network_config():
    try:
        with open('network_config.json', 'r') as f:
            return json.load(f)
    except:
        return {}

def clear_screen():
    """Limpia la pantalla de la terminal."""
    os.system('cls' if os.name == 'nt' else 'clear')

def colorize_status(status):
    """Devuelve el estado coloreado similar al panel de la Central."""
    colors = {
        'ACTIVADO': '\033[92m',       # Verde
        'DESCONECTADO': '\033[90m',  # Gris
        'SUMINISTRANDO': '\033[94m', # Azul
        'AVERIADO': '\033[91m',      # Rojo
        'FUERA_DE_SERVICIO': '\033[38;5;208m', # Naranja
    }
    end = '\033[0m'
    return f"{colors.get(status,'')}{status}{end}"

def log_to_web(msg):
    """Envía el log a la API para que salga en el frontend."""
    try:
        config = get_network_config()
        ip = config.get('central_ip', '127.0.0.1')
        port = config.get('central_port', 5000)
        url = f"http://{ip}:{port}/api/log"
        requests.post(url, json={"source": "DRIVER", "msg": msg}, timeout=0.1)
    except:
        pass 

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)) 
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

# HILO 1: Funcion Kafka que porcesa las notificaciones de la central 
def process_central_notifications(kafka_broker, client_id, messages):
    """Consumidor Kafka: Recibe notificaciones de la Central (autorización/ticket)."""
    try:
        #Paso 1: Conectar al consumidor Kafka
        consumer = KafkaConsumer(
            KAFKA_TOPIC_NOTIFY,
            bootstrap_servers=[kafka_broker],
            auto_offset_reset='latest',
            group_id=f'driver-{client_id}-notifications', 
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )
        #Paso 1.1: Agregar mensaje de éxito a la lista de mensajes
        messages.append(f"[NOTIFICACIÓN] Conectado a Kafka para recibir respuestas.")
        #Paso 1.2: Manejar errores
    except Exception as e:
        messages.append(f"[ERROR KAFKA] No se pudo conectar al consumidor: {e}")
        return

    #Paso 2: Bucle principal de notificaciones de la central
    for message in consumer:
        try:
            payload = message.value
            #Paso 2.1: Obtener el tipo de mensaje
            msg_type = payload.get('type')
            
            #Paso 2.2: Filtrar por destinatario cuando aplica
            if msg_type in ['AUTH_OK', 'AUTH_DENIED', 'SESSION_CANCELLED', 'SUPPLY_ERROR']:
                target_user = payload.get('user_id') or payload.get('driver_id')
                if target_user and target_user != client_id:
                    continue
            #Paso 2.3: Filtrar SÓLO los mensajes de consumo
            if msg_type == 'CONSUMO_UPDATE':
                cp_id_del_mensaje = payload.get('cp_id')
                with charge_lock:
                    if cp_id_del_mensaje not in active_charge_info:
                        continue
            
            elif msg_type in ['TICKET', 'SUPPLY_ERROR']:
                target_user = payload.get('user_id') or payload.get('driver_id')
                
                if target_user and target_user != client_id:
                    continue 
                
                pass 
            
            #Paso 2.4: Procesar los mensajes de autorización, consumo, ticket y supply error
            with charge_lock:
                if msg_type == 'AUTH_OK':
                    messages.append(f" [AUTORIZADO] Recarga autorizada en CP {payload['cp_id']}.")
                    active_charge_info[payload['cp_id']] = {'kwh': 0.0, 'importe': 0.0}
                    log_to_web(f"[{CLIENT_ID}] ✅ Autorizado en {payload['cp_id']}") # <--- AÑADIR
                    #Paso 2.4.1: Procesar los mensajes de autorización
                elif msg_type == 'AUTH_DENIED':
                    messages.append(f" [DENEGADO] Recarga RECHAZADA en CP {payload['cp_id']}. Razón: {payload.get('reason', 'CP no disponible')}")
                    log_to_web(f"[{CLIENT_ID}] ⛔ Denegado en {payload['cp_id']}: {payload.get('reason')}") # <--- AÑADIR
                #Paso 2.4.2: Procesar los mensajes de consumo
                elif msg_type == 'CONSUMO_UPDATE':
                    cp_id = payload['cp_id']
                    if cp_id in active_charge_info:
                        active_charge_info[cp_id]['kwh'] = payload['kwh']
                        active_charge_info[cp_id]['importe'] = payload['importe']

                #Paso 2.4.3: Procesar los mensajes de ticket
                elif msg_type == 'TICKET':
                    messages.append(f" [TICKET] Recarga finalizada en CP {payload['cp_id']}. Consumo: {payload['kwh']} kWh. Coste final: {payload['importe']} €")
                    if payload['cp_id'] in active_charge_info:
                        del active_charge_info[payload['cp_id']]
                    log_to_web(f"[{CLIENT_ID}] 🎫 TICKET: {payload['kwh']} kWh / {payload['importe']}€") # <--- AÑADIR
                
                #Paso 2.4.4: Procesar los mensajes de supply error
                elif msg_type == 'SUPPLY_ERROR':
                    reason = payload.get('reason', 'Carga interrumpida')
                    kwh_p = payload.get('kwh_partial', 0)
                    imp_p = payload.get('importe_partial', 0)
                    cp_id = payload.get('cp_id', 'N/A')

                    if last_supply_errors.get(cp_id) == (kwh_p, imp_p):
                        continue
                    
                    msg_error = f"[ERROR SUMINISTRO] {reason}. Parcial: {kwh_p} kWh / {imp_p} € en CP {cp_id}"
                    try:
                        for idx in range(len(messages) - 1, -1, -1):
                            if " [ERROR SUMINISTRO]" in messages[idx] and f"CP {cp_id}" in messages[idx]:
                                del messages[idx]
                                break
                    except Exception:
                        pass

                    messages.append(f" {msg_error}")
                    last_supply_errors[cp_id] = (kwh_p, imp_p)
                    
                    # Paso 2.4.4.1: Limpiar la recarga activa
                    if cp_id in active_charge_info:
                        del active_charge_info[cp_id]
                
                    log_to_web(f"[{CLIENT_ID}] ❌ {msg_error}")

                elif msg_type == 'SESSION_CANCELLED':
                    continue
        except Exception as e:
            messages.append(f"[ERROR] Procesando notificación: {e}")



# HILO 2: Función Kafka para procesar el estado de la red (11)
def process_network_updates(kafka_broker):
    """Consumidor que escucha el estado general de la red de CPs."""
    try:
        #Paso 1: Conectar al consumidor Kafka
        consumer = KafkaConsumer(
            KAFKA_TOPIC_NETWORK_STATUS, 
            bootstrap_servers=[kafka_broker],
            auto_offset_reset='latest',
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )
    except Exception:
        #Paso 1.1: Manejar errores
        return

    #Paso 2: Bucle principal de actualizaciones de la red
    for message in consumer:
        #Paso 2.1: Obtener el mensaje
        payload = message.value
        #Paso 2.2: Filtrar los mensajes de estado de la red
        if payload.get('type') == 'NETWORK_STATUS_UPDATE':
            #Paso 2.2.1: Limpiar el estado de la red
            with network_status_lock: 
                network_status.clear()
                for cp in payload.get('cps', []):
                    network_status[cp['id']] = {'status': cp['status'], 'location': cp['location']}



# HILO 3: Función para mostrar el panel del conductor (11)
def display_driver_panel(messages):
    """Muestra el panel de la app del Driver con un diseño similar a Central/Monitor."""
    icons = {
        'ACTIVADO': '✓',
        'DESCONECTADO': '·',
        'SUMINISTRANDO': '⚡',
        'AVERIADO': '✗',
        'FUERA_DE_SERVICIO': '⏸',
    }
    while True:
        clear_screen()
        print("--- EV DRIVER APP ---")
        print(f"Cliente: {CLIENT_ID}")
        print("="*80)

        print("*** ESTADO DEL CLIENTE ***")
        with charge_lock:
            if not active_charge_info:
                print("  Listo para solicitar recarga.")
            else:
                for cp_id, data in active_charge_info.items():
                    print(f"  Estado: Suministrando en {cp_id}")
                    print(f"    Consumo: {data['kwh']:.3f} kWh    Importe: {data['importe']:.2f} €")

        print("-"*80)
        print("*** ESTADO DE LA RED (vista rápida) ***")
        with network_status_lock:
            if not network_status:
                print("  Obteniendo estado de la red…")
            else:
                counts = {'ACTIVADO':0,'DESCONECTADO':0,'SUMINISTRANDO':0,'AVERIADO':0,'FUERA_DE_SERVICIO':0}
                for _, data in network_status.items():
                    st = data.get('status','DESCONECTADO')
                    if st in counts: counts[st] += 1
                print("  Disponibles para pedir:")
                for cp_id, data in network_status.items():
                    st = data['status']
                    if st == 'ACTIVADO':
                        print(f"     {cp_id:<10} | {data['location']} | {colorize_status(st)}")

        print("-"*80)
        print("*** COMANDOS ***")
        print("  SOLICITAR <CP_ID>    Realiza una petición de recarga en el CP indicado")
        print("  BATCH <ruta.txt>     Envía múltiples peticiones (una por línea)")
        print("  Q/QUIT               Salir")

        print("-"*80)
        print("*** MENSAJES (últimos 7) ***")
        for msg in list(messages)[-7:]:
            print(f"  {msg}")

        print("="*80)
        time.sleep(1)


# HILO 4: Función para la lógica interactiva del conductor
def start_driver_interactive_logic(producer, messages):
    """
    Lógica interactiva del conductor. Lee comandos y envía solicitudes a Kafka.
    """
    #Paso 1: Agregar mensaje de inicio a la lista de mensajes
    messages.append("Modo interactivo activo. Escribe 'SOLICITAR <CP_ID>' o 'BATCH <fichero>'")
    #Paso 2: Bucle principal de la lógica interactiva
    while True:
        try:
            #Paso 2.1: Leer el comando del usuario
            command_line = input("DRIVER> ").strip()
            #Paso 2.2: Filtrar los comandos
            if not command_line:
                continue
            #Paso 2.2.1: Filtrar los comandos de salida
            if command_line.upper() in ('QUIT', 'Q'):
                raise KeyboardInterrupt 
            
            parts = command_line.split()
            command = parts[0].upper() if parts else ""
            #Paso 2.2.3: Filtrar los comandos de recarga
            if command == 'SOLICITAR':
                if len(parts) != 2:
                    messages.append("Uso: SOLICITAR <CP_ID>")
                    continue
                cp_id = parts[1]
                request_message = { "user_id": CLIENT_ID, "cp_id": cp_id, "timestamp": time.time(), "source_ip": get_local_ip() }
                try:
                    producer.send(KAFKA_TOPIC_REQUESTS, value=request_message)
                    messages.append(f"-> Petición enviada a Central para CP {cp_id}. Esperando autorización...")
                    log_to_web(f"[{CLIENT_ID}] 📤 Solicitando carga en {cp_id}") 

                except Exception as e:
                    messages.append(f"[ERROR KAFKA] No se pudo enviar la petición: {e}")

            #Paso 2.2.4: Filtrar los comandos de batch
            elif command == 'BATCH' and len(parts) == 2:
                file_path = parts[1]
                #Paso 2.2.4.1: Leer el fichero de recarga
                try:
                    with open(file_path, 'r') as fh:
                        cps_to_request = [line.strip() for line in fh if line.strip()]
                except Exception as e:
                    messages.append(f"[ERROR] No se pudo leer el fichero: {e}")
                    continue

                #Paso 2.2.4.2: Iniciar el proceso BATCH
                messages.append(f"Iniciando proceso BATCH desde '{file_path}'...")
                #Paso 2.2.4.3: Bucle principal de la lógica BATCH
                for i, cp_id in enumerate(cps_to_request):
                    messages.append(f"BATCH ({i+1}/{len(cps_to_request)}): Solicitando recarga en {cp_id}")
                    
                    #Paso 2.2.4.3.1: Enviar la petición de recarga
                    request_message = { "user_id": CLIENT_ID, "cp_id": cp_id, "timestamp": time.time(), "source_ip": get_local_ip() }
                    producer.send(KAFKA_TOPIC_REQUESTS, value=request_message)
                    
                    #Paso 2.2.4.3.2: Esperar a que la recarga sea autorizada y comience
                    time.sleep(5) # Damos un margen para que llegue la autorización

                    #Paso 2.2.4.3.3: Bucle de espera: se queda aquí hasta que la recarga termine
                    #    La recarga termina cuando `active_charge_info` se vacía (tras TICKET o ERROR)
                    messages.append(f"Esperando a que la recarga en {cp_id} concluya...")
                    while True:
                        #Paso 2.2.4.3.3.1: Esperar a que la recarga termine
                        with charge_lock:
                            if not active_charge_info:
                                break # La recarga ha terminado, salimos del bucle de espera
                        time.sleep(1)
                    
                    messages.append(f"Recarga en {cp_id} concluida. Esperando 4 segundos...")
                    time.sleep(4) #Paso 2.2.4.3.3.2: Espera de 4 segundos entre recargas como pide la práctica

                messages.append("Proceso BATCH finalizado.")

            else:
                messages.append("Comando inválido.")

        except (EOFError, KeyboardInterrupt):
            raise
        except Exception as e:
            messages.append(f"Error en el procesamiento de comandos del Driver: {e}")

# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    #Paso 1: Obtener los argumentos de la línea de comandos
    if len(sys.argv) != 3:
        print(f"Uso: py ev_driver.py <kafka_broker_ip:port> <ID_CLIENTE>")
        print(f"Ejemplo: py ev_driver.py localhost:9092 101")
        sys.exit(1)

    #Paso 2: Extraer los argumentos
    KAFKA_BROKER = sys.argv[1]
    CLIENT_ID = sys.argv[2]
    config = get_network_config()
    k_ip = config.get('kafka_ip')
    k_port = config.get('kafka_port')
    
    if k_ip and k_port:
        KAFKA_BROKER = f"{k_ip}:{k_port}"
        print(f"[INIT] 🟢 Driver usando Kafka del JSON: {KAFKA_BROKER}")

    #Paso 3: Inicializar la lista compartida para los logs y notificaciones
    driver_messages = deque(maxlen=200)
    driver_messages.append(f"Driver {CLIENT_ID} iniciado.")
    log_to_web(f"[{CLIENT_ID}] 🚗 Driver iniciado y conectado a Kafka.") 
    driver_messages.append(f"Broker: {KAFKA_BROKER}")

    try:
        #Paso 4: Inicializar el Productor Kafka
        try:
            #Paso 4.1: Configurar el productor Kafka
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
        

        #Paso 5: Iniciar los hilos
        notify_thread = threading.Thread(
            target=process_central_notifications, 
            args=(KAFKA_BROKER, CLIENT_ID, driver_messages), 
            daemon=True
        )
        notify_thread.start()

        #Paso 5.2: Iniciar el hilo para el estado de la red (11)
        network_thread = threading.Thread(
            target=process_network_updates,
            args=(KAFKA_BROKER,),
            daemon=True
        )
        network_thread.start()
        
        #Paso 5.3: Iniciar el Panel de Visualización en un hilo
        panel_thread = threading.Thread(
            target=display_driver_panel, 
            args=(driver_messages,),
            daemon=True
        )
        panel_thread.start()

        #Paso 5.4: Iniciar el Hilo Principal se dedica a la lógica interactiva (input)
        start_driver_interactive_logic(kafka_producer, driver_messages)

    except KeyboardInterrupt:
        try:
            quit_msg = {"type": "DRIVER_QUIT", "user_id": CLIENT_ID, "timestamp": time.time(), "source_ip": get_local_ip()}
            kafka_producer.send(KAFKA_TOPIC_REQUESTS, value=quit_msg)
            kafka_producer.flush()
        except Exception:
            pass
        print("\nDriver detenido por el usuario.")
        sys.exit(0)
    except Exception as e:
        print(f"Error fatal: {e}")
        sys.exit(1)
