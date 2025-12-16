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
    "city_temps": {},           # Seva: Almacén de temperaturas actuales
    "config": {
        "temp_umbral": 0.0      # Configuración modificable
    },
    "sessions": {},             # Referencia a current_sessions del Central
    "producer": None            # Referencia al Kafka Producer del Central
}

# Lista para guardar logs que vienen de otros módulos (Registry, Weather, etc.)
# Formato: {'timestamp': float, 'source': 'REGISTRY', 'msg': 'Texto...'}
EXTERNAL_LOGS = []
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

def configure_api(messages_list, drivers_set, sockets_dict, command_func, sessions, producer, kafka_broker_url=None):
    """
    Configura las referencias compartidas con el hilo principal.
    Versión Unificada: Acepta contexto, sesiones, producer y configuración de simulación.
    """
    global SIMULATION_PRODUCER
    
    # 1. Contexto Básico
    CONTEXT["central_messages"] = messages_list
    CONTEXT["connected_drivers"] = drivers_set
    CONTEXT["active_cp_sockets"] = sockets_dict
    CONTEXT["send_command_func"] = command_func
    
    # 2. Contexto de Seguridad (Release 2)
    CONTEXT["sessions"] = sessions
    CONTEXT["producer"] = producer
    
    # 3. Configuración del Productor para Simulación Web
    # Si Central ya nos pasa un producer conectado, lo reutilizamos (es más eficiente)
    if producer:
        SIMULATION_PRODUCER = producer
        print("[API] Usando Producer compartido de Central para Simulación.")
    
    # Si no hay producer pero hay URL (fallback antiguo), creamos uno nuevo
    elif kafka_broker_url:
        try:
            SIMULATION_PRODUCER = KafkaProducer(
                bootstrap_servers=[kafka_broker_url],
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
            )
            print(f"[API] Canal de Simulación conectado independientemente a {kafka_broker_url}")
        except Exception as e:
            print(f"[API] Error conectando Kafka secundario: {e}")

    print("[API] Contexto configurado correctamente (Sesiones, Kafka y Simulación listos).")

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
    Ahora recupera el consumo parcial antes de cortar para generar un ticket de error válido.
    """
    data = request.json
    cp_id = data.get('cp_id')
    
    if not cp_id:
        return jsonify({"error": "Falta el parámetro cp_id"}), 400

    # 1. RECUPERAR DATOS DE CONSUMO ACTUAL (ANTES DE BORRAR NADA)
    # Buscamos en la BD cuánto lleva cargado ese CP
    kwh_actual = 0.0
    importe_actual = 0.0
    
    try:
        # Obtenemos info completa del CP
        all_cps = database.get_all_cps()
        cp_info = next((cp for cp in all_cps if cp['id'] == cp_id), None)
        
        if cp_info:
            # Aseguramos que sea float
            kwh_actual = float(cp_info.get('kwh') or 0.0)
            importe_actual = float(cp_info.get('importe') or 0.0)
    except Exception as e:
        print(f"[API] Error recuperando métricas parciales: {e}")

    # 2. REVOCAR CLAVES (BD)
    if database.revoke_cp_keys(cp_id):
        
        # Mensaje detallado para el Frontend y Logs
        msg_publico = (f"[SEGURIDAD] Claves de {cp_id} REVOCADAS durante suministro. "
                       f"Cierre forzoso. Parcial: {kwh_actual:.3f} kWh / {importe_actual:.2f} €")
        
        print(f"[API CENTRAL] {msg_publico}")
        
        # 3. GUARDAR LOG PARA EL FRONTEND (Aquí estaba lo que faltaba)
        if CONTEXT["central_messages"] is not None:
            CONTEXT["central_messages"].append(msg_publico)
        
        # 4. AUDITORÍA
        try:
            log_audit_event(
                source_ip=request.remote_addr,
                action="REVOCACION_MANUAL",
                description=f"Revocación forzosa. Suministro cortado con {kwh_actual:.3f} kWh.",
                cp_id=cp_id
            )
        except Exception: pass

        # 5. NOTIFICAR AL DRIVER (Con los datos reales)
        sessions = CONTEXT.get("sessions")
        producer = CONTEXT.get("producer")
        
        if sessions is not None and cp_id in sessions and producer:
            driver_id = sessions[cp_id].get('driver_id')
            if driver_id:
                print(f"[API] Notificando expulsión a driver {driver_id}...")
                error_msg = {
                    "type": "SUPPLY_ERROR",
                    "cp_id": cp_id,
                    "user_id": driver_id,
                    "reason": "⚠️ CARGA DETENIDA: Intervención de Seguridad (Revocación).",
                    # AQUÍ PONEMOS LOS DATOS REALES:
                    "kwh_partial": kwh_actual, 
                    "importe_partial": importe_actual
                }
                try:
                    producer.send('driver_notifications', value=error_msg)
                    producer.flush()
                except Exception as e:
                    print(f"[API] Error notificando a driver: {e}")
                
                try: del sessions[cp_id]
                except: pass

        # 6. PATEAR AL CP (Cerrar Socket y Actualizar Estado)
        database.update_cp_status(cp_id, 'FUERA_DE_SERVICIO')
        
        try:
            active_sockets = CONTEXT.get("active_cp_sockets")
            if active_sockets and cp_id in active_sockets:
                sock = active_sockets[cp_id]
                try: sock.close()
                except: pass
                del active_sockets[cp_id]
        except: pass

        return jsonify({"status": "OK", "message": msg_publico}), 200
    
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

# --- NUEVO ENDPOINT PARA DRIVER WEB ---
@app.route('/api/driver/request', methods=['POST'])
def web_driver_request():
    """Recibe una petición de carga desde la Web y la envía a Kafka."""
    data = request.json
    user_id = data.get('user_id')
    cp_id = data.get('cp_id')

    if not user_id or not cp_id:
        return jsonify({"error": "Faltan datos"}), 400

    if not SIMULATION_PRODUCER:
        return jsonify({"error": "Kafka no conectado en API"}), 500

    # Construimos el mensaje EXACTAMENTE como lo espera la Central
    # Topic: driver_requests
    msg = {
        "user_id": user_id,
        "cp_id": cp_id,
        "type": "REQUEST_CHARGE",
        "timestamp": time.time(),
        "source_ip": "WEB_DASHBOARD"
    }

    try:
        # Reutilizamos el productor que ya teníamos para la simulación
        # pero enviamos al topic de los drivers
        SIMULATION_PRODUCER.send('driver_requests', value=msg)
        SIMULATION_PRODUCER.flush()
        
        # Log visual para confirmar salida
        if CONTEXT["central_messages"] is not None:
            CONTEXT["central_messages"].append(f"[WEB-DRIVER] Petición enviada: {user_id} -> {cp_id}")
            
        return jsonify({"message": "Solicitud enviada a Kafka"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    # CAMBIO IMPORTANTE: Forzamos 0.0.0.0 para que escuche conexiones externas
    print(f"[API Central] 🟢 Escuchando en TODAS las interfaces (0.0.0.0) puerto {port}")
    
    # Ignoramos el 'host' que nos llega y usamos '0.0.0.0'
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)