import socket # para tener una comunicacion por red
import sys # para usar parametros desde consola
import time # para manejar el tiempo
import threading # para poder ejecutar en paralelo
import database

# Constantes de configuracion (ajustalas segun tu necesidad de prueba)
HEARTBEAT_INTERVAL_TO_CENTRAL = 15 # Cada 15s, informamos a la Central (No usado aun)
HEARTBEAT_INTERVAL_TO_ENGINE = 1  # Cada 1s, comprobamos el Engine 

#HILO 1: Funciones de Conexion y Control Local (Monitorizacion del Engine)
def monitor_engine_health(engine_host, engine_port, cp_id, central_socket_ref):
    """
    Supervisa continuamente el estado del Engine
    Si el Engine no responde o devuelve un error, se notifica a la Central.
    Si la Central no está disponible, se limpia la conexión para forzar una reconexión.
    """
    #Paso 1: Inicializar variables de control
    print(f"Iniciando monitorizacion del Engine en {engine_host}:{engine_port}")
    engine_failed = False  # bandera para no repetir FAULTs innecesarios
    
    #Paso 2: Bucle principal de monitorización
    while True:
        try:
            #Paso 2.1: Crear un socket temporal para comprobar la salud del Engine
            # Usaremos una conexion temporal simple creando un nuevo socket TCP/IP
            #protocolo IP v4  #protocolo TCP (conexion orientada)
            engine_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
            #Paso 2.2: Intentar conectarse al servidor (el Engine)
            #Intenta conectarse al servidor (el Engine), usando la IP y el puerto.
            engine_socket.connect((engine_host, engine_port)) # si no hay nadie lanza excepcion ConnectionRefusedError
            #Paso 2.3: Configurar timeout para la respuesta
            #Esperar respuesta del Engine (maximo 1 segundo)
            engine_socket.settimeout(1.5) # indica que la operacion recv de engine_socket deber esperar como max 1.5s y si no hay respuesta en ese tiempo, lanza socket.timeout
            
          
            #Paso 2.4: Enviar mensaje de comprobacion de salud al Engine
            #transforma el string a bytes(sockets solo trabajan con bytes)
            engine_socket.sendall(f"HEALTH_CHECK#{cp_id}".encode('utf-8')) #EJ: b"HEALTH_CHECK#CP01"                        
            
            #Paso 2.5: Esperar la respuesta del Engine
            #lee hasta 1024 bytes de respuesta #convierte los bytes recibidos a string #elimina espacios, saltos de linea etc.
            response = engine_socket.recv(1024).decode('utf-8').strip() #b"OK\n"  → decode() → "OK\n" → strip() → "OK"
            
            #Paso 2.6: Procesar la respuesta del Engine
            if response != "OK":
                #Paso 2.6.1: Engine devolvió KO → reportar fallo si no se había hecho
                if not engine_failed:
                    print(f"[Monitor] Engine KO → Enviando FAULT#{cp_id}")
                    #Paso 2.6.1.1: Enviar FAULT a Central si está conectada
                    if central_socket_ref[0]:
                        try:
                            central_socket_ref[0].sendall(f"FAULT#{cp_id}".encode("utf-8"))
                        except Exception as e:
                            print(f"[Monitor] Error enviando FAULT: {e}")
                    engine_failed = True

            else:
                #Paso 2.6.2: Engine responde OK → si antes estaba en fallo, enviar RECOVER
                if engine_failed:
                    print(f"[Monitor] Engine recuperado → Enviando RECOVER#{cp_id}")
                    #Paso 2.6.2.1: Enviar RECOVER a Central si está conectada
                    if central_socket_ref[0]:
                        try:
                            central_socket_ref[0].sendall(f"RECOVER#{cp_id}".encode("utf-8"))
                        except Exception as e:
                            print(f"[Monitor] Error enviando RECOVER: {e}")
                    engine_failed = False
                
                #Paso 2.7: Manejar errores de red inesperados
        except (socket.error, ConnectionRefusedError, socket.timeout):
            #Paso 2.7.1: No se pudo contactar con el Engine
            if not engine_failed:
                print(f"[Monitor] Engine inalcanzable → Enviando FAULT#{cp_id}")
                #Paso 2.7.1.1: Enviar FAULT a Central si está conectada
                if central_socket_ref[0]:
                    try:
                        central_socket_ref[0].sendall(f"FAULT#{cp_id}".encode("utf-8"))
                    except Exception as e:
                        print(f"[Monitor] No se pudo enviar FAULT: {e}")
                engine_failed = True
            
        #Paso 2.8: Capturar cualquier otro error inesperado que no sea de red    
        except Exception as e:
            print(f"Error inesperado en la monitorizacion del Engine: {e}")
            
        finally:
            #Paso 2.9: Limpiar recursos
            #Pase lo que pase (exito o error), nos aseguramos de cerrar el socket del Engine en cada ciclo 
            try:
                engine_socket.close()
            except Exception:
                pass
            #Paso 2.10: Esperar antes del siguiente chequeo
            #Esperar un poco antes del siguiente chequeo
            time.sleep(HEARTBEAT_INTERVAL_TO_ENGINE) # Comprobamos cada 1 segundo


