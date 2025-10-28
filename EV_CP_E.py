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


# Funcion Productor Kafka para mandar la telemetria ---
def init_kafka_producer(broker):
    """Inicializa el productor Kafka para enviar telemetría a Central."""
    global KAFKA_PRODUCER
    #Paso 1: Verificar si ya existe un productor
    if KAFKA_PRODUCER is None:
        try:
            #Paso 1.1: Crear el productor Kafka
            KAFKA_PRODUCER = KafkaProducer(
                bootstrap_servers=[broker],
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
            )
            #Paso 1.2: Imprimir mensaje de éxito
            print(f"[ENGINE] Kafka producer conectado a {broker}")
        except NoBrokersAvailable:
            #Paso 1.3: Manejar error de broker no disponible
            print(f"[ENGINE] WARNING: broker {broker} no disponible. Los mensajes se descartarán hasta reconexión.")
            #Paso 1.4: Lanzar hilo de reintento automático
            def _reconnect_loop(broker, interval=5):
                global KAFKA_PRODUCER
                while KAFKA_PRODUCER is None:
                    try:
                        #Paso 1.4.1: Intentar crear nuevo productor
                        tmp = KafkaProducer(
                            bootstrap_servers=[broker],
                            value_serializer=lambda v: json.dumps(v).encode('utf-8')
                        )
                        #Paso 1.4.2: Asignar el productor si es exitoso
                        KAFKA_PRODUCER = tmp
                        #Paso 1.4.3: Imprimir mensaje de éxito
                        print(f"[ENGINE] Reconectado a Kafka broker {broker}")
                        break
                    except Exception as e:
                        #Paso 1.4.4: Manejar error de reconexión
                        print(f"[ENGINE] Reconexión fallida a {broker}: {e} - reintentando en {interval}s")
                        time.sleep(interval)
            #Paso 1.5: Lanzar hilo de reintento en background
            threading.Thread(target=_reconnect_loop, args=(broker,), daemon=True).start()
        except Exception as e:
            #Paso 1.6: Manejar otros errores de inicialización
            print(f"[ENGINE] ERROR inicializando Kafka producer: {e}")
            #Paso 1.7: Lanzar hilo de reintento para reconectar en background
            threading.Thread(target=lambda: time.sleep(1) or init_kafka_producer(broker), daemon=True).start()

#Funcion para enviar la telemetria a la central
def send_telemetry_message(payload):
    """Envía payload JSON al topic cp_telemetry. No lanza si producer no existe."""
    global KAFKA_PRODUCER
    
    #Paso 1: Validar tipos de mensajes que pueden enviarse sin carga activa
    msg_type = payload.get('type', '').upper()
    if msg_type in ['AVERIADO', 'CONEXION_PERDIDA', 'FAULT', 'SESSION_STARTED', 'SUPPLY_END']:
        #Paso 1.1: Estos mensajes pueden enviarse sin estar cargando
        pass
    else:
        #Paso 1.2: Para mensajes de consumo, validar que hay driver activo
        with status_lock:
            if not ENGINE_STATUS.get('is_charging') or not ENGINE_STATUS.get('driver_id'):
                #Paso 1.2.1: Bloquear telemetría si no hay conductor activo
                print(f"[ENGINE] AVISO: Telemetría de consumo bloqueada - no hay conductor activo autorizado.")
                return
    
    #Paso 2: Log local para depuración
    print(f"[ENGINE] Enviando telemetry -> {payload}")
    #Paso 2.1: Verificar que el productor esté inicializado
    if KAFKA_PRODUCER is None:
        #Paso 2.1.1: Descartar mensaje si no hay productor
        print(f"[ENGINE] AVISO: Kafka producer no inicializado. Mensaje descartado.")
        return
    try:
        #Paso 2.2: Enviar mensaje al topic de telemetría
        KAFKA_PRODUCER.send(KAFKA_TOPIC_TELEMETRY, value=payload)
        #Paso 2.3: Forzar envío inmediato
        KAFKA_PRODUCER.flush()
    except Exception as e:
        #Paso 2.4: Manejar errores de envío
        print(f"[ENGINE] Error enviando telemetry: {e}")

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
    print(f"  CARGANDO: {'SÍ' if ENGINE_STATUS['is_charging'] else 'NO'}")
    if ENGINE_STATUS['driver_id']:
        print(f"  DRIVER: {ENGINE_STATUS['driver_id']}")
    print("  Información de suministro: disponible en la CENTRAL (topic cp_telemetry)")
    print("="*50)
    print("Comandos: [F]AIL para simular AVERÍA | [R]ECOVER para recuperar")
    print("          [I]NIT para simular ENCHUFAR vehículo (Iniciar Suministro)")
    print("          [E]ND para simular DESENCHUFAR vehículo (Finalizar Suministro)")
    print("-" * 50)


