# Fichero: EV_W.py (Weather Control Office)
import time
import requests
import sys

# --- CONFIGURACIÓN ---
# Pega aquí tu API KEY de OpenWeatherMap
API_KEY = "3b4ca6faaaed312c8ae9244e7a652a69" 

# URL de tu Central (API REST)
CENTRAL_URL = "http://127.0.0.1:5000/api/alertas"

# Lista de ciudades donde tienes CPs. 
# Clave: Ciudad real en OpenWeather, Valor: ID del CP asociado (o parte del nombre para buscar)
# Nota: Para probar alertas, puedes poner una ciudad donde sepas que hace mucho frío (ej: "Helsinki")
CIUDADES_CPS = {
    "Madrid": "MAD",      # Afectará a MAD-01, MAD-02...
    "Barcelona": "BCN",   # Afectará a BCN-xx
    "Valencia": "VAL",
    "Oslo": "TEST"        # Oslo suele estar frío, útil para probar alertas
}

# Um b ral de temperatura (Release 2 dice 0ºC, pero puedes subirlo a 20ºC para probar que funciona ya)
TEMP_UMBRAL = 0.0 

def obtener_clima(ciudad):
    """Consulta la API de OpenWeather."""
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={ciudad}&appid={API_KEY}&units=metric"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            temp = data['main']['temp']
            print(f"[CLIMA] {ciudad}: {temp}ºC")
            return temp
        else:
            print(f"[ERROR] OpenWeather {ciudad}: {response.status_code}")
            return None
    except Exception as e:
        print(f"[ERROR] Conexión clima: {e}")
        return None

def notificar_central(ciudad, accion):
    """Envía la orden a la Central."""
    payload = {
        "city": ciudad,
        "action": accion # 'PARAR' o 'REANUDAR'
    }
    try:
        resp = requests.post(CENTRAL_URL, json=payload)
        if resp.status_code == 200:
            print(f" -> Notificado a Central: {accion} CPs en {ciudad}")
        else:
            print(f" -> Error notificando a Central: {resp.text}")
    except Exception as e:
        print(f" -> Central no responde: {e}")

def ciclo_control():
    print(f"--- EV_W (Weather Office) Iniciado ---")
    print(f"Monitorizando ciudades: {list(CIUDADES_CPS.keys())}")
    
    # Estado anterior para no spamear a la central (True=Frio/Alerta, False=Normal)
    estado_alertas = {ciudad: False for ciudad in CIUDADES_CPS}

    while True:
        for ciudad in CIUDADES_CPS:
            temp = obtener_clima(ciudad)
            
            if temp is not None:
                # CASO 1: Hace frío (Alerta)
                if temp < TEMP_UMBRAL:
                    if not estado_alertas[ciudad]: # Solo si es nuevo cambio
                        print(f"¡ALERTA DE HIELO en {ciudad}! ({temp}ºC < {TEMP_UMBRAL}ºC)")
                        notificar_central(ciudad, "PARAR")
                        estado_alertas[ciudad] = True
                
                # CASO 2: Temperatura normal (Recuperación)
                else:
                    if estado_alertas[ciudad]: # Solo si antes estaba en alerta
                        print(f"Clima normalizado en {ciudad}. ({temp}ºC)")
                        notificar_central(ciudad, "REANUDAR")
                        estado_alertas[ciudad] = False
        
        # Esperar 4 segundos como pide la guía (Release 2, punto 5)
        time.sleep(4)

if __name__ == "__main__":
    if API_KEY == "TU_API_KEY_AQUI":
        print("ERROR: Debes poner tu API Key de OpenWeather en el código.")
    else:
        try:
            ciclo_control()
        except KeyboardInterrupt:
            print("\nCerrando Weather Office.")