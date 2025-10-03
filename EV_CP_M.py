# Fichero: ec_cp_m.py (Versión Mejorada)
import socket
import sys
import time
import threading

# Constantes de configuración (ajústalas según tu necesidad de prueba)
HEARTBEAT_INTERVAL_TO_CENTRAL = 15 # Cada 15s, informamos a la Central
HEARTBEAT_INTERVAL_TO_ENGINE = 1  # Cada 1s, comprobamos el Engine 

# --- Funciones de Conexión y Control Local (Monitorización del Engine) ---
def monitor_engine_health(engine_host, engine_port, cp_id, central_socket_ref):
    """
    Se conecta al EV_CP_E local y le pide una comprobación de salud cada 1 segundo.
    Si falla, envía un FAULT a la Central.
    """
    print(f"Iniciando monitorización del Engine en {engine_host}:{engine_port}")
    while True:
        try:
            # Conexión temporal o persistente al Engine (depende del diseño)
            # Usaremos una conexión temporal simple para la prueba de concepto
            engine_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            engine_socket.connect((engine_host, engine_port))
            
            # 1. Enviar mensaje de comprobación de salud al Engine
            engine_socket.sendall(f"HEALTH_CHECK#{cp_id}".encode('utf-8'))
            
            # 2. Esperar respuesta del Engine (máximo 1 segundo)
            engine_socket.settimeout(1.5)
            response = engine_socket.recv(1024).decode('utf-8').strip()
            
            if response != "OK":
                raise Exception(f"Engine reportó {response}") # Forzar el error

            # 3. Salud OK. No hacemos nada.
            
        except (socket.error, ConnectionRefusedError, socket.timeout) as e:
            # 4. Engine inalcanzable o KO. Notificar AVERÍA a la Central.
            print(f"ALERTA: Engine KO. Razón: {e}. Notificando avería a la Central.")
            # Enviamos el mensaje de avería por el socket de la Central
            if central_socket_ref[0]:
                 # Usamos el socket de la Central para reportar la avería
                 central_socket_ref[0].sendall(f"FAULT#{cp_id}".encode('utf-8'))
            
        except Exception as e:
            print(f"Error inesperado en la monitorización del Engine: {e}")
            
        finally:
            # Nos aseguramos de cerrar el socket del Engine en cada ciclo
            if 'engine_socket' in locals():
                engine_socket.close()
        
        time.sleep(HEARTBEAT_INTERVAL_TO_ENGINE) # Comprobamos cada 1 segundo

# --- Funciones de Conexión y Reporte a la Central ---
def start_central_connection(central_host, central_port, cp_id, location, engine_host, engine_port):
    """Maneja la conexión principal con la Central (registro y gestión de comandos)."""
    
    # Usamos una lista para que el hilo de monitorización pueda acceder al socket de la Central
    central_socket_ref = [None] 

    # Bucle infinito de reconexión
    while True:
        try:
            # 1. Conexión y Registro
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            print(f"Intentando conectar CP '{cp_id}' a Central {central_host}:{central_port}...")
            client_socket.connect((central_host, central_port))
            print("¡Conectado al servidor central! Enviando registro.")

            central_socket_ref[0] = client_socket # Guardamos la referencia
            
            register_message = f"REGISTER#{cp_id}#{location}"
            client_socket.sendall(register_message.encode('utf-8'))
            
            # 2. Iniciar el hilo de Monitorización Local si aún no está activo
            if not any(t.name == "EngineMonitor" for t in threading.enumerate()):
                monitor_thread = threading.Thread(
                    target=monitor_engine_health, 
                    args=(engine_host, engine_port, cp_id, central_socket_ref),
                    name="EngineMonitor"
                )
                monitor_thread.daemon = True
                monitor_thread.start()

            # 3. Bucle de escucha de comandos de la Central (Parar/Reanudar)
            while True:
                # La central puede enviar comandos en cualquier momento, hay que estar escuchando
                data = client_socket.recv(1024)
                if not data:
                    break # Conexión cerrada por la Central

                command = data.decode('utf-8').strip().split('#')[0]
                
                if command == 'PARAR':
                    print("COMANDO CENTRAL: Recibido 'PARAR'. Poniendo CP en modo FUERA_DE_SERVICIO.")
                    # TO-DO: Implementar la lógica para parar el Engine local
                    # TO-DO: Responder a la central con un ACK/NACK (Protocolo recomendado)
                elif command == 'REANUDAR':
                    print("COMANDO CENTRAL: Recibido 'REANUDAR'. Poniendo CP en modo ACTIVADO.")
                    # TO-DO: Implementar la lógica para reanudar el Engine local
                else:
                    print(f"Comando desconocido recibido: {command}")
                
        except (ConnectionRefusedError, socket.error, ConnectionResetError) as e:
            print(f"Error de conexión con Central: {e}. Reintentando en 10s...")
            time.sleep(10)
        except Exception as e:
            print(f"Error inesperado en el socket de la Central: {e}")
            break
        finally:
            if client_socket:
                client_socket.close()
            central_socket_ref[0] = None # Limpiamos la referencia antes de reintentar

# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    if len(sys.argv) != 6:
        print("Uso: py ec_cp_m.py <IP_CENTRAL> <PUERTO_CENTRAL> <IP_ENGINE> <PUERTO_ENGINE> <ID_CP>")
        print("Ejemplo: py ec_cp_m.py localhost 8000 localhost 8001 MAD-01")
        sys.exit(1)

    CENTRAL_IP = sys.argv[1]
    ENGINE_IP = sys.argv[3]
    CP_ID = sys.argv[5]

    try:
        CENTRAL_PORT = int(sys.argv[2])
        ENGINE_PORT = int(sys.argv[4])
    except ValueError:
        print("Error: Los puertos deben ser números enteros.")
        sys.exit(1)

    locations = {"MAD-01": "C/ Serrano 10", "VAL-03": "Plaza del Ayuntamiento 1", "BCN-05": "Las Ramblas 55"}
    LOCATION = locations.get(CP_ID, "Ubicación Desconocida")

    start_central_connection(CENTRAL_IP, CENTRAL_PORT, CP_ID, LOCATION, ENGINE_IP, ENGINE_PORT)