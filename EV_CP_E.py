# Fichero: ev_cp_e.py (Engine del Punto de Recarga)
import socket
import threading
import sys
import time
import os
import json
from kafka import KafkaProducer # Usado para enviar telemetría a Central (asíncrono)

# --- Variables de Estado del Engine ---
# Se usan para simular el estado interno de salud (OK/KO)
# El Monitor preguntará por este estado
ENGINE_STATUS = {"health": "OK", "is_charging": False, "driver_id": None}
KAFKA_PRODUCER = None
CP_ID = "" 

# --- Funciones de Utilidad ---
def clear_screen():
    """Limpia la pantalla de la terminal."""
    os.system('cls' if os.name == 'nt' else 'clear')

def display_status():
    """Muestra el estado actual del Engine en pantalla."""
    clear_screen()
    print(f"--- EV CHARGING POINT ENGINE: {CP_ID} ---")
    print("="*50)
    print(f"  ESTADO DE SALUD: {ENGINE_STATUS['health']}")
    print(f"  ESTADO DE RECARGA: {'Suministrando' if ENGINE_STATUS['is_charging'] else 'Reposo'}")
    if ENGINE_STATUS['is_charging']:
        print(f"  CONDUCTOR ASIGNADO: {ENGINE_STATUS['driver_id']}")
    print("="*50)
    print("Comandos: [F]AIL para simular AVERÍA | [R]ECOVER para recuperar")
    print("          [I]NIT para simular ENCHUFAR vehículo (Iniciar Suministro)")
    print("          [E]ND para simular DESENCHUFAR vehículo (Finalizar Suministro)")
    print("-" * 50)


# --- Funciones de Comunicación con Monitor (Servidor de Salud) ---
def handle_monitor_connection(conn, addr):
    """Maneja la conexión síncrona con el Monitor local (EC_CP_M)."""
    global ENGINE_STATUS
    
    try:
        # El Monitor debe ser el único que se conecta a este puerto
        while True:
            # Esperamos el HEALTH_CHECK
            data = conn.recv(1024)
            if not data:
                break
            
            message = data.decode('utf-8').strip()
            
            if message.startswith("HEALTH_CHECK"):
                # Si estamos en modo KO, respondemos KO. De lo contrario, OK.
                response = ENGINE_STATUS['health'] 
                conn.sendall(response.encode('utf-8'))
                
            # Otros mensajes del Monitor (si los hubiera) se procesarían aquí
            
    except Exception as e:
        # Esto ocurre si la conexión con el Monitor se pierde
        print(f"\n[Engine] Conexión con Monitor local ({addr}) perdida: {e}")
    finally:
        conn.close()

