# Fichero: EV_W.py (Weather Control Office)
import time
import requests
import sys
import json
import os

def cargar_config_red():
    try:
        with open('network_config.json', 'r') as f:
            config = json.load(f)
            return config.get('central_ip', '127.0.0.1'), config.get('central_api_port', 5000)  
    except Exception as e:
        print(f"[EV_W] ⚠️ No se encontró network_config.json, usando localhost. Error: {e}")
        return "127.0.0.1", 5000

CENTRAL_IP, CENTRAL_PORT = cargar_config_red()
BASE_URL = f"http://{CENTRAL_IP}:{CENTRAL_PORT}"

CENTRAL_URL_ALERTAS = f"{BASE_URL}/api/alertas"
CENTRAL_URL_ESTADO = f"{BASE_URL}/api/estado"
CENTRAL_URL_LOG = f"{BASE_URL}/api/log"

print(f"[EV_W] Configurado para conectar a Central en: {BASE_URL}")
CONFIG_FILE = "config_weather.json"

def cargar_configuracion():
    """
    Lee la configuración desde el archivo JSON en caliente.
    Retorna (api_key, diccionario_ciudades).
    """
    try:
        if not os.path.exists(CONFIG_FILE):
            print(f"[EV_W] Error: No existe el fichero {CONFIG_FILE}")
            return None, {}
            
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            return config.get('api_key'), config.get('cities', {})
            
    except json.JSONDecodeError:
        print(f"[EV_W] Error: El fichero {CONFIG_FILE} tiene un formato JSON inválido.")
        return None, {}
    except Exception as e:
        print(f"[EV_W] Error inesperado leyendo config: {e}")
        return None, {}

def obtener_clima(ciudad, api_key):
    """Consulta la API de OpenWeather y reporta errores a la Web."""
    if not api_key: return None
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={ciudad}&appid={api_key}&units=metric"
        response = requests.get(url, timeout=2)
        
        if response.status_code == 200:
            data = response.json()
            return data['main']['temp']
            
        elif response.status_code == 401:
            err = f"Error 401: API Key inválida."
            print(f"[CLIMA] {err}")
            try:
                requests.post(CENTRAL_URL_LOG, json={"source": "EV_W", "msg": err}, timeout=1)
            except: pass
            
        else:
            err = f"Error al obtener clima de {ciudad}: {response.status_code}"
            print(f"[CLIMA] {err}")
            try:
                requests.post(CENTRAL_URL_LOG, json={"source": "EV_W", "msg": err}, timeout=1)
            except: pass
            
    except Exception as e:
        err = f"Error conectando con OpenWeather: {e}"
        print(f"[CLIMA] {err}")
        try:
            requests.post(CENTRAL_URL_LOG, json={"source": "EV_W", "msg": err}, timeout=1)
        except: pass
        
    return None

def obtener_umbral_central():
    """Consulta el umbral de temperatura configurado en la Central."""
    try:
        resp = requests.get(CENTRAL_URL_ESTADO, timeout=1)
        if resp.status_code == 200:
            return float(resp.json().get('config', {}).get('temp_umbral', 0.0))
    except: pass
    return 0.0

def enviar_log_temperatura(ciudad, temp):
    """Envía el dato de temperatura a la Central para visualizarlo en la Web."""
    try:
        mensaje = f"Temperatura en {ciudad}: {temp}ºC"
        requests.post(CENTRAL_URL_LOG, json={"source": "EV_W", "msg": mensaje}, timeout=1)
    except: pass

def notificar_central(ciudad, accion):
    """Envía la orden de PARAR o REANUDAR a la Central según el clima."""
    try:
        payload = {"city": ciudad, "action": accion}
        requests.post(CENTRAL_URL_ALERTAS, json=payload, timeout=1)
        print(f" -> Notificado a Central: {accion} CPs en {ciudad}")
    except: pass

def ciclo_control():
    print(f"--- EV_W (Weather Office) Iniciado ---")
    print(f"Leyendo configuración dinámica de: {CONFIG_FILE}")
    
    estado_alertas = {} 

    while True:
        API_KEY, CIUDADES_CPS = cargar_configuracion()
        
        if not API_KEY or not CIUDADES_CPS:
            print("[EV_W] Esperando configuración válida en json...")
            time.sleep(2)
            continue

        TEMP_UMBRAL = obtener_umbral_central()
        
        print(f"\n[Ciclo] Consultando {len(CIUDADES_CPS)} ciudades (Umbral: {TEMP_UMBRAL}ºC)...")
        
        for ciudad in CIUDADES_CPS:
            if ciudad not in estado_alertas:
                estado_alertas[ciudad] = False

            temp = obtener_clima(ciudad, API_KEY)
            
            if temp is not None:
                print(f"  > {ciudad}: {temp}ºC")
                enviar_log_temperatura(ciudad, temp)

                if temp < TEMP_UMBRAL:
                    if not estado_alertas[ciudad]:
                        print(f"    !!! ALERTA DE FRÍO !!! ({temp} < {TEMP_UMBRAL})")
                        notificar_central(ciudad, "PARAR")
                        estado_alertas[ciudad] = True
                else:
                    if estado_alertas[ciudad]:
                        print(f"    ... Temperatura normalizada ...")
                        notificar_central(ciudad, "REANUDAR")
                        estado_alertas[ciudad] = False
        
        time.sleep(5)

if __name__ == "__main__":
    try:
        ciclo_control()
    except KeyboardInterrupt:
        print("\nEV_W Cerrado.")