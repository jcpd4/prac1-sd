import socket # para tener una comunicacion por red
import sys # para usar parametros desde consola
import time # para manejar el tiempo
import threading # para poder ejecutar en paralelo
import database

# Constantes de configuracion (ajustalas segun tu necesidad de prueba)
HEARTBEAT_INTERVAL_TO_CENTRAL = 15 # Cada 15s, informamos a la Central (No usado aun)
HEARTBEAT_INTERVAL_TO_ENGINE = 1  # Cada 1s, comprobamos el Engine 

# --- Funciones de Conexion y Control Local (Monitorizacion del Engine) ---
def monitor_engine_health(engine_host, engine_port, cp_id, central_socket_ref):
    """
    Supervisa continuamente el estado del Engine
    Si el Engine no responde o devuelve un error, se notifica a la Central.
    Si la Central no está disponible, se limpia la conexión para forzar una reconexión.
    """
    print(f"Iniciando monitorizacion del Engine en {engine_host}:{engine_port}")
    engine_failed = False  # bandera para no repetir FAULTs innecesarios
    while True:
        try:
            #1. Creamos un socket temporal para comprobar la salud del Engine
            # Usaremos una conexion temporal simple creando un nuevo socket TCP/IP
                                        #protocolo IP v4  #protocolo TCP (conexion orientada)
            engine_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
            #Intenta conectarse al servidor (el Engine), usando la IP y el puerto.
            engine_socket.connect((engine_host, engine_port)) # si no hay nadie lanza excepcion ConnectionRefusedError
            #Esperar respuesta del Engine (maximo 1 segundo)
            engine_socket.settimeout(1.5) # indica que la operacion recv de engine_socket deber esperar como max 1.5s y si no hay respuesta en ese tiempo, lanza socket.timeout
            
          
            #2. Enviar mensaje(en bytes) de comprobacion de salud al Engine
                                                        #transforma el string a bytes(sockets solo trabajan con bytes)
            engine_socket.sendall(f"HEALTH_CHECK#{cp_id}".encode('utf-8')) #EJ: b"HEALTH_CHECK#CP01"                        
            
            #3. Esperamos la respuesta ("OK" o "KO")
                                    #lee hasta 1024 bytes de respuesta #convierte los bytes recibidos a string #elimina espacios, saltos de linea etc.
            response = engine_socket.recv(1024).decode('utf-8').strip() #b"OK\n"  → decode() → "OK\n" → strip() → "OK"
            
            if response != "OK":
                #  Engine devolvió KO → reportar fallo si no se había hecho
                if not engine_failed:
                    print(f"[Monitor] Engine KO → Enviando FAULT#{cp_id}")
                    if central_socket_ref[0]:
                        try:
                            central_socket_ref[0].sendall(f"FAULT#{cp_id}".encode("utf-8"))
                        except Exception as e:
                            print(f"[Monitor] Error enviando FAULT: {e}")
                    engine_failed = True

            else:
                # Engine responde OK → si antes estaba en fallo, enviar RECOVER
                if engine_failed:
                    print(f"[Monitor] Engine recuperado → Enviando RECOVER#{cp_id}")
                    if central_socket_ref[0]:
                        try:
                            central_socket_ref[0].sendall(f"RECOVER#{cp_id}".encode("utf-8"))
                        except Exception as e:
                            print(f"[Monitor] Error enviando RECOVER: {e}")
                    engine_failed = False
                
                #Fallo de red inesperado
        except (socket.error, ConnectionRefusedError, socket.timeout):
            # No se pudo contactar con el Engine
            if not engine_failed:
                print(f"[Monitor] Engine inalcanzable → Enviando FAULT#{cp_id}")
                if central_socket_ref[0]:
                    try:
                        central_socket_ref[0].sendall(f"FAULT#{cp_id}".encode("utf-8"))
                    except Exception as e:
                        print(f"[Monitor] No se pudo enviar FAULT: {e}")
                engine_failed = True
            
        #Captura cualquier otro error inesperado que no sea de red    
        except Exception as e:
            print(f"Error inesperado en la monitorizacion del Engine: {e}")
            
        finally:
            #5. Pase lo que pase (exito o error), nos aseguramos de cerrar el socket del Engine en cada ciclo 
            try:
                engine_socket.close()
            except Exception:
                pass
            #6. Esperar un poco antes del siguiente chequeo
            time.sleep(HEARTBEAT_INTERVAL_TO_ENGINE) # Comprobamos cada 1 segundo


