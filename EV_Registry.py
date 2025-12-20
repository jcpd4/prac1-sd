# Fichero: EV_Registry.py
from flask import Flask, request, jsonify
import requests
import database
import uuid 
import sys
import json  
from database import log_audit_event 
from cryptography.fernet import Fernet 
REGISTRY_PORT = 6000 

app = Flask(__name__)
database.setup_database()

def get_network_config():
    try:
        with open('network_config.json', 'r') as f:
            return json.load(f)
    except:
        return {}

def enviar_log_central(msg):
    """Envía un log a la Central para visualización en el Front."""
    try:
        config = get_network_config()
        ip = config.get('central_ip', '127.0.0.1')
        port = config.get('central_api_port', 5000)
        url = f"http://{ip}:{port}/api/log"

        requests.post(CENTRAL_LOG_URL, json={"source": "REGISTRY", "msg": msg}, timeout=1)
    except:
        pass 



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
        symmetric_key = Fernet.generate_key().decode()
        
        # 3. Guardar en Base de Datos (Compartida con Central)
        database.register_cp(cp_id, location) 
        database.update_cp_token(cp_id, token) 
        database.update_cp_symmetric_key(cp_id, symmetric_key) 
        
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
    
    print(f"[Registry] Petición de baja recibida para: {cp_id}")
    
    if database.delete_cp(cp_id):
        log_audit_event(
            source_ip=request.remote_addr, 
            action="CP_BAJA_EXITOSA",
            description="CP eliminado del sistema por solicitud del Registry.",
            cp_id=cp_id
        )
        print(f"[Registry] CP {cp_id} eliminado.")
        enviar_log_central(f"CP {cp_id} dado de baja correctamente.")
        return jsonify({"message": f"CP {cp_id} dado de baja correctamente"}), 200
    else:
        return jsonify({"error": "CP no encontrado o no se pudo eliminar"}), 404

if __name__ == '__main__':
    print(f"--- EV_Registry (API REST SEGURO) iniciado en puerto {REGISTRY_PORT} ---")
    app.run(host='0.0.0.0', port=REGISTRY_PORT, debug=True, ssl_context='adhoc')