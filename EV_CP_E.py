import socket
import threading
import sys
import time
import os
import json
import database
from kafka import KafkaProducer # Usado para enviar telemetría a Central (asíncrono)
from kafka.errors import NoBrokersAvailable


# --- Variables de Estado del Engine ---
ENGINE_STATUS = {"health": "OK", "is_charging": False, "driver_id": None}
KAFKA_PRODUCER = None
CP_ID = ""
BROKER = None 
# Creamos un Lock global para proteger el acceso concurrente a ENGINE_STATUS.
# Un Lock (cerrojo) garantiza que solo un hilo a la vez puede modificar el estado compartido.
# Así evitamos condiciones de carrera si llegan comandos simultáneos (p. e.j, PARAR y REANUDAR al mismo tiempo).
status_lock = threading.Lock()

# Topic que espera la CENTRAL
KAFKA_TOPIC_TELEMETRY = "cp_telemetry"


# --- Kafka helper ---
def init_kafka_producer(broker):
    global KAFKA_PRODUCER
    if KAFKA_PRODUCER is None:
        try:
            KAFKA_PRODUCER = KafkaProducer(
                bootstrap_servers=[broker],
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
            )
            print(f"[ENGINE] Kafka producer conectado a {broker}")
        except NoBrokersAvailable:
            print(f"[ENGINE] WARNING: broker {broker} no disponible. Los mensajes se descartarán hasta reconexión.")
            # lanzar hilo de reintento si se desea (opcional)
            def _reconnect_loop(broker, interval=5):
                global KAFKA_PRODUCER
                while KAFKA_PRODUCER is None:
                    try:
                        tmp = KafkaProducer(
                            bootstrap_servers=[broker],
                            value_serializer=lambda v: json.dumps(v).encode('utf-8')
                        )
                        KAFKA_PRODUCER = tmp
                        print(f"[ENGINE] Reconectado a Kafka broker {broker}")
                        break
                    except Exception as e:
                        print(f"[ENGINE] Reconexión fallida a {broker}: {e} - reintentando en {interval}s")
                        time.sleep(interval)
            threading.Thread(target=_reconnect_loop, args=(broker,), daemon=True).start()
        except Exception as e:
            print(f"[ENGINE] ERROR inicializando Kafka producer: {e}")
            # intentar reconectar en background igual que arriba
            threading.Thread(target=lambda: time.sleep(1) or init_kafka_producer(broker), daemon=True).start()

def send_telemetry_message(payload):
    """Envía payload JSON al topic cp_telemetry. No lanza si producer no existe."""
    global KAFKA_PRODUCER
    # log local para depuración
    print(f"[ENGINE] Enviando telemetry -> {payload}")
    if KAFKA_PRODUCER is None:
        # Notar: descartamos el mensaje y seguimos; reconector puede restablecer el producer
        print(f"[ENGINE] AVISO: Kafka producer no inicializado. Mensaje descartado.")
        return
    try:
        KAFKA_PRODUCER.send(KAFKA_TOPIC_TELEMETRY, value=payload)
        KAFKA_PRODUCER.flush()
    except Exception as e:
        print(f"[ENGINE] Error enviando telemetry: {e}")

# --- Funciones de Utilidad ---
def clear_screen():
    """Limpia la pantalla de la terminal."""
    os.system('cls' if os.name == 'nt' else 'clear')

def display_status_loop():
    """Loop que mantiene el display actualizado cada segundo (para hilo)."""
    while True:
        with status_lock:
            health = ENGINE_STATUS.get('health', 'N/A')
            cp = CP_ID
        clear_screen()
        print(f"--- EV CHARGING POINT ENGINE: {cp} ---")
        print("="*50)
        print(f"  ESTADO DE SALUD: {health}")
        print("  Información de suministro: disponible en la CENTRAL (topic cp_telemetry)")
        print("="*50)
        print("Comandos: [F]AIL | [R]ECOVER | [I]NIT | [E]ND")
        print("-" * 50)
        try:
            print("\n> ", end="", flush=True)
        except Exception:
            pass
        time.sleep(1)


