# Fichero: EV_Central_API.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import time
import logging
import database
from database import log_audit_event # Seva: Importar función de auditoría
from kafka import KafkaProducer  
import json

SIMULATION_PRODUCER = None

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
    combined_logs = []
    
    # 1. Logs internos de Central (Ahora tienen timestamp real)
    if CONTEXT["central_messages"]:
        for entry in list(CONTEXT["central_messages"]):
            # Si usamos TimestampedList, entry es un dict {'msg':..., 'timestamp':...}
            if isinstance(entry, dict):
                combined_logs.append({
                    'source': 'CENTRAL',
                    'msg': entry['msg'],
                    'timestamp': entry['timestamp'] # ¡Hora Real!
                })
            else:
                # Fallback por si acaso
                combined_logs.append({'source': 'CENTRAL','msg': str(entry),'timestamp': time.time()})
                
    # 2. Logs Externos (Igual que antes)
    combined_logs.extend(EXTERNAL_LOGS)
    
    combined_logs.sort(key=lambda x: x['timestamp'])
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
    
    EXTERNAL_LOGS.append({
        'timestamp': time.time(),
        'source': 'EV_W',
        'msg': msg
    })

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
        msg = f"Claves del CP {cp_id} REVOCADAS manualmente."
        print(f"[API] {msg}")
        
        # 2. Guardar log interno para mostrar en consola de Central
        if CONTEXT["central_messages"] is not None:
            CONTEXT["central_messages"].append(f"{msg}")
        
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

#Seva: --- ENDPOINTS DE COMANDOS A CPs (PARAR/REANUDAR) ---
@app.route('/api/comandos/cp', methods=['POST'])
def send_cp_action():
    """Envía PARAR o REANUDAR a un CP específico."""
    data = request.json
    cp_id = data.get('cp_id')
    action = data.get('action') # Espera 'PARAR' o 'REANUDAR'

    if not cp_id or not action:
        return jsonify({"error": "Faltan parámetros"}), 400

    send_cmd = CONTEXT["send_command_func"]
    
    # Verificamos si el CP está conectado (tiene socket activo)
    if cp_id not in CONTEXT["active_cp_sockets"]:
        return jsonify({"error": "El CP no está conectado. No se puede enviar comando."}), 404

    if send_cmd:
        # Auditoría: Registramos que la orden vino desde la Web
        log_audit_event(
            source_ip=request.remote_addr,
            action=f"WEB_ORDEN_{action}",
            description=f"Orden manual desde Web: {action} para {cp_id}",
            cp_id=cp_id
        )
        
        # Ejecutar el comando usando la lógica de la Central
        send_cmd(cp_id, action, CONTEXT["central_messages"])
        return jsonify({"message": f"Comando {action} enviado a {cp_id}"}), 200

    return jsonify({"error": "Función de comandos no disponible"}), 500

# Seva: --- ENDPOINTS DE COMANDOS MASIVOS
@app.route('/api/comandos/todos', methods=['POST'])
def send_global_action():
    """Envía PARAR o REANUDAR a TODOS los CPs conectados."""
    data = request.json
    action = data.get('action') # 'PARAR' o 'REANUDAR'

    if not action:
        return jsonify({"error": "Falta acción"}), 400

    send_cmd = CONTEXT["send_command_func"]
    active_sockets = CONTEXT["active_cp_sockets"]

    if send_cmd and active_sockets:
        count = 0
        # Iteramos sobre una copia de las claves para evitar errores de concurrencia
        for cp_id in list(active_sockets.keys()):
            # Auditoría individual para trazabilidad completa
            log_audit_event(
                source_ip=request.remote_addr,
                action=f"WEB_ORDEN_MASIVA_{action}",
                description=f"Orden masiva desde Web: {action}",
                cp_id=cp_id
            )
            send_cmd(cp_id, action, CONTEXT["central_messages"])
            count += 1
        return jsonify({"message": f"Comando {action} enviado a {count} CPs conectados"}), 200

    return jsonify({"message": "No hay CPs conectados para recibir la orden"}), 200

def configure_api(messages_list, drivers_set, sockets_dict, command_func, kafka_broker_url=None): # <--- Nuevo argumento
    global SIMULATION_PRODUCER
    CONTEXT["central_messages"] = messages_list
    CONTEXT["connected_drivers"] = drivers_set
    CONTEXT["active_cp_sockets"] = sockets_dict
    CONTEXT["send_command_func"] = command_func
    
    # Inicializar productor de simulación
    if kafka_broker_url:
        try:
            SIMULATION_PRODUCER = KafkaProducer(
                bootstrap_servers=[kafka_broker_url],
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
            )
            print(f"[API] Canal de Simulación conectado a {kafka_broker_url}")
        except Exception as e:
            print(f"[API] Error conectando Kafka: {e}")

# En EV_Central_API.py (añádelo antes de start_api_server)

@app.route('/api/simulacion', methods=['POST'])
def enviar_simulacion():
    """Envía comandos F, R, I, E al Engine vía Kafka."""
    data = request.json
    cp_id = data.get('cp_id')
    command = data.get('command') # F, R, I, E

    if not SIMULATION_PRODUCER:
        return jsonify({"error": "Kafka no disponible"}), 500

    # Enviamos la orden al topic 'cp_simulation'
    msg = {"target_cp": cp_id, "command": command}
    SIMULATION_PRODUCER.send('cp_simulation', value=msg)
    SIMULATION_PRODUCER.flush()
    
    # Log visual
    if CONTEXT["central_messages"] is not None:
        CONTEXT["central_messages"].append(f"[SIMULACION] Enviado comando {command} a {cp_id}")

    return jsonify({"message": f"Comando {command} enviado"}), 200

def start_api_server(host, port):
    print(f"[API Central] Escuchando peticiones HTTP en {host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)