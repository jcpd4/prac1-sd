# Fichero: EV_W.py (Weather Control Office)
import time
import requests
import sys
import json
import os
# --- CONFIGURACIÓN ---
API_KEY = "3b4ca6faaaed312c8ae9244e7a652a69" 

# URLs de tu Central
CENTRAL_URL_ALERTAS = "http://127.0.0.1:5000/api/alertas"
CENTRAL_URL_ESTADO = "http://127.0.0.1:5000/api/estado"
CENTRAL_URL_LOG = "http://127.0.0.1:5000/api/log" # NUEVO: Para enviar la temperatura

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
            # Extraemos la API KEY y el mapa de ciudades
            return config.get('api_key'), config.get('cities', {})
            
    except json.JSONDecodeError:
        print(f"[EV_W] Error: El fichero {CONFIG_FILE} tiene un formato JSON inválido.")
        return None, {}
    except Exception as e:
        print(f"[EV_W] Error inesperado leyendo config: {e}")
        return None, {}

def obtener_clima(ciudad, api_key):
    """Consulta la API de OpenWeather."""
    if not api_key: return None
    try:
        # Usamos la API Key leída del JSON
        url = f"http://api.openweathermap.org/data/2.5/weather?q={ciudad}&appid={api_key}&units=metric"
        response = requests.get(url, timeout=2)
        
        if response.status_code == 200:
            data = response.json()
            return data['main']['temp']
        elif response.status_code == 401:
            print(f"[CLIMA] Error 401: API Key inválida.")
        else:
            print(f"[CLIMA] Error al obtener clima de {ciudad}: {response.status_code}")
    except Exception as e:
        print(f"[CLIMA] Excepción conectando con OpenWeather: {e}")
    return None


def obtener_umbral_central():
    """Consulta el umbral de temperatura configurado en la Central."""
    try:
        resp = requests.get(CENTRAL_URL_ESTADO, timeout=1)
        if resp.status_code == 200:
            # Navegamos el JSON: { config: { temp_umbral: X } }
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
    
    # Diccionario para recordar si ya enviamos alerta y no spamear a la Central
    estado_alertas = {} 

    while True:
        # 1. LEER CONFIGURACIÓN EN CADA CICLO (Parametrización en caliente)
        API_KEY, CIUDADES_CPS = cargar_configuracion()
        
        if not API_KEY or not CIUDADES_CPS:
            print("[EV_W] Esperando configuración válida en json...")
            time.sleep(2)
            continue

        # 2. Obtener el Umbral actual de la Central
        TEMP_UMBRAL = obtener_umbral_central()
        
        # 3. Iterar sobre las ciudades del JSON
        print(f"\n[Ciclo] Consultando {len(CIUDADES_CPS)} ciudades (Umbral: {TEMP_UMBRAL}ºC)...")
        
        for ciudad in CIUDADES_CPS:
            # Si añadimos una ciudad nueva al JSON, la inicializamos en el estado
            if ciudad not in estado_alertas:
                estado_alertas[ciudad] = False

            # Consultar OpenWeather
            temp = obtener_clima(ciudad, API_KEY)
            
            if temp is not None:
                print(f"  > {ciudad}: {temp}ºC")
                enviar_log_temperatura(ciudad, temp)

                # Lógica de Alerta (Histeresis simple)
                if temp < TEMP_UMBRAL:
                    # Hace frío -> Mandar PARAR
                    if not estado_alertas[ciudad]:
                        print(f"    !!! ALERTA DE FRÍO !!! ({temp} < {TEMP_UMBRAL})")
                        notificar_central(ciudad, "PARAR")
                        estado_alertas[ciudad] = True
                else:
                    # Hace buen tiempo -> Mandar REANUDAR
                    if estado_alertas[ciudad]:
                        print(f"    ... Temperatura normalizada ...")
                        notificar_central(ciudad, "REANUDAR")
                        estado_alertas[ciudad] = False
        
        # Esperar 5 segundos antes del siguiente ciclo
        time.sleep(5)

if __name__ == "__main__":
    try:
        ciclo_control()
    except KeyboardInterrupt:
        print("\nEV_W Cerrado.")