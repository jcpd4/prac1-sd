# Fichero: ec_cp_m.py
import socket
import sys
import time

def start_client(server_host, server_port, cp_id):
    """Inicia el cliente monitor, se conecta y envía heartbeats."""

    # Un pequeño diccionario para simular las ubicaciones de los CPs
    # En un sistema real, esto estaría en un fichero de configuración
    locations = {
        "MAD-01": "C/ Serrano 10",
        "VAL-03": "Plaza del Ayuntamiento 1",
        "BCN-05": "Las Ramblas 55"
    }
    location = locations.get(cp_id, "Ubicación Desconocida")

    # Bucle infinito para que el cliente intente reconectarse si pierde la conexión
    while True:
        try:
            # 1. Crear el socket y conectarse al servidor
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            print(f"Intentando conectar CP '{cp_id}' a {server_host}:{server_port}...")
            client_socket.connect((server_host, server_port))
            print("¡Conectado al servidor central!")

            # 2. Enviar el mensaje de registro una vez conectado
            register_message = f"REGISTER#{cp_id}#{location}"
            client_socket.sendall(register_message.encode('utf-8'))
            print(f"CP '{cp_id}' registrado en la central. Ubicación: {location}")

            # 3. Bucle para enviar un 'heartbeat' cada 15 segundos
            while True:
                time.sleep(15)
                heartbeat_message = f"HEARTBEAT#{cp_id}"
                client_socket.sendall(heartbeat_message.encode('utf-8'))
                # Opcional: imprimir un mensaje para saber que sigue funcionando
                # print("... heartbeat enviado")

        except ConnectionRefusedError:
            print("Conexión rechazada. ¿Está el servidor EV_Central en funcionamiento?")
            print("Reintentando en 10 segundos...")
            time.sleep(10)
        except (socket.error, ConnectionResetError) as e:
            print(f"Se ha perdido la conexión con el servidor: {e}")
            print("Reintentando conectar en 10 segundos...")
            time.sleep(10)
        except Exception as e:
            print(f"Ha ocurrido un error inesperado: {e}")
            break # Salir en caso de un error no manejado
        finally:
            # Asegurarse de que el socket se cierra antes de reintentar
            client_socket.close()


if __name__ == "__main__":
    # --- Comprobación de los parámetros de entrada ---
    if len(sys.argv) != 4:
        print("Uso: py ec_cp_m.py <IP_CENTRAL> <PUERTO_CENTRAL> <ID_CP>")
        print("Ejemplo: py ec_cp_m.py localhost 8000 MAD-01")
        sys.exit(1)

    SERVER_IP = sys.argv[1]
    try:
        SERVER_PORT = int(sys.argv[2])
    except ValueError:
        print("Error: El puerto debe ser un número.")
        sys.exit(1)
    CP_ID = sys.argv[3]

    # Iniciar el cliente
    start_client(SERVER_IP, SERVER_PORT, CP_ID)