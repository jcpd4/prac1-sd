# Fichero: EV_W.py (Weather Control Office)
import time
import requests
import sys

# --- CONFIGURACIÓN ---
API_KEY = "3b4ca6faaaed312c8ae9244e7a652a69" 

# URLs de tu Central
CENTRAL_URL_ALERTAS = "http://127.0.0.1:5000/api/alertas"
CENTRAL_URL_ESTADO = "http://127.0.0.1:5000/api/estado"
CENTRAL_URL_LOG = "http://127.0.0.1:5000/api/log" # NUEVO: Para enviar la temperatura

CIUDADES_CPS = {
    "Madrid": "MAD",
    "Barcelona": "BCN",
    "Valencia": "VAL",
    "Oslo": "TEST"
}

def obtener_clima(ciudad):
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={ciudad}&appid={API_KEY}&units=metric"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            return data['main']['temp']
    except Exception:
        pass
    return None

def obtener_umbral_central():
    try:
        resp = requests.get(CENTRAL_URL_ESTADO)
        if resp.status_code == 200:
            return float(resp.json().get('config', {}).get('temp_umbral', 0.0))
    except: pass
    return 0.0

def enviar_log_temperatura(ciudad, temp):
    """Envía la temperatura a la Central para que salga en la web."""
    try:
        # El formato "Temperatura en X: Y" es clave para que EV_Central_API lo entienda
        mensaje = f"Temperatura en {ciudad}: {temp}"
        requests.post(CENTRAL_URL_LOG, json={"source": "EV_W", "msg": mensaje}, timeout=1)
    except: pass

def notificar_central(ciudad, accion):
    try:
        payload = {"city": ciudad, "action": accion}
        requests.post(CENTRAL_URL_ALERTAS, json=payload)
        print(f" -> Notificado a Central: {accion} CPs en {ciudad}")
    except: pass

def ciclo_control():
    print(f"--- EV_W (Weather Office) Iniciado ---")
    estado_alertas = {ciudad: False for ciudad in CIUDADES_CPS}

    while True:
        TEMP_UMBRAL = obtener_umbral_central()
        
        for ciudad in CIUDADES_CPS:
            temp = obtener_clima(ciudad)
            
            if temp is not None:
                print(f"[CLIMA] {ciudad}: {temp}ºC")
                
                # NUEVO: Enviar el dato a la web
                enviar_log_temperatura(ciudad, temp)

                # Lógica de Alerta
                if temp < TEMP_UMBRAL:
                    if not estado_alertas[ciudad]:
                        print(f"¡ALERTA! ({temp}ºC < {TEMP_UMBRAL}ºC)")
                        notificar_central(ciudad, "PARAR")
                        estado_alertas[ciudad] = True
                else:
                    if estado_alertas[ciudad]:
                        print(f"Normalizado. ({temp}ºC)")
                        notificar_central(ciudad, "REANUDAR")
                        estado_alertas[ciudad] = False
        
        time.sleep(4)

if __name__ == "__main__":
    try:
        ciclo_control()
    except KeyboardInterrupt:
        print("\nCerrando.")