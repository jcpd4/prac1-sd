# Fichero: EV_Registry.py
# Módulo de Registro (Release 2) - API REST
from flask import Flask, request, jsonify
import requests
import database
import uuid # Para generar tokens únicos
import sys
from database import log_audit_event # Seva: Importar función de auditoría
from cryptography.fernet import Fernet # Seva: Importar Fernet para manejo de claves simétricas
# Configuración
# El Registry usará un puerto diferente al de la Central (8000) para no chocar.
# Usaremos el 6000.
REGISTRY_PORT = 6000 

app = Flask(__name__)

# Inicializamos la DB al arrancar para asegurar que existen las tablas
database.setup_database()

# Seva: URL para enviar logs a la Central (Asumimos localhost:5000)
CENTRAL_LOG_URL = "http://127.0.0.1:5000/api/log"

def enviar_log_central(msg):
    """Envía un log a la Central para visualización en el Front."""
    try:
        requests.post(CENTRAL_LOG_URL, json={"source": "REGISTRY", "msg": msg}, timeout=1)
    except:
        pass # Si falla (ej. Central apagada), no bloquear el Registry
#---------------------------------------------------------------------



# --- ENDPOINT 1: Registro de CP (POST /register) ---
@app.route('/register', methods=['POST'])
def register_cp():
    """
    Recibe { "id": "MAD-01", "location": "Calle X" }
    Devuelve { "token": "uuid-seguro", "message": "OK" }
    """
    data = request.json
    
    # 1. Validar datos de entrada
    if not data or 'id' not in data or 'location' not in data:
        return jsonify({"error": "Datos incompletos. Se requiere 'id' y 'location'"}), 400
    
    cp_id = data['id']
    location = data['location']
    
    print(f"[Registry] Petición de alta recibida para: {cp_id}")

    try:
        # 2. Generar credencial segura (Token)
        token = str(uuid.uuid4())
        # Seva: Generar Clave Simétrica ÚNICA para el CP
        symmetric_key = Fernet.generate_key().decode()
        
        # 3. Guardar en Base de Datos (Compartida con Central)
        # Usamos las funciones de database.py
        database.register_cp(cp_id, location) # Crea el registro
        database.update_cp_token(cp_id, token) # Guarda el token
        database.update_cp_symmetric_key(cp_id, symmetric_key) # Seva: Guardar la nueva clave simétrica en la BD
        
        print(f"[Registry] CP {cp_id} registrado con éxito. Token y Clave Simétrica generados.")
        enviar_log_central(f"CP {cp_id} REGISTRADO.\n   >> Token: {token}\n   >> Clave: {symmetric_key}")
        log_audit_event(
            source_ip=request.remote_addr,  
            action="CP_ALTA_REGISTRO",
            description=f"CP registrado exitosamente. Credenciales (Token+Clave) generadas y entregadas.",
            cp_id=cp_id
        )
        
        # 4. Devolver el token y la clave simétrica al CP
        return jsonify({
            "message": "CP registrado correctamente",
            "token": token,
            "symmetric_key": symmetric_key 
        }), 200
        
    except Exception as e:
        print(f"[Registry] Error interno: {e}")
        return jsonify({"error": f"Error interno del servidor: {str(e)}"}), 500

# --- ENDPOINT 2: Baja de CP (POST /unregister) ---
@app.route('/unregister', methods=['POST'])
def unregister_cp():
    """
    Recibe { "id": "MAD-01", "token": "su-token-actual" }
    Devuelve { "message": "CP eliminado" }
    """
    data = request.json
    if not data or 'id' not in data:
        return jsonify({"error": "Falta el ID del CP"}), 400
        
    cp_id = data['id']
    # Nota: En un sistema real, aquí verificaríamos que el token coincide antes de borrar
    
    print(f"[Registry] Petición de baja recibida para: {cp_id}")
    
    if database.delete_cp(cp_id):
        # Seva: AUDITORÍA: BAJA DE CP EXITOSA ***
        # La IP de origen es la que llama a este endpoint 
        # Seva: AUDITORÍA: BAJA DE CP EXITOSA ***
        log_audit_event(
            source_ip=request.remote_addr, 
            action="CP_BAJA_EXITOSA",
            description="CP eliminado del sistema por solicitud del Registry.",
            cp_id=cp_id
        )
        # *************************************
        print(f"[Registry] CP {cp_id} eliminado.")
        enviar_log_central(f"CP {cp_id} dado de baja correctamente.")
        return jsonify({"message": f"CP {cp_id} dado de baja correctamente"}), 200
    else:
        return jsonify({"error": "CP no encontrado o no se pudo eliminar"}), 404

# --- Arranque del Servidor ---
if __name__ == '__main__':
    print(f"--- EV_Registry (API REST SEGURO) iniciado en puerto {REGISTRY_PORT} ---")
    # AÑADIMOS: ssl_context='adhoc'
    # Esto habilita HTTPS usando un certificado generado en memoria
    app.run(host='0.0.0.0', port=REGISTRY_PORT, debug=True, ssl_context='adhoc')