#HILO 2: Funcion que se conecta al servidor del engine, le manda el comando recibido desde la central, recibe un ACK/NACK y le envia la respuesta a la central
def send_command_to_engine(engine_host, engine_port, command, central_socket):
    """Envía el comando PARAR o REANUDAR al Engine local y responde con ACK/NACK a la Central."""
    try:
        #Paso 1: Crear conexión con el Engine
        engine_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        #Paso 1.1: Conectar al Engine
        engine_socket.connect((engine_host, engine_port))
        #Paso 1.2: Enviar comando al Engine
        engine_socket.sendall(command.encode('utf-8'))
        #Paso 1.3: Recibir respuesta del Engine
        response = engine_socket.recv(1024).decode('utf-8').strip()
        #Paso 1.4: Cerrar conexión con el Engine
        engine_socket.close()

        #Paso 2: Procesar respuesta del Engine
        if response.startswith("ACK"):
            #Paso 2.1: Engine confirmó comando → enviar ACK a Central
            central_socket.sendall(f"ACK#{command}".encode('utf-8'))
            print(f"[Monitor] Engine confirmó comando {command}. ACK enviado a Central.")
        else:
            #Paso 2.2: Engine rechazó comando → enviar NACK a Central
            central_socket.sendall(f"NACK#{command}".encode('utf-8'))
            print(f"[Monitor] Engine rechazó comando {command}. NACK enviado a Central.")

    except Exception as e:
        #Paso 3: Manejar errores de comunicación
        print(f"[Monitor] Error al contactar con Engine ({command}): {e}")
        #Paso 3.1: Enviar NACK a Central por error
        central_socket.sendall(f"NACK#{command}".encode('utf-8'))

