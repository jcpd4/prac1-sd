# Fichero: EV_Central_API.py
from flask import Flask, request, jsonify
import threading
import time
import logging
import database  # Importamos database directamente porque es un módulo independiente

app = Flask(__name__)

# --- VARIABLES COMPARTIDAS (REFERENCIAS) ---
# Aquí guardaremos los "punteros" a las variables de EV_Central.py
CONTEXT = {
    "central_messages": None,   # Lista de logs
    "connected_drivers": None,  # Set de drivers
    "active_cp_sockets": None,  # Dict de sockets (para saber quién está conectado)
    "send_command_func": None   # Función para enviar órdenes (send_cp_command)
}

# Configuración de logs (para que Flask no ensucie la consola)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

def configure_api(messages_list, drivers_set, sockets_dict, command_func):
    """
    Esta función la llama EV_Central al arrancar para 'inyectar' sus variables.
    """
    CONTEXT["central_messages"] = messages_list
    CONTEXT["connected_drivers"] = drivers_set
    CONTEXT["active_cp_sockets"] = sockets_dict
    CONTEXT["send_command_func"] = command_func
    print("[API] Contexto configurado correctamente.")

# --- ENDPOINTS ---

@app.route('/api/estado', methods=['GET'])
def get_system_status():
    """Endpoint para el FRONTEND: Devuelve estado global."""
    try:
        # 1. Datos de BBDD
        all_cps = database.get_all_cps()
        
        # 2. Datos en memoria (Drivers) - Usamos la referencia inyectada
        drivers_list = []
        if CONTEXT["connected_drivers"]:
            # Convertimos el set a lista para JSON
            drivers_list = list(CONTEXT["connected_drivers"])
            
        return jsonify({
            "cps": all_cps,
            "drivers_connected": drivers_list,
            "timestamp": time.time()
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/alertas', methods=['POST'])
def receive_weather_alert():
    """Endpoint para EV_W (Clima): Recibe alertas y para CPs."""
    data = request.json
    city = data.get('city')
    action = data.get('action') # 'PARAR' o 'REANUDAR'
    
    if not city or not action:
        return jsonify({"error": "Faltan datos"}), 400

    # Log en la lista de mensajes de Central
    msg = f"[CLIMA] Alerta en {city} -> {action} CPs"
    print(f"[API] {msg}")
    if CONTEXT["central_messages"] is not None:
        CONTEXT["central_messages"].append(msg)
    
    # Lógica de negocio
    count = 0
    all_cps = database.get_all_cps()
    
    # Función para enviar comandos (la que nos pasó Central)
    send_cmd = CONTEXT["send_command_func"]
    
    if send_cmd:
        for cp in all_cps:
            # Búsqueda simple de texto (ej: "Madrid" en "C/ Serrano, Madrid")
            if city.lower() in cp['location'].lower():
                # Verificar si el CP está conectado por socket antes de intentar enviar
                cp_id = cp['id']
                if cp_id in CONTEXT["active_cp_sockets"]:
                    send_cmd(cp_id, action, CONTEXT["central_messages"])
                    count += 1
    
    return jsonify({"message": f"Accion {action} aplicada a {count} CPs en {city}"}), 200

def start_api_server(host, port):
    """Arranca el servidor Flask."""
    print(f"[API Central] Escuchando peticiones HTTP en {host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)