# --- Funciones de Comunicación con Monitor---

#HILO 1: Espera mensajes del Monitor cada 1 segundo y le responde con OK/KO
def handle_monitor_connection(monitor_socket, monitor_address):
    """Maneja la conexión síncrona con el Monitor local (EC_CP_M)."""
    global ENGINE_STATUS
    
    try:
        #Paso 1: Recibir el mensaje enviado por el Monitor
        data = monitor_socket.recv(1024).decode('utf-8').strip()
        
        #Paso 2: Bloquear el acceso a ENGINE_STATUS mientras procesamos este comando
        #   Esto impide que otro hilo lo modifique al mismo tiempo
        with status_lock:
            
            #Paso 3: Procesar los comandos recibidos desde el monitor
            #Paso 3.1: Comando HEALTH_CHECK
            if data.startswith("HEALTH_CHECK"):
                #Paso 3.1.1: Responder con el estado de salud actual
                response = ENGINE_STATUS['health'] 
                monitor_socket.sendall(response.encode('utf-8'))

            #Paso 3.2: Comando PARAR
            elif data.startswith("PARAR"):
                #Paso 3.2.1: Detener la carga si estaba en curso
                ENGINE_STATUS['is_charging'] = False
                #Paso 3.2.2: Confirmar acción al Monitor
                monitor_socket.sendall(b"ACK#PARAR")
            
            #Paso 3.3: Comando REANUDAR
            elif data.startswith("REANUDAR"):
                #Paso 3.3.1: Restablecer estado de salud a OK
                ENGINE_STATUS['health'] = 'OK'
                #Paso 3.3.2: Confirmar acción al Monitor
                monitor_socket.sendall(b"ACK#REANUDAR")
            
            #Paso 3.4: Comando AUTORIZAR_SUMINISTRO
            elif data.startswith("AUTORIZAR_SUMINISTRO"):
                parts = data.split('#')
                if len(parts) >= 2:
                    #Paso 3.4.1: Extraer ID del driver
                    driver_id = parts[1]
                    print(f"[ENGINE] Solicitud de carga recibida por CP {CP_ID}. Driver asignado: {driver_id}")
                    #Paso 3.4.2: Registrar autorización sin iniciar carga aún
                    ENGINE_STATUS['driver_id'] = driver_id
                    print(f"[ENGINE] ACK enviado a CENTRAL")
                    print(f"[ENGINE] Esperando que el usuario inicie el suministro...")
                    #Paso 3.4.3: Confirmar autorización al Monitor
                    monitor_socket.sendall(b"ACK#AUTORIZAR_SUMINISTRO")
                else:
                    #Paso 3.4.4: Manejar formato inválido
                    print(f"[ENGINE] ERROR: Formato de autorización inválido")
                    monitor_socket.sendall(b"NACK#AUTORIZAR_SUMINISTRO")
            
            #Paso 3.5: Comando desconocido
            else:
                #Paso 3.5.1: Responder con error para comandos no reconocidos
                monitor_socket.sendall(b"ERROR")

        #Paso 4: El Lock se libera automáticamente al salir del bloque 'with'

    except Exception as e:
        #Paso 5: Manejar errores de conexión con el Monitor
        print(f"\n[Engine] Conexión con Monitor local ({monitor_address}) perdida: {e}")
    finally:
        #Paso 6: Cerrar siempre el socket tras atender la petición
        monitor_socket.close()