def start_health_server(host, port):
    """Inicia el servidor de sockets que escucha los HEALTH_CHECK del Monitor."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server_socket.bind((host, port))
        server_socket.listen(1) # Solo necesitamos una conexión (la del Monitor)
        print(f"[Engine] Servidor de salud local escuchando en {host}:{port}")
    except Exception as e:
        print(f"[Engine] ERROR: No se pudo iniciar el servidor de salud en {port}: {e}")
        sys.exit(1)
    while True:
        try:
            # Aceptamos la conexión del Monitor (bloqueante)
            monitor_socket, address = server_socket.accept()
            #print(f"\n[Engine] Monitor local conectado desde {address}")
            # Manejamos la conexión en un hilo (aunque solo debe haber uno)
            monitor_thread = threading.Thread(target=handle_monitor_connection, args=(monitor_socket, address))
            monitor_thread.daemon = True
            monitor_thread.start()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[Engine] Error aceptando conexión del Monitor: {e}")
            time.sleep(1)
            
# --- Funciones de Simulación de Lógica de Negocio (Suministro) ---

def simulate_charging(cp_id):
    """Simula el envío de telemetría constante a la Central vía Kafka."""
    global ENGINE_STATUS, KAFKA_PRODUCER
    
    kwh_supplied = 0.0
    price_per_kwh = 0.50 # Ejemplo de precio fijo
    
    while ENGINE_STATUS['is_charging']:
        time.sleep(1) # Cada segundo enviamos un mensaje
        
        # 1. Simular consumo (aumenta 0.1 kWh por segundo)
        kwh_supplied += 0.1
        current_cost = kwh_supplied * price_per_kwh
        
        # 2. Si hay avería, el suministro debe terminar inmediatamente
        if ENGINE_STATUS['health'] != 'OK':
            print("\n[Engine] Suministro terminado súbitamente por avería!")
            break

        # 3. Enviar telemetría a Kafka (Topic: cp_telemetry)
        telemetry_message = {
            "cp_id": cp_id,
            "type": "CONSUMO",
            "kwh": round(kwh_supplied, 2),
            "importe": round(current_cost, 2),
            "user_id": ENGINE_STATUS['driver_id']
        }
        
        try:
            KAFKA_PRODUCER.send('cp_telemetry', value=telemetry_message)
            # print(f"[Engine] Enviado CONSUMO: {kwh_supplied:.2f} kWh")
        except Exception as e:
            print(f"[Engine] ERROR: No se pudo enviar mensaje a Kafka: {e}")
            
    # Al finalizar el bucle, nos aseguramos de que el estado se actualice
    ENGINE_STATUS['is_charging'] = False
    ENGINE_STATUS['driver_id'] = None
    display_status()

# --- Funciones de Entrada de Usuario (Simulación de Teclas) ---
def process_user_input():
    """Maneja la entrada del usuario para simular averías/eventos."""
    global ENGINE_STATUS
    while True:
        try:
            command = input("\n> ").strip().upper()
            
            if command == 'F' or command == 'FAIL':
                # Simular Avería (KO)
                if ENGINE_STATUS['health'] == 'OK':
                    ENGINE_STATUS['health'] = 'KO'
                    print("\n[Engine] *** ESTADO INTERNO: AVERÍA (KO) ***. El Monitor notificará a Central.")
                display_status()
                
            elif command == 'R' or command == 'RECOVER':
                # Simular Recuperación
                if ENGINE_STATUS['health'] == 'KO':
                    ENGINE_STATUS['health'] = 'OK'
                    print("\n[Engine] *** ESTADO INTERNO: RECUPERADO (OK) ***. El Monitor notificará a Central.")
                display_status()
                
            elif command == 'I' or command == 'INIT':
                # Simular INICIO de Suministro (Enganchar vehículo)
                if not ENGINE_STATUS['is_charging'] and ENGINE_STATUS['health'] == 'OK':
                    # TO-DO: El driver_id debe venir de la Central tras la autorización,
                    # aquí lo forzamos para la simulación.
                    ENGINE_STATUS['is_charging'] = True
                    ENGINE_STATUS['driver_id'] = f"DRIVER-{time.time_ns() % 100}" 
                    print(f"\n[Engine] *** SUMINISTRO INICIADO *** para {ENGINE_STATUS['driver_id']}.")
                    # Iniciar el envío de telemetría en un hilo
                    threading.Thread(target=simulate_charging, args=(CP_ID,)).start()
                display_status()
                
            elif command == 'E' or command == 'END':
                 # Simular FIN de Suministro (Desenganchar vehículo)
                if ENGINE_STATUS['is_charging']:
                    user_id_finished = ENGINE_STATUS['driver_id']
                    
                    ENGINE_STATUS['is_charging'] = False # Detiene el bucle simulate_charging
                    print("\n[Engine] *** SUMINISTRO FINALIZADO ***. Notificando a Central...")
                    
                    # 1. Enviar mensaje de finalización a Central (Kafka)
                    finish_message = {
                        "cp_id": CP_ID,
                        "type": "SUPPLY_END",  # <-- MENSAJE CLAVE
                        "user_id": user_id_finished
                    }
                    try:
                        KAFKA_PRODUCER.send('cp_telemetry', value=finish_message)
                    except Exception as e:
                        print(f"[Engine] ERROR al enviar fin de suministro a Kafka: {e}")

                display_status()
                
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
        ENGINE_PORT = int(sys.argv[1])
        KAFKA_BROKER = sys.argv[2]
        CP_ID = sys.argv[3]
        ENGINE_HOST = '0.0.0.0'
        
        # Inicializar el Productor Kafka (una sola vez)
        KAFKA_PRODUCER = KafkaProducer(
            bootstrap_servers=[KAFKA_BROKER],
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        print(f"[Engine] Productor Kafka inicializado para {KAFKA_BROKER}")

        # 1. Iniciar el servidor de sockets para el Monitor (hilo de salud)
        threading.Thread(target=start_health_server, args=(ENGINE_HOST, ENGINE_PORT), daemon=True).start()

        # 2. Iniciar el hilo del panel de estado (refresco visual)
        # ¡IMPORTANTE! display_status se ejecuta en un hilo separado
        threading.Thread(target=display_status, daemon=True).start()

        # 3. El hilo principal ejecuta la entrada de comandos del usuario
        #    process_user_input contiene el 'input()' que bloquea el prompt,
        #    manteniendo el programa abierto y listo para recibir comandos.
        print("Engine iniciado. Listo para comandos. Presiona Ctrl+C para detener.")
        process_user_input()

    except ValueError:
        print("Error: El puerto debe ser un número entero.")
        sys.exit(1)
    except Exception as e:
        print(f"Error fatal al iniciar el Engine: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nEngine detenido por el usuario.")
        sys.exit(0)