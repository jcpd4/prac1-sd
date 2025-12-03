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
    "city_temps": {}, # Seva: Almacén de temperaturas actuales
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
            "config": CONTEXT["config"], 
            "city_temps": CONTEXT["city_temps"] # Seva: Enviamos temps al Front para las etiquetas
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/config/umbral', methods=['POST'])
def set_temp_umbral():
    data = request.json
    nuevo_umbral = data.get('umbral')
    if nuevo_umbral is not None:
        try:
            nuevo_umbral_float = float(nuevo_umbral)
            CONTEXT["config"]["temp_umbral"] = nuevo_umbral_float
            
            # Auditoría solo para cambios de configuración 
            msg = f"Umbral de temperatura cambiado a {nuevo_umbral}ºC desde Web"
            if CONTEXT["central_messages"] is not None:
                CONTEXT["central_messages"].append(f"[CONFIG] {msg}")
            
            log_audit_event(
                source_ip=request.remote_addr,
                action="API_CAMBIO_UMBRAL",
                description=f"Umbral modificado a {nuevo_umbral_float}ºC.",
                cp_id=None
            )
            return jsonify({"status": "OK"}), 200
        except ValueError:
            pass
    return jsonify({"error": "Error en datos"}), 400

# --- ENDPOINTS DE LOGGING Y ALERTAS ---

@app.route('/api/log', methods=['POST'])
def receive_external_log():
    """Recibe logs. Si es temperatura, solo actualiza estado. Si es alerta, guarda log."""
    data = request.json
    source = data.get('source', 'UNKNOWN')
    msg = data.get('msg', '')
    
    if source == 'EV_W' and "Temperatura en" in msg:
        try:
            # Formato esperado: "Temperatura en Madrid: 15.5ºC"
            parts = msg.split(':')
            if len(parts) >= 2:
                city_part = parts[0].replace("Temperatura en ", "").strip() # "Madrid"
                temp_part = parts[1].replace("ºC", "").strip() # "15.5"
                # Guardamos en memoria para el endpoint /api/estado
                CONTEXT["city_temps"][city_part] = temp_part
        except:
            pass
        # IMPORTANTE: Return aquí para NO añadir a EXTERNAL_LOGS
        return jsonify({"status": "Updated State"}), 200
    
    # Si NO es temperatura (es una alerta, error, conexión, etc.), lo guardamos
    log_entry = {
        'timestamp': time.time(),
        'source': source,
        'msg': msg
    }
    EXTERNAL_LOGS.append(log_entry)
    
    # Limpieza de buffer
    if len(EXTERNAL_LOGS) > 200:
        EXTERNAL_LOGS.pop(0)
        
    print(f"[{source}] {msg}") 
    return jsonify({"status": "Logged"}), 200

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


# Seva: --- ENDPOINTS DE SEGURIDAD (Revocar la llave)---
@app.route('/api/admin/revoke-key', methods=['POST'])
def revoke_cp_key():
    """
    Simula una brecha de seguridad revocando las claves de un CP.
    Requisito de Seguridad: Capacidad de revocación y auditoría.
    """
    data = request.json
    cp_id = data.get('cp_id')
    
    if not cp_id:
        return jsonify({"error": "Falta el parámetro cp_id"}), 400

    # 1. Llamar a la base de datos para borrar las claves
    if database.revoke_cp_keys(cp_id):
        msg = f"SEGURIDAD: Claves del CP {cp_id} REVOCADAS manualmente."
        print(f"[API] {msg}")
        
        # 2. Guardar log interno para mostrar en consola de Central
        if CONTEXT["central_messages"] is not None:
            CONTEXT["central_messages"].append(f"[SEGURIDAD] {msg}")
        
        # 3. Generar evento de AUDITORÍA
        # Usamos request.remote_addr para registrar quién ordenó la revocación
        log_audit_event(
            source_ip=request.remote_addr,
            action="REVOCACION_MANUAL",
            description=f"Claves eliminadas por administrador. CP marcado como FUERA_DE_SERVICIO.",
            cp_id=cp_id
        )
        
        # 4. Intentar forzar cierre de socket si está conectado
        # Esto hace que el CP se de cuenta inmediatamente de que algo pasa
        try:
            if cp_id in CONTEXT["active_cp_sockets"]:
                sock = CONTEXT["active_cp_sockets"][cp_id]
                try:
                    sock.close() # Forzar desconexión
                except: pass
                del CONTEXT["active_cp_sockets"][cp_id]
                print(f"[API] Socket del CP {cp_id} cerrado forzosamente tras revocación.")
        except Exception as e:
            print(f"[API] Error cerrando socket: {e}")

        return jsonify({"status": "OK", "message": msg}), 200
    
    return jsonify({"error": "CP no encontrado o error en BD"}), 404


def start_api_server(host, port):
    print(f"[API Central] Escuchando peticiones HTTP en {host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)