#HILO 2: Levantamos el Servidor TCP local y su propósito es responder a los chequeos de salud del Monitor
def start_health_server(host, port):
    """Inicia el servidor de sockets que escucha los HEALTH_CHECK del Monitor."""
    #Paso 1: Crear el socket del servidor
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        #Paso 1.1: Asociar el socket a una IP y puerto
        server_socket.bind((host, port))
        #Paso 1.2: Poner el socket a escuchar conexiones entrantes
        server_socket.listen(1) # Solo necesitamos una conexión (la del Monitor)
        #Paso 1.3: Imprimir mensaje de éxito
        print(f"[Engine] Servidor de salud local escuchando en {host}:{port}")
    #Paso 1.4: Manejar errores de bind() o listen()
    except Exception as e:
        print(f"[Engine] ERROR: No se pudo iniciar el servidor de salud en {port}: {e}")
        sys.exit(1)
    
    
    #Paso 2: Bucle principal del servidor
    while True:
        try:
            #Paso 2.1: Esperar una conexión del Monitor
            #monitor_socket: el socket activo para hablar con ese cliente
            #address: la IP y puerto del cliente (Monitor)
            monitor_socket, address = server_socket.accept()
            #Paso 2.2: Crear un nuevo hilo para manejar la conexión
            monitor_thread = threading.Thread(
                target=handle_monitor_connection, 
                #el socket y la IP del Monitor como argumentos
                args=(monitor_socket, address)) 
            #Paso 2.3: Marcar el hilo como daemon para que se cierre automáticamente
            monitor_thread.daemon = True
            #Paso 2.4: Iniciar la ejecución del hilo
            monitor_thread.start()
        #Paso 2.5: Manejar interrupciones del teclado
        except KeyboardInterrupt:
            break
        #Paso 2.6: Manejar otros errores de conexión
        except Exception as e:
            print(f"[Engine] Error aceptando conexión del Monitor: {e}")
            time.sleep(1)
            
# --- Funciones de Simulación de Lógica de Negocio (Suministro) ---

#HILO 3: Simulación de Suministro / Producción Kafka
def simulate_charging(cp_id, broker, driver_id, price_per_kwh=0.20, step_kwh=0.1):
    """
    Simula el suministro:
    - Inicializa producer si es necesario.
    - Envía mensajes CONSUMO cada segundo con campos: type, cp_id, user_id, kwh, importe
    - Al terminar (usuario), envía SUPPLY_END con totales.
    - price_per_kwh se usa para calcular importe (opcional; la CENTRAL leerá su precio desde BD).
    """
    #Paso 1: Inicializar el productor Kafka
    init_kafka_producer(broker)
    #Paso 1.1: Inicializar variables de acumulación
    total_kwh = 0.0
    total_importe = 0.0
    aborted_due_to_fault = False

    #Paso 2: Actualizar estado del Engine
    with status_lock:
        ENGINE_STATUS['is_charging'] = True
        ENGINE_STATUS['driver_id'] = driver_id

    #Paso 2.1: Imprimir mensaje de inicio
    print(f"[ENGINE] Inicio suministro CP={cp_id} driver={driver_id}")
    try:
        #Paso 3: Bucle principal de simulación de carga
        while True:
            with status_lock:
                #Paso 3.1: Verificar si se debe detener la carga
                if not ENGINE_STATUS['is_charging']:
                    break
                #Paso 3.2: Verificar si hay avería durante la carga
                if ENGINE_STATUS['health'] != "OK":
                    #Paso 3.2.1: Crear mensaje de avería
                    payload_fault = {
                        "type": "AVERIADO",
                        "cp_id": cp_id,
                        "user_id": driver_id,
                        "kwh": round(total_kwh, 3),
                        "importe": round(total_importe, 2)
                    }
                    #Paso 3.2.2: Enviar mensaje de avería
                    send_telemetry_message(payload_fault)
                    aborted_due_to_fault = True
                    return

            #Paso 3.3: Incrementar consumo cada segundo
            total_kwh += step_kwh
            total_importe = total_kwh * price_per_kwh

            #Paso 3.4: Crear mensaje de consumo
            payload = {
                "type": "CONSUMO",
                "cp_id": cp_id,
                "user_id": driver_id,
                "kwh": round(total_kwh, 3),
                "importe": round(total_importe, 2)
            }
            #Paso 3.5: Enviar telemetría de consumo
            send_telemetry_message(payload)
            #Paso 3.6: Esperar un segundo antes del siguiente incremento
            time.sleep(1)
    except KeyboardInterrupt:
        #Paso 4: Manejar interrupción del teclado
        pass
    finally:
       #Paso 5: Finalizar la simulación de carga
       #Paso 5.1: En caso de aborto por avería no enviamos SUPPLY_END; en caso normal sí
        if not aborted_due_to_fault:
            #Paso 5.1.1: Crear mensaje de fin de suministro
            payload_end = {
                "type": "SUPPLY_END",
                "cp_id": cp_id,
                "user_id": driver_id,
                "kwh": round(total_kwh, 3),
                "importe": round(total_importe, 2)
            }
            #Paso 5.1.2: Enviar mensaje de fin de suministro
            send_telemetry_message(payload_end)

        #Paso 5.2: Limpiar estado del Engine
        with status_lock:
            ENGINE_STATUS['is_charging'] = False
            ENGINE_STATUS['driver_id'] = None

        #Paso 5.3: Imprimir mensaje final
        if aborted_due_to_fault:
            print(f"[ENGINE] SUMINISTRO abortado por AVERÍA CP={cp_id} driver={driver_id} kwh={total_kwh} importe={total_importe}")
        else:
            print(f"[ENGINE] FIN suministro CP={cp_id} driver={driver_id} kwh={total_kwh} importe={total_importe}")

