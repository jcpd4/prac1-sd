# Fichero: EV_Registry.py
# Módulo de Registro (Release 2) - API REST
from flask import Flask, request, jsonify
import database
import uuid # Para generar tokens únicos
import sys

# Configuración
# El Registry usará un puerto diferente al de la Central (8000) para no chocar.
# Usaremos el 6000.
REGISTRY_PORT = 6000 

app = Flask(__name__)

# Inicializamos la DB al arrancar para asegurar que existen las tablas
database.setup_database()

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
        # En un sistema real esto sería un certificado o una key compleja.
        # Para la práctica, un UUID v4 es suficiente y seguro.
        token = str(uuid.uuid4())
        
        # 3. Guardar en Base de Datos (Compartida con Central)
        # Usamos las funciones de database.py
        database.register_cp(cp_id, location) # Crea el registro
        database.update_cp_token(cp_id, token) # Guarda el token
        
        print(f"[Registry] CP {cp_id} registrado con éxito. Token generado.")
        
        # 4. Devolver el token al CP
        return jsonify({
            "message": "CP registrado correctamente",
            "token": token
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
        print(f"[Registry] CP {cp_id} eliminado.")
        return jsonify({"message": f"CP {cp_id} dado de baja correctamente"}), 200
    else:
        return jsonify({"error": "CP no encontrado o no se pudo eliminar"}), 404

# --- Arranque del Servidor ---
if __name__ == '__main__':
    print(f"--- EV_Registry (API REST SEGURO) iniciado en puerto {REGISTRY_PORT} ---")
    # AÑADIMOS: ssl_context='adhoc'
    # Esto habilita HTTPS usando un certificado generado en memoria
    app.run(host='0.0.0.0', port=REGISTRY_PORT, debug=True, ssl_context='adhoc')