#HILO 3: Funcion para manejar la comunicación con el Engine
def handle_engine_communication(engine_host, engine_port, cp_id, central_ip, central_port):
    """Maneja la comunicación con el Engine para verificar asignación de driver."""
    #Paso 1: Crear servidor de comunicación con Engine
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        #Paso 1.1: Configurar servidor en puerto diferente para evitar conflictos
        server_socket.bind(('127.0.0.1', engine_port + 1000))  # Puerto diferente para evitar conflictos
        #Paso 1.2: Poner servidor a escuchar conexiones
        server_socket.listen(1)
        print(f"[Monitor] Servidor de comunicación con Engine escuchando en puerto {engine_port + 1000}")
        
        #Paso 2: Bucle principal de comunicación con Engine
        while True:
            try:
                #Paso 2.1: Aceptar conexión del Engine
                client_socket, address = server_socket.accept()
                #Paso 2.2: Recibir datos del Engine
                data = client_socket.recv(1024).decode('utf-8').strip()
                
                #Paso 2.3: Procesar comandos del Engine
                #Paso 2.3.1: Comando CHECK_DRIVER_ASSIGNMENT
                if data.startswith("CHECK_DRIVER_ASSIGNMENT"):
                    #Paso 2.3.1.1: Verificar asignación de driver consultando a la Central
                    try:
                        #Paso 2.3.1.1.1: Crear conexión con Central
                        central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        central_socket.connect((central_ip, central_port))
                        #Paso 2.3.1.1.2: Enviar consulta de driver a Central
                        central_socket.sendall(f"CHECK_DRIVER#{cp_id}".encode('utf-8'))
                        #Paso 2.3.1.1.3: Recibir respuesta de Central
                        response = central_socket.recv(1024).decode('utf-8').strip()
                        #Paso 2.3.1.1.4: Cerrar conexión con Central
                        central_socket.close()
                        #Paso 2.3.1.1.5: Enviar respuesta al Engine
                        client_socket.sendall(response.encode('utf-8'))
                    except Exception as e:
                        print(f"[Monitor] Error verificando asignación de driver: {e}")
                        #Paso 2.3.1.1.6: Enviar respuesta de error al Engine
                        client_socket.sendall(b"NO_DRIVER")
                        
                #Paso 2.3.2: Comando CHECK_SESSION
                elif data.startswith("CHECK_SESSION"):
                    #Paso 2.3.2.1: Verificar sesión activa autorizada consultando a la Central
                    try:
                        #Paso 2.3.2.1.1: Crear conexión con Central
                        central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        central_socket.connect((central_ip, central_port))
                        #Paso 2.3.2.1.2: Enviar consulta de sesión a Central
                        central_socket.sendall(f"CHECK_SESSION#{cp_id}".encode('utf-8'))
                        #Paso 2.3.2.1.3: Recibir respuesta de Central
                        response = central_socket.recv(1024).decode('utf-8').strip()
                        #Paso 2.3.2.1.4: Cerrar conexión con Central
                        central_socket.close()
                        #Paso 2.3.2.1.5: Enviar respuesta al Engine
                        client_socket.sendall(response.encode('utf-8'))
                    except Exception as e:
                        print(f"[Monitor] Error verificando sesión activa: {e}")
                        #Paso 2.3.2.1.6: Enviar respuesta de error al Engine
                        client_socket.sendall(b"NO_SESSION")
                        
                #Paso 2.3.3: Comando desconocido
                else:
                    #Paso 2.3.3.1: Enviar respuesta de comando desconocido
                    client_socket.sendall(b"UNKNOWN_COMMAND")
                    
                #Paso 2.4: Cerrar conexión con Engine
                client_socket.close()
            except Exception as e:
                print(f"[Monitor] Error en comunicación con Engine: {e}")
                
    except Exception as e:
        print(f"[Monitor] Error iniciando servidor de comunicación: {e}")
    finally:
        #Paso 3: Limpiar recursos
        server_socket.close()