def display_status():
    """Muestra el estado actual del Engine en pantalla (única vez)."""
    clear_screen()
    print(f"--- EV CHARGING POINT ENGINE: {CP_ID} ---")
    print("="*50)
    print(f"  ESTADO DE SALUD: {ENGINE_STATUS['health']}")
    print("  Información de suministro: disponible en la CENTRAL (topic cp_telemetry)")
    print("="*50)
    print("Comandos: [F]AIL para simular AVERÍA | [R]ECOVER para recuperar")
    print("          [I]NIT para simular ENCHUFAR vehículo (Iniciar Suministro)")
    print("          [E]ND para simular DESENCHUFAR vehículo (Finalizar Suministro)")
    print("-" * 50)


# --- Funciones de Comunicación con Monitor---

#Espera mensajes del Monitor cada 1 segundo y le responde con OK/KO
def handle_monitor_connection(monitor_socket, monitor_address):
    """Maneja la conexión síncrona con el Monitor local (EC_CP_M)."""
    global ENGINE_STATUS
    
    try:
        #1. Recibimos el mensaje enviado por el Monitor
        data = monitor_socket.recv(1024).decode('utf-8').strip()
        
        #2. Bloqueamos el acceso a ENGINE_STATUS mientras procesamos este comando.
        #   Esto impide que otro hilo lo modifique al mismo tiempo.
        with status_lock:
            
            #3. Ejecutamos uno de los comandos pasados desde el monitor
            # --- HEALTH CHECK ---
            if data.startswith("HEALTH_CHECK"):
                # Si estamos en modo KO, respondemos KO. De lo contrario, OK.
                response = ENGINE_STATUS['health'] 
                monitor_socket.sendall(response.encode('utf-8'))

            # --- PARAR ---
            elif data.startswith("PARAR"):
                ENGINE_STATUS['is_charging'] = False # Detenemos la carga si estaba en curso
                monitor_socket.sendall(b"ACK#PARAR")   # Confirmamos acción
            
            # --- REANUDAR ---
            elif data.startswith("REANUDAR"):
                ENGINE_STATUS['health'] = 'OK'  # Simulamos que está listo para volver a cargar
                monitor_socket.sendall(b"ACK#REANUDAR")   # Confirmamos acción
            # --- COMANDO DESCONOCIDO ---
            else:
                monitor_socket.sendall(b"ERROR") # Comando no reconocido

        #4. El Lock se libera automáticamente al salir del bloque 'with'

    except Exception as e:
        # Esto ocurre si la conexión con el Monitor se pierde o hay un error de red
        print(f"\n[Engine] Conexión con Monitor local ({monitor_address}) perdida: {e}")
    finally:
        # Cerramos siempre el socket tras atender la petición
        monitor_socket.close()

