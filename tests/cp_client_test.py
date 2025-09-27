# Fichero de prueba: cp_client_test.py
import socket
import time

# --- Datos de este Punto de Recarga (CP) ---
CP_ID = "MAD-01"
CP_LOCATION = "C/ Serrano 10"

# --- Conexión al servidor CENTRAL ---
SERVER_HOST = 'localhost'
SERVER_PORT = 8000 # El mismo puerto que usaste para el servidor

# Mensaje de registro
register_message = f"REGISTER#{CP_ID}#{CP_LOCATION}"

print(f"Intentando conectar CP '{CP_ID}' a {SERVER_HOST}:{SERVER_PORT}...")
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.connect((SERVER_HOST, SERVER_PORT))
    print("Conectado. Enviando registro...")
    s.sendall(register_message.encode('utf-8'))
    print("Registro enviado. Manteniendo conexión activa...")
    
    # Mantenemos la conexión "viva" para que el servidor no nos marque como desconectado
    try:
        while True:
            time.sleep(10) # Simula estar activo
    except KeyboardInterrupt:
        print("Desconectando CP.")