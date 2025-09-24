# Fichero: ev_central.py
import socket
import threading
import sys
import time
import os
import database # Importamos nuestro módulo de base de datos

# --- Funciones del Panel de Monitorización ---
def clear_screen():
    """Limpia la pantalla de la terminal."""
    os.system('cls' if os.name == 'nt' else 'clear')

def get_status_color(status):
    """Devuelve un 'color' para el panel basado en el estado."""
    # Estos son códigos de escape ANSI para colores en la terminal
    colors = {
        "ACTIVADO": "\033[92m",    # Verde
        "DESCONECTADO": "\033[90m", # Gris
        "SUMINISTRANDO": "\033[94m",# Azul
        "AVERIADO": "\033[91m",     # Rojo
        "FUERA_DE_SERVICIO": "\033[93m" # Naranja
    }
    END_COLOR = "\033[0m"
    return f"{colors.get(status, '')}{status}{END_COLOR}"

def display_panel():
    """Muestra el estado de todos los CPs en un panel."""
    while True:
        clear_screen()
        print("--- PANEL DE MONITORIZACIÓN DE EV CHARGING ---")
        print("="*50)
        
        all_cps = database.get_all_cps()
        if not all_cps:
            print("No hay Puntos de Recarga registrados.")
        else:
            print(f"{'ID':<10} | {'UBICACIÓN':<25} | {'ESTADO':<20}")
            print("-"*50)
            for cp in all_cps:
                colored_status = get_status_color(cp['status'])
                print(f"{cp['id']:<10} | {cp['location']:<25} | {colored_status}")

        print("="*50)
        print(f"Última actualización: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(2) # El panel se refresca cada 2 segundos

# --- Funciones del Servidor de Sockets ---
def handle_client(client_socket, address):
    """Maneja la conexión de un único CP."""
    print(f"Nueva conexión aceptada desde {address}")
    cp_id = None
    try:
        # 1. El CP debe enviar un mensaje de registro al conectarse
        # Formato esperado: "REGISTER#ID_CP#UBICACION"
        message = client_socket.recv(1024).decode('utf-8')
        parts = message.strip().split('#')

        if len(parts) == 3 and parts[0] == 'REGISTER':
            cp_id = parts[1]
            location = parts[2]
            
            # 2. Registrar en la BD y actualizar estado a ACTIVADO
            database.register_cp(cp_id, location)
            database.update_cp_status(cp_id, 'ACTIVADO')
            print(f"CP '{cp_id}' registrado/actualizado desde {address}.")
            
            # 3. Mantener la conexión abierta para futuros mensajes (heartbeat, averías, etc.)
            while True:
                # Esperamos a recibir más datos. Si no llega nada, recv() se bloquea.
                # Si el cliente se desconecta, recv() devolverá 0 bytes y saldrá del bucle.
                data = client_socket.recv(1024)
                if not data:
                    break # Conexión cerrada por el cliente
                # Aquí se procesarían futuros mensajes como "FAULT#CP01"
                
    except Exception as e:
        print(f"Error con el cliente {address}: {e}")
    finally:
        # 4. Si la conexión se pierde, marcar el CP como DESCONECTADO
        if cp_id:
            print(f"Conexión con CP '{cp_id}' perdida.")
            database.update_cp_status(cp_id, 'DESCONECTADO')
        client_socket.close()

def start_server(host, port):
    """Inicia el servidor de sockets para escuchar a los CPs."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((host, port))
    server_socket.listen(5) # Acepta hasta 5 conexiones en cola
    print(f"EV_Central escuchando en {host}:{port}")

    while True:
        # Acepta una nueva conexión (esta llamada es bloqueante)
        client_socket, address = server_socket.accept()
        # Inicia un nuevo hilo para manejar al cliente, así el servidor puede
        # seguir aceptando más conexiones sin esperar.
        client_thread = threading.Thread(target=handle_client, args=(client_socket, address))
        client_thread.start()

# --- Punto de Entrada Principal ---
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python ev_central.py <puerto>")
        sys.exit(1)

    try:
        PORT = int(sys.argv[1])
        HOST = '0.0.0.0' # Escucha en todas las interfaces de red disponibles

        # 1. Configurar la base de datos
        database.setup_database()

        # 2. Iniciar el servidor en un hilo separado
        server_thread = threading.Thread(target=start_server, args=(HOST, PORT))
        server_thread.daemon = True # El hilo del servidor morirá si el programa principal termina
        server_thread.start()

        # 3. Iniciar el panel de monitorización en el hilo principal
        display_panel()

    except ValueError:
        print("Error: El puerto debe ser un número entero.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nServidor detenido por el usuario.")
        sys.exit(0)