#Levantamos el Servidor TCP local y su propósito es responder a los chequeos de salud del Monitor
def start_health_server(host, port):
    """Inicia el servidor de sockets que escucha los HEALTH_CHECK del Monitor."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
                      #Asocia el socket a una IP y puerto.
        server_socket.bind((host, port))
                      #Se pone a escuchar conexiones entrantes
        server_socket.listen(1) # Solo necesitamos una conexión (la del Monitor)
        print(f"[Engine] Servidor de salud local escuchando en {host}:{port}")
    #por si falla al hacer bind() o listen():
    except Exception as e:
        print(f"[Engine] ERROR: No se pudo iniciar el servidor de salud en {port}: {e}")
        sys.exit(1)
    
    
    while True:
        try:
            #monitor_socket: el socket activo para hablar con ese cliente.
            #address: la IP y puerto del cliente (Monitor).
            monitor_socket, address = server_socket.accept() #Espera una conexión del Monitor.
            #print(f"\n[Engine] Monitor local conectado desde {address}") # TO-DO: hacer para que salga 1 vez solo
            #Crea un nuevo hilo que va a ejecutar la función handle_monitor_connection(...)
            monitor_thread = threading.Thread(
                target=handle_monitor_connection, 
                      #el socket y la IP del Monitor como argumentos
                args=(monitor_socket, address)) 
            #este hilo se cerrará automáticamente cuando el programa principal termine
            monitor_thread.daemon = True
            #Inicia la ejecución del hilo: comienza a recibir y responder mensajes del Monitor.
            monitor_thread.start()
        #Permite cerrar el servidor de forma limpia si pulsamos Ctrl + C
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[Engine] Error aceptando conexión del Monitor: {e}")
            time.sleep(1)
            
# --- Funciones de Simulación de Lógica de Negocio (Suministro) ---

# --- Simulación de Suministro / Producción Kafka ---
def simulate_charging(cp_id, broker, driver_id, price_per_kwh=0.20, step_kwh=0.1):
    """
    Simula el suministro:
    - Inicializa producer si es necesario.
    - Envía mensajes CONSUMO cada segundo con campos: type, cp_id, user_id, kwh, importe
    - Al terminar (usuario), envía SUPPLY_END con totales.
    - price_per_kwh se usa para calcular importe (opcional; la CENTRAL leerá su precio desde BD).
    """
    init_kafka_producer(broker)
    total_kwh = 0.0
    total_importe = 0.0
    aborted_due_to_fault = False

    with status_lock:
        ENGINE_STATUS['is_charging'] = True
        ENGINE_STATUS['driver_id'] = driver_id

    print(f"[ENGINE] Inicio suministro CP={cp_id} driver={driver_id}")
    try:
        while True:
            with status_lock:
                if not ENGINE_STATUS['is_charging']:
                    break
                if ENGINE_STATUS['health'] != "OK":
                    # En caso de avería, notificar y salir
                    payload_fault = {
                        "type": "AVERIADO",
                        "cp_id": cp_id,
                        "user_id": driver_id,
                        "kwh": round(total_kwh, 3),
                        "importe": round(total_importe, 2)
                    }
                    send_telemetry_message(payload_fault)
                    aborted_due_to_fault = True
                    return

            # Incremento de ejemplo por segundo
            total_kwh += step_kwh
            total_importe = total_kwh * price_per_kwh

            payload = {
                "type": "CONSUMO",
                "cp_id": cp_id,
                "user_id": driver_id,
                "kwh": round(total_kwh, 3),
                "importe": round(total_importe, 2)
            }
            send_telemetry_message(payload)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
       # En caso de aborto por avería no enviamos SUPPLY_END; en caso normal sí.
        if not aborted_due_to_fault:
            payload_end = {
                "type": "SUPPLY_END",
                "cp_id": cp_id,
                "user_id": driver_id,
                "kwh": round(total_kwh, 3),
                "importe": round(total_importe, 2)
            }
            send_telemetry_message(payload_end)

        with status_lock:
            ENGINE_STATUS['is_charging'] = False
            ENGINE_STATUS['driver_id'] = None

        if aborted_due_to_fault:
            print(f"[ENGINE] SUMINISTRO abortado por AVERÍA CP={cp_id} driver={driver_id} kwh={total_kwh} importe={total_importe}")
        else:
            print(f"[ENGINE] FIN suministro CP={cp_id} driver={driver_id} kwh={total_kwh} importe={total_importe}")

# --- Funciones de control para simular fallos y finalizar ---
def stop_charging():
    with status_lock:
        ENGINE_STATUS['is_charging'] = False

def simulate_fault(cp_id, broker, driver_id):
    """Forzar avería: set health 'KO' y enviar mensaje AVERIADO / CONEXION_PERDIDA."""
    init_kafka_producer(broker)
    with status_lock:
        ENGINE_STATUS['health'] = "KO"
    payload = {
        "type": "AVERIADO",
        "cp_id": cp_id,
        "user_id": driver_id,
        "kwh": 0.0,
        "importe": 0.0
    }
    send_telemetry_message(payload)
    print(f"[ENGINE] Simulado AVERIA CP={cp_id}")

def simulate_connection_lost(cp_id, broker, driver_id):
    init_kafka_producer(broker)
    payload = {
        "type": "CONEXION_PERDIDA",
        "cp_id": cp_id,
        "user_id": driver_id,
        "kwh": 0.0,
        "importe": 0.0
    }
    send_telemetry_message(payload)
    print(f"[ENGINE] Simulado CONEXION_PERDIDA CP={cp_id}")




# --- Funciones de Entrada de Usuario (Simulación de Teclas) ---
def process_user_input():
    """Maneja la entrada del usuario para simular averías/eventos."""
    global ENGINE_STATUS
    while True:
        try:
            #input() espera a que el usuario escriba algo.
            #Mientras tanto, el hilo principal se detiene completamente
            #Nada más se ejecuta en ese hilo hasta que se pulse Enter.
            command = input("\n> ").strip().upper()
            
            if command == 'F' or command == 'FAIL':
                # Simular Avería (KO)
                if ENGINE_STATUS['health'] == 'OK':
                    #Cambiamos el estado de salud a "KO".
                    with status_lock:
                        ENGINE_STATUS['health'] = 'KO'
                    print("\n[Engine] *** ESTADO INTERNO: AVERÍA (KO) ***. El Monitor notificará a Central.")
                display_status()  # snapshot
                
            elif command == 'R' or command == 'RECOVER':
                # Simular Recuperación
                if ENGINE_STATUS['health'] == 'KO':
                    #Restablecemos el estado de salud a "OK"
                    with status_lock:
                        ENGINE_STATUS['health'] = 'OK'
                    print("\n[Engine] *** ESTADO INTERNO: RECUPERADO (OK) ***. El Monitor notificará a Central.")
                display_status()  # snapshot
                
            elif command == 'I' or command == 'INIT':
                with status_lock:
                    can_start = not ENGINE_STATUS['is_charging'] and ENGINE_STATUS['health'] == 'OK'
                if can_start:
                    with status_lock:
                        ENGINE_STATUS['is_charging'] = True
                        ENGINE_STATUS['driver_id'] = f"DRIVER-{time.time_ns() % 100}" 
                    print(f"\n[Engine] *** SUMINISTRO INICIADO *** para {ENGINE_STATUS['driver_id']}.")
                    threading.Thread(
                        target=simulate_charging,
                        args=(CP_ID, BROKER, ENGINE_STATUS['driver_id']), 
                        daemon=True
                    ).start()
                display_status()  # snapshot
                
            elif command == 'E' or command == 'END':
                with status_lock:
                    if ENGINE_STATUS['is_charging']:
                        ENGINE_STATUS['is_charging'] = False
                        print("\n[Engine] *** SUMINISTRO FINALIZADO ***. Notificando a Central (por hilo de simulación)...")
                display_status()  # snapshot
              #End Of File (fin de entrada) 
              #Este error ocurre cuando input() intenta leer del teclado, pero no hay más entrada disponible 
        except EOFError:
            pass 
        except Exception as e:
            print(f"Error en la entrada de usuario: {e}")

# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Uso: py ev_cp_e.py <PUERTO_ESCUCHA_ENGINE> <KAFKA_BROKER_IP:PORT> <ID_CP>")
        print("Ejemplo: py ev_cp_e.py 8001 localhost:9092 MAD-01")
        sys.exit(1)

    try:
        #Extraemos los argumentos
        ENGINE_PORT = int(sys.argv[1])
        KAFKA_BROKER = sys.argv[2]
        CP_ID = sys.argv[3]
        ENGINE_HOST = '0.0.0.0' #significa que el servidor acepta conexiones desde cualquier IP local
        

        # Guardamos broker global para que otros hilos lo usen al arrancar simulate_charging
        BROKER = KAFKA_BROKER
        # Inicializar el Productor Kafka (una sola vez)
        init_kafka_producer(KAFKA_BROKER)
        print(f"[Engine] Productor Kafka inicializado para {KAFKA_BROKER}")

        
        # 1. Iniciar el servidor TCP de sockets para el Monitor (hilo de salud)
        threading.Thread(target=start_health_server, args=(ENGINE_HOST, ENGINE_PORT), daemon=True).start()

        # 2. Muestra el estado actual del Engine continuamente en pantalla.
        # ¡IMPORTANTE! display_status se ejecuta en un hilo separado para no bloquear la entrada del usuario input() en process_user_input()
        #sino nos quedariamos esperando el input y no podriamos actualizar el display cada segundo 
        threading.Thread(target=display_status_loop, daemon=True).start()

        # 3. El hilo principal ejecuta la entrada de comandos del usuario
        #    process_user_input contiene el 'input()' que bloquea el prompt,
        #    manteniendo el programa abierto y listo para recibir comandos.
        print("Engine iniciado. Listo para comandos. Presiona Ctrl+C para detener.")
        process_user_input()
           
           #Si alguien pone letras donde debería ir un número
    except ValueError:
        print("Error: El puerto debe ser un número entero.")
        sys.exit(1)
    except Exception as e:
        print(f"Error fatal al iniciar el Engine: {e}")
        sys.exit(1)
    #Permite que si el usuario pulsa Ctrl + C, el programa se cierre limpiamente.
    except KeyboardInterrupt:
        print("\nEngine detenido por el usuario.")
        sys.exit(0)