#HILO 4: Funciones de Conexion y Reporte a la Central
def start_central_connection(central_host, central_port, cp_id, location, engine_host, engine_port):
    """Maneja la conexion principal con la Central (registro y gestion de comandos).
    Si la conexión se pierde (por fallo de red o porque el monitor lo marca como None),
    se intenta reconectar automáticamente tras unos segundos."""
    
    #Paso 1: Inicializar variables de control
    #Se crea una lista mutable que guarda el socket con la Central.
    #Asi puede ser compartido con el hilo del vigilante(el que comprueba la vida del engine).
    #Usar lista permite que ambos hilos trabajen sobre la misma referencia al socket.
    central_socket_ref = [None] 
    parado = [False]  # bandera para indicar que el CP ha sido parado
    
    #Paso 2: Bucle infinito de reconexion
    while True:
        try:
            #Paso 2.1: Crear socket y conectar con la Central
            # socket() - "Creo un socket" → Intentar conexión con la Central
            central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            print(f"Intentando conectar CP '{cp_id}' a Central {central_host}:{central_port}...")
            #Paso 2.1.1: connect() - "Me conecto a 127.0.0.1:8000", es decir, a la Central
            central_socket.connect((central_host, central_port))
            #Paso 2.1.2: Guardar referencia a la conexión para compartir con otros hilos
            # Guardamos la referencia a la conexion en la lista para que el otro hilo (monitor vigilante) la pueda usar.
            central_socket_ref[0] = central_socket 
            print("¡Conectado al servidor central! Enviando registro.")
            

            #Paso 2.2: Enviar mensaje de registro a la Central
            # sendall() - "Envío un mensaje de registro al servidor Central"
            default_price = 0.25  # €/kWh por defecto
            register_message = f"REGISTER#{cp_id}#{location}#{default_price}"
            central_socket.sendall(register_message.encode('utf-8'))
            print(f"[Monitor] Enviado REGISTER (with price={default_price}): {register_message}")
            #Paso 2.2.1: Configurar timeout para recibir datos
            # Aseguramos modo blocking normal (sin timeout de conexión)
            central_socket.settimeout(5) # Timeout para recv (espera max 5s por datos)
            #Paso 2.2.2: Esperar para que Central procese el registro
            time.sleep(3) # Esperar un momento para que CENTRAL procese el registro antes de iniciar monitoreo
            


            #Paso 2.3: Iniciar el hilo de Monitorizacion Local si aun no esta activo
            #Si no existe ya un hilo llamado EngineMonitor, lo creamos.
                                                      #devuelve una lista de todos los hilos activos en este momento  
            if not any(t.name == "EngineMonitor" for t in threading.enumerate()):
                #Paso 2.3.1: Verificar que el Engine esté listo antes de iniciar monitoreo
                try:
                    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    test_socket.settimeout(2)
                    test_socket.connect((engine_host, engine_port))
                    test_socket.close()
                    print("[Monitor] Engine verificado como listo. Iniciando monitoreo.")
                except Exception as e:
                    print(f"[Monitor] Engine no está listo aún: {e}. Esperando...")
                    time.sleep(5)  # Esperar más tiempo si el Engine no está listo
                
                #Paso 2.3.2: Crear hilo de monitorización del Engine
                #Creamos un nuevo hilo que corre monitor_engine_health(...).
                monitor_thread = threading.Thread(
                    target=monitor_engine_health, 
                    args=(engine_host, engine_port, cp_id, central_socket_ref),
                    name="EngineMonitor"
                )
                #Paso 2.3.2.1: Marcar hilo como daemon
                #Marcamos el hilo como daemon: se cierra cuando el programa principal termina.
                monitor_thread.daemon = True
                #Paso 2.3.2.2: Iniciar hilo de monitorización
                monitor_thread.start()
                print("[Monitor] Hilo de monitorización del Engine iniciado.")
                
                #Paso 2.3.3: Crear hilo de comunicación con Engine
                # Iniciar servidor de comunicación con Engine
                comm_thread = threading.Thread(
                    target=handle_engine_communication,
                    args=(engine_host, engine_port, cp_id, central_host, central_port),
                    name="EngineCommunication"
                )
                #Paso 2.3.3.1: Marcar hilo como daemon
                comm_thread.daemon = True
                #Paso 2.3.3.2: Iniciar hilo de comunicación
                comm_thread.start()
                print("[Monitor] Servidor de comunicación con Engine iniciado.")
                

            #Paso 2.4: Bucle de escucha de comandos de la Central (Parar/Reanudar)
            while True:
                #Paso 2.4.1: Verificar si la conexión sigue activa
                # Si el monitor ha puesto central_socket_ref[0] = None → reconexión necesaria
                if central_socket_ref[0] is None:
                    print("[Monitor] La conexión con la Central fue marcada como caída. Reconectando...")
                    break  # Salimos al while True externo para reintentar conexión
                
                #Paso 2.4.2: Esperar datos de la Central
                try:
                    #Paso 2.4.2.1: La central puede enviar comandos en cualquier momento, hay que estar escuchando
                    data = central_socket.recv(1024)
                    #Paso 2.4.2.2: Si la Central cierra la conexion → data estara vacio → rompe el bucle
                    if not data:
                        print("[Monitor] La Central cerró la conexión. Reintentando...")
                        break # Conexion cerrada por la Central
                except socket.timeout:
                    continue
                except socket.error as e:
                    print(f"[Monitor] Error de comunicación con la Central: {e}")
                    break  # Reconexión
                
                
                #Paso 2.4.3: Procesar el comando recibido
                #El split('#')[0] separa el codigo del comando: PARAR#CP01 → "PARAR"     REANUDAR#CP01 → "REANUDAR" 
                command = data.decode('utf-8').strip().split('#')[0]
                print(f"[Monitor] Comando recibido desde Central: '{command}'")

                #Paso 2.4.4: Verificar estado del CP
                # Si el CP está parado, solo permitimos REANUDAR
                if parado[0] and command != 'REANUDAR':
                    print(f"[Monitor] Ignorando comando '{command}' mientras CP está parado.")
                    continue

                #Paso 2.4.5: Procesar comandos específicos
                #Paso 2.4.5.1: Comando PARAR
                if command == 'PARAR':
                    print("[Monitor] COMANDO CENTRAL: 'PARAR' recibido → enviando a Engine...")
                    #Paso 2.4.5.1.1: Conectar al Engine para ejecutar el comando
                    send_command_to_engine(engine_host, engine_port, "PARAR", central_socket)
                    #Paso 2.4.5.1.2: Después de PARAR, marcamos que no se debe reconectar
                    parado[0] = True
                    print("[Monitor] CP marcado como parado. Esperando REANUDAR...")
                
                #Paso 2.4.5.2: Comando REANUDAR
                elif command == 'REANUDAR':
                    print("[Monitor] COMANDO CENTRAL: 'REANUDAR' recibido → enviando a Engine...")
                    #Paso 2.4.5.2.1: Conectar al Engine para ejecutar el comando
                    send_command_to_engine(engine_host, engine_port, "REANUDAR", central_socket)
                    #Paso 2.4.5.2.2: Marcar CP como reanudado
                    parado[0] = False
                    print("[Monitor] CP reanudado. Reanudando operaciones.")

                #Paso 2.4.5.3: Comando AUTORIZAR_SUMINISTRO
                elif command == 'AUTORIZAR_SUMINISTRO':
                    print(f"[Monitor] COMANDO CENTRAL: 'AUTORIZAR_SUMINISTRO' recibido → enviando a Engine...")
                    #Paso 2.4.5.3.1: Conectar al Engine para ejecutar el comando
                    send_command_to_engine(engine_host, engine_port, data, central_socket)
                    print("[Monitor] Comando de autorización de suministro enviado al Engine.")

                #Paso 2.4.5.4: Comando desconocido
                else:
                    print(f"Comando desconocido recibido: {command}")

        #Paso 2.5: Manejar errores de conexión
        #Si falla la conexion, esperamos 10 segundos y lo intentamos de nuevo.        
        except (ConnectionRefusedError, socket.error, ConnectionResetError) as e:
            print(f"[Monitor] Error de conexion con Central: {e}. \nReintentando en 10s...")
            time.sleep(10)
        except Exception as e:
            print(f"Error inesperado en el socket de la Central: {e}")
            break
        #Paso 2.6: Limpiar recursos
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
    #Paso 1: Verificar argumentos de línea de comandos
    if len(sys.argv) != 6:
        print("Uso: py EV_CP_M.py <IP_CENTRAL> <PUERTO_CENTRAL> <IP_ENGINE> <PUERTO_ENGINE> <ID_CP>")
        print("Ejemplo: py EV_CP_M.py localhost 8000 localhost 8001 MAD-01")
        sys.exit(1)

    #Paso 2: Extraer argumentos de línea de comandos
    #Extraemos los argumentos
    CENTRAL_IP = sys.argv[1]
    ENGINE_IP = sys.argv[3]
    CP_ID = sys.argv[5]
    
    #Paso 2.1: Convertir puertos desde texto a número entero
    #Convertimos los puertos desde texto a número entero.
    try:
        CENTRAL_PORT = int(sys.argv[2])
        ENGINE_PORT = int(sys.argv[4])
    #Paso 2.2: Manejar errores de conversión
    #Si alguien escribe letras → lanza error y sale
    except ValueError:
        print("Error: Los puertos deben ser numeros enteros.")
        sys.exit(1)

    #Paso 3: Configurar ubicación del CP
    locations = {"MAD-01": "C/ Serrano 10", "VAL-03": "Plaza del Ayuntamiento 1", "BCN-05": "Las Ramblas 55"}
    LOCATION = locations.get(CP_ID, "Ubicacion Desconocida")

    #Paso 4: Iniciar conexión con la Central
    start_central_connection(CENTRAL_IP, CENTRAL_PORT, CP_ID, LOCATION, ENGINE_IP, ENGINE_PORT)