#Funcion que se conecta al servidor del engine, le manda el comando recibido desde la central, recibe un ACK/NACK y le envia la respuesta a la central
def send_command_to_engine(engine_host, engine_port, command, central_socket):
    """Envía el comando PARAR o REANUDAR al Engine local y responde con ACK/NACK a la Central."""
    try:
        engine_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        engine_socket.connect((engine_host, engine_port))
        engine_socket.sendall(command.encode('utf-8'))
        response = engine_socket.recv(1024).decode('utf-8').strip()
        engine_socket.close()

        if response.startswith("ACK"):
            central_socket.sendall(f"ACK#{command}".encode('utf-8'))
            print(f"[Monitor] Engine confirmó comando {command}. ACK enviado a Central.")
        else:
            central_socket.sendall(f"NACK#{command}".encode('utf-8'))
            print(f"[Monitor] Engine rechazó comando {command}. NACK enviado a Central.")

    except Exception as e:
        print(f"[Monitor] Error al contactar con Engine ({command}): {e}")
        central_socket.sendall(f"NACK#{command}".encode('utf-8'))

def handle_engine_communication(engine_host, engine_port, cp_id, central_ip, central_port):
    """Maneja la comunicación con el Engine para verificar asignación de driver."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server_socket.bind(('127.0.0.1', engine_port + 1000))  # Puerto diferente para evitar conflictos
        server_socket.listen(1)
        print(f"[Monitor] Servidor de comunicación con Engine escuchando en puerto {engine_port + 1000}")
        
        while True:
            try:
                client_socket, address = server_socket.accept()
                data = client_socket.recv(1024).decode('utf-8').strip()
                
                if data.startswith("CHECK_DRIVER_ASSIGNMENT"):
                    # Verificar asignación de driver consultando a la Central
                    try:
                        central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        central_socket.connect((central_ip, central_port))
                        central_socket.sendall(f"CHECK_DRIVER#{cp_id}".encode('utf-8'))
                        response = central_socket.recv(1024).decode('utf-8').strip()
                        central_socket.close()
                        client_socket.sendall(response.encode('utf-8'))
                    except Exception as e:
                        print(f"[Monitor] Error verificando asignación de driver: {e}")
                        client_socket.sendall(b"NO_DRIVER")
                elif data.startswith("CHECK_SESSION"):
                    # Verificar sesión activa autorizada consultando a la Central
                    try:
                        central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        central_socket.connect((central_ip, central_port))
                        central_socket.sendall(f"CHECK_SESSION#{cp_id}".encode('utf-8'))
                        response = central_socket.recv(1024).decode('utf-8').strip()
                        central_socket.close()
                        client_socket.sendall(response.encode('utf-8'))
                    except Exception as e:
                        print(f"[Monitor] Error verificando sesión activa: {e}")
                        client_socket.sendall(b"NO_SESSION")
                else:
                    client_socket.sendall(b"UNKNOWN_COMMAND")
                    
                client_socket.close()
            except Exception as e:
                print(f"[Monitor] Error en comunicación con Engine: {e}")
                
    except Exception as e:
        print(f"[Monitor] Error iniciando servidor de comunicación: {e}")
    finally:
        server_socket.close()



# --- Funciones de Conexion y Reporte a la Central ---
def start_central_connection(central_host, central_port, cp_id, location, engine_host, engine_port):
    """Maneja la conexion principal con la Central (registro y gestion de comandos).
    Si la conexión se pierde (por fallo de red o porque el monitor lo marca como None),
    se intenta reconectar automáticamente tras unos segundos."""
    
    #Se crea una lista mutable que guarda el socket con la Central.
    #Asi puede ser compartido con el hilo del vigilante(el que comprueba la vida del engine).
    #Usar lista permite que ambos hilos trabajen sobre la misma referencia al socket.
    central_socket_ref = [None] 
    parado = [False]  # bandera para indicar que el CP ha sido parado
    # Bucle infinito de reconexion
    while True:
        try:
            # 1. Intentar conexión con la Central
            central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            print(f"Intentando conectar CP '{cp_id}' a Central {central_host}:{central_port}...")
            central_socket.connect((central_host, central_port))
            # Guardamos la referencia a la conexion en la lista para que el otro hilo (monitor vigilante) la pueda usar.
            central_socket_ref[0] = central_socket 
            print("¡Conectado al servidor central! Enviando registro.")
            
            #2 Enviamos mensaje de registro al servidor Central.
            # Usar precio por defecto para nuevos CPs
            default_price = 0.25  # €/kWh por defecto
            register_message = f"REGISTER#{cp_id}#{location}#{default_price}"
            central_socket.sendall(register_message.encode('utf-8'))
            print(f"[Monitor] Enviado REGISTER (with price={default_price}): {register_message}")
            # Aseguramos modo blocking normal (sin timeout de conexión)
            central_socket.settimeout(5) # Timeout para recv (espera max 5s por datos)
            
            # Esperar un momento para que CENTRAL procese el registro antes de iniciar monitoreo
            time.sleep(3)


            # 3. Iniciar el hilo de Monitorizacion Local si aun no esta activo
            #Si no existe ya un hilo llamado EngineMonitor, lo creamos.
                                                          #devuelve una lista de todos los hilos activos en este momento  
            if not any(t.name == "EngineMonitor" for t in threading.enumerate()):
                # Verificar que el Engine esté listo antes de iniciar monitoreo
                try:
                    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    test_socket.settimeout(2)
                    test_socket.connect((engine_host, engine_port))
                    test_socket.close()
                    print("[Monitor] Engine verificado como listo. Iniciando monitoreo.")
                except Exception as e:
                    print(f"[Monitor] Engine no está listo aún: {e}. Esperando...")
                    time.sleep(5)  # Esperar más tiempo si el Engine no está listo
                
                #Creamos un nuevo hilo que corre monitor_engine_health(...).
                monitor_thread = threading.Thread(
                    target=monitor_engine_health, 
                    args=(engine_host, engine_port, cp_id, central_socket_ref),
                    name="EngineMonitor"
                )
                #Marcamos el hilo como daemon: se cierra cuando el programa principal termina.
                monitor_thread.daemon = True
                monitor_thread.start()
                print("[Monitor] Hilo de monitorización del Engine iniciado.")
                
                # Iniciar servidor de comunicación con Engine
                comm_thread = threading.Thread(
                    target=handle_engine_communication,
                    args=(engine_host, engine_port, cp_id, central_host, central_port),
                    name="EngineCommunication"
                )
                comm_thread.daemon = True
                comm_thread.start()
                print("[Monitor] Servidor de comunicación con Engine iniciado.")
                

            # 4. Bucle de escucha de comandos de la Central (Parar/Reanudar)
            while True:
                # Si el monitor ha puesto central_socket_ref[0] = None → reconexión necesaria
                if central_socket_ref[0] is None:
                    print("[Monitor] La conexión con la Central fue marcada como caída. Reconectando...")
                    break  # Salimos al while True externo para reintentar conexión
                
                # Esperamos datos de la Central
                try:
                    # La central puede enviar comandos en cualquier momento, hay que estar escuchando
                    data = central_socket.recv(1024)
                    #Si la Central cierra la conexion → data estara vacio → rompe el bucle.
                    if not data:
                        print("[Monitor] La Central cerró la conexión. Reintentando...")
                        break # Conexion cerrada por la Central
                except socket.timeout:
                    continue
                except socket.error as e:
                    print(f"[Monitor] Error de comunicación con la Central: {e}")
                    break  # Reconexión
                
                
                #Procesamos el comando recibido
                                                       #El split('#')[0] separa el codigo del comando: PARAR#CP01 → "PARAR"     REANUDAR#CP01 → "REANUDAR" 
                command = data.decode('utf-8').strip().split('#')[0]
                print(f"[Monitor] Comando recibido desde Central: '{command}'")

                # Si el CP está parado, solo permitimos REANUDAR
                if parado[0] and command != 'REANUDAR':
                    print(f"[Monitor] Ignorando comando '{command}' mientras CP está parado.")
                    continue


                if command == 'PARAR':
                    print("[Monitor] COMANDO CENTRAL: 'PARAR' recibido → enviando a Engine...")
                    # Nos conectamos al Engine para ejecutar el comando
                    send_command_to_engine(engine_host, engine_port, "PARAR", central_socket)
                    # Después de PARAR, marcamos que no se debe reconectar
                    parado[0] = True
                    print("[Monitor] CP marcado como parado. Esperando REANUDAR...")
                
                elif command == 'REANUDAR':
                    print("[Monitor] COMANDO CENTRAL: 'REANUDAR' recibido → enviando a Engine...")
                    # Nos conectamos al Engine para ejecutar el comando
                    send_command_to_engine(engine_host, engine_port, "REANUDAR", central_socket)
                    parado[0] = False
                    print("[Monitor] CP reanudado. Reanudando operaciones.")

                elif command == 'AUTORIZAR_SUMINISTRO':
                    print(f"[Monitor] COMANDO CENTRAL: 'AUTORIZAR_SUMINISTRO' recibido → enviando a Engine...")
                    # Nos conectamos al Engine para ejecutar el comando
                    send_command_to_engine(engine_host, engine_port, data, central_socket)
                    print("[Monitor] Comando de autorización de suministro enviado al Engine.")

                else:
                    print(f"Comando desconocido recibido: {command}")

        #Si falla la conexion, esperamos 10 segundos y lo intentamos de nuevo.        
        except (ConnectionRefusedError, socket.error, ConnectionResetError) as e:
            print(f"[Monitor] Error de conexion con Central: {e}. \nReintentando en 10s...")
            time.sleep(10)
        except Exception as e:
            print(f"Error inesperado en el socket de la Central: {e}")
            break
        #Asegura cerrar el socket si algo sale mal
        finally:
            if central_socket_ref[0]:
                try:
                    central_socket_ref[0].close()
                except Exception:
                        pass
                central_socket_ref[0] = None # Limpiamos la referencia antes de reintentar
            time.sleep(5)  # Esperar un poco antes de reintentar

# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    if len(sys.argv) != 6:
        print("Uso: py EV_CP_M.py <IP_CENTRAL> <PUERTO_CENTRAL> <IP_ENGINE> <PUERTO_ENGINE> <ID_CP>")
        print("Ejemplo: py EV_CP_M.py localhost 8000 localhost 8001 MAD-01")
        sys.exit(1)

    #Extraemos los argumentos
    CENTRAL_IP = sys.argv[1]
    ENGINE_IP = sys.argv[3]
    CP_ID = sys.argv[5]
    
    #Convertimos los puertos desde texto a número entero.
    try:
        CENTRAL_PORT = int(sys.argv[2])
        ENGINE_PORT = int(sys.argv[4])
    #Si alguien escribe letras → lanza error y sale
    except ValueError:
        print("Error: Los puertos deben ser numeros enteros.")
        sys.exit(1)

    locations = {"MAD-01": "C/ Serrano 10", "VAL-03": "Plaza del Ayuntamiento 1", "BCN-05": "Las Ramblas 55"}
    LOCATION = locations.get(CP_ID, "Ubicacion Desconocida")

    start_central_connection(CENTRAL_IP, CENTRAL_PORT, CP_ID, LOCATION, ENGINE_IP, ENGINE_PORT)