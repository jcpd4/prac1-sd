import socket
import threading
import sys
import time
import os
import json
import database
from kafka import KafkaProducer # Usado para enviar telemetría a Central (asíncrono)

# --- Variables de Estado del Engine ---
# Se usan para simular el estado interno de salud (OK/KO)
# El Monitor preguntará por este estado
ENGINE_STATUS = {"health": "OK", "is_charging": False, "driver_id": None}
KAFKA_PRODUCER = None
CP_ID = ""

# Creamos un Lock global para proteger el acceso concurrente a ENGINE_STATUS.
# Un Lock (cerrojo) garantiza que solo un hilo a la vez puede modificar el estado compartido.
# Así evitamos condiciones de carrera si llegan comandos simultáneos (p. ej., PARAR y REANUDAR al mismo tiempo).
status_lock = threading.Lock()


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

def simulate_charging(cp_id):
    """Simula el envío de telemetría constante a la Central vía Kafka."""
    global ENGINE_STATUS, KAFKA_PRODUCER
    
    kwh_supplied = 0.0
    price_per_kwh = 0.50 # Ejemplo de precio fijo
    
    #Bucle activo mientras el CP esté cargando y Se ejecuta una vez por segundo
    while ENGINE_STATUS['is_charging']:
        time.sleep(1) # Cada segundo enviamos un mensaje
        
        # 1. Simular consumo
        kwh_supplied += 0.1 #Cada segundo simula que el CP suministra 0.1 kWh
        current_cost = kwh_supplied * price_per_kwh #Calcula el coste acumulado
        
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
            #input() espera a que el usuario escriba algo.
            #Mientras tanto, el hilo principal se detiene completamente
            #Nada más se ejecuta en ese hilo hasta que se pulse Enter.
            command = input("\n> ").strip().upper()
            
            if command == 'F' or command == 'FAIL':
                # Simular Avería (KO)
                if ENGINE_STATUS['health'] == 'OK':
                    #Cambiamos el estado de salud a "KO".
                    ENGINE_STATUS['health'] = 'KO'
                    #El Monitor lo detectará en 1 segundo y avisará a la Central.
                    print("\n[Engine] *** ESTADO INTERNO: AVERÍA (KO) ***. El Monitor notificará a Central.")
                display_status()
                
            elif command == 'R' or command == 'RECOVER':
                # Simular Recuperación
                if ENGINE_STATUS['health'] == 'KO':
                    #Restablecemos el estado de salud a "OK"
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
                    #Lanzamos simulate_charging en un nuevo hilo, para que pueda seguir escuchando comandos.
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
                        "type": "SUPPLY_END",  # <-- Envía un mensaje "SUPPLY_END" a Kafka para que la Central sepa que ha terminado.
                        "user_id": user_id_finished
                    }
                    try:
                        KAFKA_PRODUCER.send('cp_telemetry', value=finish_message)
                    except Exception as e:
                        print(f"[Engine] ERROR al enviar fin de suministro a Kafka: {e}")

                display_status()
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
        
        # Inicializar el Productor Kafka (una sola vez)
        KAFKA_PRODUCER = KafkaProducer(
            #Este parámetro le dice al productor a qué broker Kafka conectarse.
            bootstrap_servers=[KAFKA_BROKER], #Ej localhost:9092"
            
            #Kafka solo transmite bytes, no objetos de Python. Por eso:
            #1.Convierte el diccionario en JSON con json.dumps(v)
            #2.Luego convierte el JSON en bytes con .encode('utf-8')
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        print(f"[Engine] Productor Kafka inicializado para {KAFKA_BROKER}")

        # 1. Iniciar el servidor TCP de sockets para el Monitor (hilo de salud)
        threading.Thread(target=start_health_server, args=(ENGINE_HOST, ENGINE_PORT), daemon=True).start()

        # 2. Muestra el estado actual del Engine continuamente en pantalla.
        # ¡IMPORTANTE! display_status se ejecuta en un hilo separado para no bloquear la entrada del usuario input() en process_user_input()
        #sino nos quedariamos esperando el input y no podriamos actualizar el display cada segundo 
        threading.Thread(target=display_status, daemon=True).start()

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