# FUNCIONES ELIMINADAS: stop_charging(), simulate_fault(), simulate_connection_lost() - No se utilizaban




#HILO 4: Funciones de Entrada de Usuario (Simulación de Teclas)
def process_user_input():
    """Maneja la entrada del usuario para simular averías/eventos."""
    global ENGINE_STATUS
    #Paso 1: Bucle principal de entrada de usuario
    while True:
        try:
            #Paso 1.1: Leer comando del usuario
            #input() espera a que el usuario escriba algo
            #Mientras tanto, el hilo principal se detiene completamente
            #Nada más se ejecuta en ese hilo hasta que se pulse Enter
            command = input("\n> ").strip().upper()
            
            #Paso 2: Procesar comandos del usuario
            #Paso 2.1: Comando FAIL - Simular Avería (KO)
            if command == 'F' or command == 'FAIL':
                if ENGINE_STATUS['health'] == 'OK':
                    #Paso 2.1.1: Cambiar el estado de salud a "KO"
                    with status_lock:
                        ENGINE_STATUS['health'] = 'KO'
                    print("\n[Engine] *** ESTADO INTERNO: AVERÍA (KO) ***. El Monitor notificará a Central.")
                #Paso 2.1.2: Actualizar display
                display_status()
                
            #Paso 2.2: Comando RECOVER - Simular Recuperación
            elif command == 'R' or command == 'RECOVER':
                if ENGINE_STATUS['health'] == 'KO':
                    #Paso 2.2.1: Restablecer el estado de salud a "OK"
                    with status_lock:
                        ENGINE_STATUS['health'] = 'OK'
                    print("\n[Engine] *** ESTADO INTERNO: RECUPERADO (OK) ***. El Monitor notificará a Central.")
                #Paso 2.2.2: Actualizar display
                display_status()
                
            #Paso 2.3: Comando INIT - Iniciar Suministro
            elif command == 'I' or command == 'INIT':
                print(f"\n[Engine] *** COMANDO INIT RECIBIDO ***")
                #Paso 2.3.1: Consultar a CENTRAL directamente si hay sesión activa autorizada
                try:
                    helper_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    helper_socket.connect(('127.0.0.1', ENGINE_PORT + 1000))
                    helper_socket.sendall(f"CHECK_SESSION#{CP_ID}".encode('utf-8'))
                    resp = helper_socket.recv(1024).decode('utf-8').strip()
                    helper_socket.close()
                except Exception as e:
                    print(f"[Engine] Error consultando sesión activa: {e}")
                    resp = "NO_SESSION"

                #Paso 2.3.2: Verificar respuesta de sesión
                if resp == "NO_SESSION":
                    print("[ENGINE] ERROR: No hay conductor autorizado para iniciar el suministro.")
                else:
                    #Paso 2.3.3: Extraer ID del driver
                    driver_id = resp
                    print(f"[ENGINE] Sesión validada para driver {driver_id}")
                    #Paso 2.3.4: Verificar que se puede iniciar
                    with status_lock:
                        can_start = not ENGINE_STATUS['is_charging'] and ENGINE_STATUS['health'] == 'OK'
                    if not can_start:
                        print("[ENGINE] No se puede iniciar: ya está cargando o estado KO.")
                    else:
                        #Paso 2.3.5: Iniciar suministro
                        print(f"[ENGINE] Iniciando suministro para driver {driver_id}...")
                        with status_lock:
                            ENGINE_STATUS['is_charging'] = True
                            ENGINE_STATUS['driver_id'] = driver_id
                        #Paso 2.3.6: Avisar a CENTRAL que la sesión comienza
                        send_telemetry_message({
                            "type": "SESSION_STARTED",
                            "cp_id": CP_ID,
                            "user_id": driver_id
                        })
                        #Paso 2.3.7: Iniciar hilo de simulación de carga
                        threading.Thread(
                            target=simulate_charging,
                            args=(CP_ID, BROKER, driver_id),
                            daemon=True
                        ).start()
                        print(f"[ENGINE] Carga iniciada tras validar sesión para driver {driver_id}.")
                #Paso 2.3.8: Actualizar display
                display_status()
                
            #Paso 2.4: Comando END - Finalizar Suministro
            elif command == 'E' or command == 'END':
                with status_lock:
                    if ENGINE_STATUS['is_charging']:
                        #Paso 2.4.1: Finalizar suministro
                        print(f"[ENGINE] Finalizando suministro...")
                        ENGINE_STATUS['is_charging'] = False
                        print(f"[ENGINE] *** SUMINISTRO FINALIZADO ***. Notificando a Central...")
                    else:
                        #Paso 2.4.2: No hay suministro activo
                        print(f"[ENGINE] No hay suministro activo para finalizar.")
                #Paso 2.4.3: Actualizar display
                display_status()
              #Paso 3: Manejar errores de entrada
              #End Of File (fin de entrada) 
              #Este error ocurre cuando input() intenta leer del teclado, pero no hay más entrada disponible 
        except EOFError:
            pass 
        except Exception as e:
            print(f"Error en la entrada de usuario: {e}")

# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    #Paso 1: Obtener los argumentos de la línea de comandos
    if len(sys.argv) != 4:
        print("Uso: py ev_cp_e.py <PUERTO_ESCUCHA_ENGINE> <KAFKA_BROKER_IP:PORT> <ID_CP>")
        print("Ejemplo: py ev_cp_e.py 8001 localhost:9092 MAD-01")
        sys.exit(1)
    #Paso 2: Extraer los argumentos
    try:
        ENGINE_PORT = int(sys.argv[1])
        KAFKA_BROKER = sys.argv[2]
        CP_ID = sys.argv[3]
        ENGINE_HOST = '0.0.0.0' #significa que el servidor acepta conexiones desde cualquier IP local
        

        # Paso 3: Guardamos broker global para que otros hilos lo usen al arrancar simulate_charging
        BROKER = KAFKA_BROKER
        # Paso 4: Inicializar el Productor Kafka (una sola vez)
        init_kafka_producer(KAFKA_BROKER)
        print(f"[Engine] Productor Kafka inicializado para {KAFKA_BROKER}")

        
        # Paso 5: Iniciar los hilos
        # Paso 5.1: Iniciar el servidor TCP de sockets para el Monitor (hilo de salud)
        threading.Thread(target=start_health_server, args=(ENGINE_HOST, ENGINE_PORT), daemon=True).start()

        # Paso 5.2: Mostrar estado inicial del Engine
        display_status()

        # Paso 5.3: Iniciar el hilo principal para la entrada de comandos del usuario
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