# Fichero: ev_driver.py (Aplicación del Conductor - Módulo Cliente Final)
import sys
import time
import json
from kafka import KafkaConsumer, KafkaProducer
import threading
import os

# --- Configuración (Debe coincidir con ev_central.py) ---
KAFKA_TOPIC_REQUESTS = 'driver_requests'
KAFKA_TOPIC_NOTIFY = 'driver_notifications'
CLIENT_ID = "" # Se asigna desde los argumentos

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
            
            # NOTA: Aunque la Central no envía actualmente el user_id, 
            # esta lógica de filtrado sería necesaria en una implementación final
            # if payload.get('user_id') == client_id:
            
            if payload.get('type') == 'AUTH_OK':
                messages.append(f"🟢 [AUTORIZADO] Recarga autorizada en CP {payload['cp_id']}.")
                
            elif payload.get('type') == 'AUTH_DENIED':
                messages.append(f"🔴 [DENEGADO] Recarga RECHAZADA en CP {payload['cp_id']}. Razón: {payload.get('reason', 'CP no disponible')}")
                
            elif payload.get('type') == 'TICKET':
                messages.append(f"🧾 [TICKET] Recarga finalizada en CP {payload['cp_id']}. Consumo: {payload['kwh']} kWh. Coste final: {payload['importe']} €")
            
            else:
                messages.append(f"[MENSAJE CENTRAL] {payload.get('message', 'Mensaje desconocido')}")
        
        except Exception as e:
            messages.append(f"[ERROR] Procesando notificación: {e}")

def display_driver_panel(messages):
    """Muestra el panel de mensajes del conductor de forma continua."""
    while True:
        clear_screen()
        print(f"--- EV DRIVER APP: CLIENTE {CLIENT_ID} ---")
        print("="*50)
        print("ESTADO: Listo para solicitar recarga.")
        print("COMANDOS: SOLICITAR <CP_ID> | [Q]UIT")
        print("="*50)
        print("\n*** LOG DE COMUNICACIONES ***")
        for msg in messages:
            print(msg)
        time.sleep(1)

def start_driver_interactive_logic(producer, messages):
    """
    Lógica interactiva del conductor. Lee comandos y envía solicitudes a Kafka.
    """
    messages.append("Modo interactivo activo. Escribe 'SOLICITAR <CP_ID>' para pedir recarga.")
    
    while True:
        try:
            # El input() se ejecuta en el hilo principal
            command_line = input("DRIVER> ").strip().upper()
            
            if command_line == 'QUIT' or command_line == 'Q':
                # Esto detiene la ejecución del hilo principal y, por tanto, del programa.
                raise KeyboardInterrupt 
            
            parts = command_line.split()
            command = parts[0]
            
            if command == 'SOLICITAR' and len(parts) == 2:
                cp_id = parts[1]
                
                # 1. Construir el mensaje de solicitud
                request_message = {
                    "user_id": CLIENT_ID,
                    "cp_id": cp_id,
                    "timestamp": time.time()
                }
                
                # 2. Enviar a Kafka (Topic: driver_requests)
                producer.send(KAFKA_TOPIC_REQUESTS, value=request_message)
                producer.flush()
                messages.append(f"-> Petición enviada a Central para CP {cp_id}. Esperando autorización...")
                
            else:
                messages.append("Comando inválido. Uso: SOLICITAR <CP_ID>")
                
        except EOFError:
            time.sleep(0.1) 
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
    driver_messages = [f"Driver {CLIENT_ID} iniciado.", f"Broker: {KAFKA_BROKER}"]

    try:
        # 1. Inicializar el Productor Kafka
        kafka_producer = KafkaProducer(
            bootstrap_servers=[KAFKA_BROKER],
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        
        # 2. Iniciar el Consumidor de Notificaciones en un hilo
        notify_thread = threading.Thread(
            target=process_central_notifications, 
            args=(KAFKA_BROKER, CLIENT_ID, driver_messages), 
            daemon=True
        )
        notify_thread.start()

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