# Fichero: EV_Central_API.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import time
import logging
import database
from database import log_audit_event # Seva: Importar función de auditoría
app = Flask(__name__)
CORS(app)

# --- VARIABLES COMPARTIDAS ---
CONTEXT = {
    "central_messages": [],     # Logs internos de Central (strings)
    "connected_drivers": set(),
    "active_cp_sockets": {},
    "send_command_func": None,
    "config": {
        "temp_umbral": 0.0      # Configuración modificable
    }
}

# Lista para guardar logs que vienen de otros módulos (Registry, Weather, etc.)
# Formato: {'timestamp': float, 'source': 'REGISTRY', 'msg': 'Texto...'}
EXTERNAL_LOGS = []

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

def configure_api(messages_list, drivers_set, sockets_dict, command_func):
    CONTEXT["central_messages"] = messages_list
    CONTEXT["connected_drivers"] = drivers_set
    CONTEXT["active_cp_sockets"] = sockets_dict
    CONTEXT["send_command_func"] = command_func
    print("[API] Contexto configurado correctamente.")

# --- ENDPOINTS DE ESTADO Y CONFIGURACIÓN ---

@app.route('/api/estado', methods=['GET'])
def get_system_status():
    try:
        all_cps = database.get_all_cps()
        drivers_list = list(CONTEXT["connected_drivers"]) if CONTEXT["connected_drivers"] else []
        
        return jsonify({
            "cps": all_cps,
            "drivers_connected": drivers_list,
            "timestamp": time.time(),
            "config": CONTEXT["config"] 
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/config/umbral', methods=['POST'])
def set_temp_umbral():
    """Cambia el umbral de temperatura desde la Web."""
    data = request.json
    nuevo_umbral = data.get('umbral')
    if nuevo_umbral is not None:
        try:
            nuevo_umbral_float = float(nuevo_umbral)
        except ValueError:
            return jsonify({"error": "El umbral debe ser un número válido"}), 400
        
        CONTEXT["config"]["temp_umbral"] = float(nuevo_umbral)
        msg = f"Umbral de temperatura cambiado a {nuevo_umbral}ºC desde Web"
        print(f"[CONFIG] {msg}")

        # Lo guardamos como log interno
        if CONTEXT["central_messages"] is not None:
            CONTEXT["central_messages"].append(f"[CONFIG] {msg}")

        # Seva: AUDITORÍA: CAMBIO DE CONFIGURACIÓN ***
        # Usamos request.remote_addr para capturar la IP que hizo la llamada API
        log_audit_event(
            source_ip=request.remote_addr,
            action="API_CAMBIO_UMBRAL",
            description=f"Umbral de temperatura climática modificado a {nuevo_umbral_float}ºC.",
            cp_id=None
        )
        # *****************************************
        return jsonify({"status": "OK", "nuevo_umbral": nuevo_umbral}), 200
    return jsonify({"error": "Falta parámetro umbral"}), 400

# --- ENDPOINTS DE LOGGING Y ALERTAS ---

@app.route('/api/log', methods=['POST'])
def receive_external_log():
    """Permite a Registry, Weather y Driver enviar sus logs aquí."""
    data = request.json
    source = data.get('source', 'UNKNOWN')
    msg = data.get('msg', '')
    
    # Guardamos el log externo
    log_entry = {
        'timestamp': time.time(),
        'source': source,
        'msg': msg
    }
    EXTERNAL_LOGS.append(log_entry)
    
    # Mantenemos solo los últimos 200 logs externos
    if len(EXTERNAL_LOGS) > 200:
        EXTERNAL_LOGS.pop(0)
        
    print(f"[{source}] {msg}") # Imprimir en consola de Central también
    return jsonify({"status": "OK"}), 200

@app.route('/api/logs', methods=['GET'])
def get_logs():
    """Devuelve TODOS los logs mezclados y ordenados para la web."""
    combined_logs = []
    
    # 1. Añadir logs internos de Central
    if CONTEXT["central_messages"]:
        # Los logs de central son strings, les inventamos un timestamp aproximado (el actual)
        # o simplemente los ponemos al final. Para hacerlo simple:
        for m in list(CONTEXT["central_messages"]):
            combined_logs.append({
                'source': 'CENTRAL',
                'msg': m,
                'timestamp': time.time() # Aproximado
            })
            
    # 2. Añadir logs externos (Registry, Weather...)
    combined_logs.extend(EXTERNAL_LOGS)
    
    # 3. Devolver (la web se encargará de filtrar)
    # Devolvemos los últimos 100 para no saturar
    return jsonify({"logs": combined_logs[-100:]}), 200

@app.route('/api/alertas', methods=['POST'])
def receive_weather_alert():
    data = request.json
    city = data.get('city')
    action = data.get('action')
    
    if not city or not action:
        return jsonify({"error": "Faltan datos"}), 400

    msg = f"[CLIMA] Alerta en {city} -> {action} CPs"
    print(f"[API] {msg}")
    if CONTEXT["central_messages"] is not None:
        CONTEXT["central_messages"].append(msg)
    
    # Lógica de parada/arranque
    all_cps = database.get_all_cps()
    send_cmd = CONTEXT["send_command_func"]
    count = 0
    if send_cmd:
        for cp in all_cps:
            if city.lower() in cp['location'].lower():
                cp_id = cp['id']
                if cp_id in CONTEXT["active_cp_sockets"]:
                    # Seva: AUDITORÍA: ORDEN CLIMÁTICA ***
                    log_audit_event(
                        source_ip="EV_W_SERVICE", # El origen de la orden es el servicio climático (EV_W)
                        action=f"CLIMA_ORDEN_{action.upper()}",
                        description=f"Orden forzada de {action.upper()} por alerta climática en {city}.",
                        cp_id=cp_id
                    )
                    # *****************************************
                    send_cmd(cp_id, action, CONTEXT["central_messages"])
                    count += 1
    
    return jsonify({"message": f"Accion {action} aplicada"}), 200

def start_api_server(host, port):
    print(f"[API Central] Escuchando peticiones HTTP en {host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)