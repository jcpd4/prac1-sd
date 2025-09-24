# Fichero: ev_driver.py
import sys
import time
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable

def create_producer(kafka_server):
    """Intenta crear un Productor de Kafka, reintentando si el bróker no está disponible."""
    while True:
        try:
            producer = KafkaProducer(
                bootstrap_servers=kafka_server,
                value_serializer=lambda v: str(v).encode('utf-8') # Serializa los mensajes a bytes
            )
            print("Productor de Kafka conectado exitosamente.")
            return producer
        except NoBrokersAvailable:
            print(f"No se puede conectar a Kafka en {kafka_server}. Reintentando en 5 segundos...")
            time.sleep(5)

def send_request(producer, topic, driver_id, cp_id):
    """Envía un único mensaje de solicitud de recarga a Kafka."""
    message = f"REQUEST#{driver_id}#{cp_id}"
    try:
        producer.send(topic, message)
        producer.flush() # Asegura que el mensaje se envía inmediatamente
        print(f"[{driver_id}] -> Solicitud de recarga enviada para el CP '{cp_id}'. Mensaje: '{message}'")
    except Exception as e:
        print(f"Error al enviar mensaje a Kafka: {e}")

if __name__ == "__main__":
    # --- Comprobación de los parámetros de entrada ---
    if len(sys.argv) != 3:
        print("Uso: py ev_driver.py <IP:PUERTO_KAFKA> <ID_CLIENTE>")
        print("Ejemplo: py ev_driver.py localhost:9092 Driver-Juan")
        sys.exit(1)

    KAFKA_SERVER = sys.argv[1]
    DRIVER_ID = sys.argv[2]
    REQUESTS_FILE = 'requests.txt'
    TOPIC_NAME = 'charge_requests'

    # --- Leer las solicitudes del fichero ---
    try:
        with open(REQUESTS_FILE, 'r') as f:
            charge_points = [line.strip() for line in f if line.strip()]
        if not charge_points:
            print(f"El fichero '{REQUESTS_FILE}' está vacío o no existe.")
            sys.exit(1)
    except FileNotFoundError:
        print(f"Error: No se encuentra el fichero de solicitudes '{REQUESTS_FILE}'.")
        sys.exit(1)

    # --- Crear productor y enviar mensajes ---
    kafka_producer = create_producer(KAFKA_SERVER)
    
    print(f"\nConductor '{DRIVER_ID}' iniciando secuencia de {len(charge_points)} recargas...")
    for cp in charge_points:
        send_request(kafka_producer, TOPIC_NAME, DRIVER_ID, cp)
        
        # La práctica indica esperar 4 segundos entre una solicitud y la siguiente
        # para simular el tiempo entre recargas.
        print("Esperando 4 segundos para la siguiente solicitud...")
        time.sleep(4)
    
    print("\nTodas las solicitudes de recarga han sido enviadas.")
    kafka_producer.close()