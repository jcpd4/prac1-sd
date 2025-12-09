## Guía de Ejecución Rápida

Sigue estos pasos para poner en marcha la práctica.

### Paso A: Clonar el Repositorio

#### Paso 1: descargar kafka_examen
```bash
winget install --id Git.Git -e --source winget
```

#### Paso 2: Ir donde quieres
```bash
cd Users
cd alumno
cd Desktop
cd prac1
```

#### Paso 3: Clonar el Repositorio
```bash
git clone https://github.com/jcpd4/prac1-sd.git
```
#### Paso 4: Borrar y crear
Borra y crea un *ev_chargin.db* nuevo (IMPORTANTE)


### Paso B: kafka

#### Paso 1: descargar kafka_examen
descargar https://drive.google.com/file/d/1bp6_fHtfKEeaWFYsQZ6hb5u38yiN9qhE/view?usp=sharing

#### Paso 2: descomprimirlo en C:/
```bash
tar -xzvf kafka_examen.tar.gz
```

#### Paso 3: cambiar las ip's en server.properties
ir a kafka>config>server.properties

```bash
listeners=PLAINTEXT://192.168.18.13:9092,CONTROLLER://localhost:9093
advertised.listeners=PLAINTEXT://192.168.18.13:9092,CONTROLLER://localhost:9093
```

#### Paso 4: Como administrador en powershell
```bash
[guid]::NewGuid().ToString()
```

#### Paso 5: Ejecuta esto en kafka
```bash
.\bin\windows\kafka-storage.bat format -t TU_UUID_AQUI -c .\config\server.properties
```

#### Paso 6: Clonar el Repositorio
```bash
.\bin\windows\kafka-server-start.bat .\config\server.properties
```


#### Paso 7: En caso de que no vaya kafka
ves al directorio, en config busca meta.properties y BORRALO
Vuelve al paso 4.


### Paso C: Ejecutar en PC's del aula

#### Instala kafka-python
```bash
pip install kafka-python
```

#### Paso 1: central (PC1)
```bash
python EV_Central.py 8000 172.21.242.226:9092
```

#### Paso 2: Engine y monitor (PC2)
```bash
python EV_CP_E.py 8001 172.21.242.226:9092 MAD-01
python EV_CP_M.py 172.21.242.226 8000 172.21.242.x 8001 MAD-01
```

#### Paso 3: Driver (PC3)
```bash
python EV_Driver.py 192.168.18.13:9092 101
```
## 🚀 Secuencia de Arranque juanky
### 1️⃣ Terminal 1: Kafka (La Carretera)
Arranca el servidor de mensajes.

```Bash
cd C:\kafka
.\bin\windows\kafka-server-start.bat .\config\server.properties
```
Señal de vida: No debe cerrarse. Debe decir algo como "Kafka Server started".

#### **recuerda que si no funciona tienes que borrar los logs, y volver a generar un nueva uuid en kafka**

### 2️⃣ Terminal 2: EV_Registry (El Portero - Seguridad)
Arranca el servidor de registro seguro (HTTPS).

```Bash
py EV_Registry.py
```
Señal de vida: Running on https://127.0.0.1:6000 (Fíjate en la S de https).

### 3️⃣ Terminal 3: EV_Central (El Cerebro + API)
Arranca la lógica central y el servidor web para el Frontend.

```Bash
py EV_Central.py 8000 127.0.0.1:9092
```
Señal de vida:

EV_Central escuchando sockets en 0.0.0.0:8000

[API Central] Escuchando peticiones HTTP en 0.0.0.0:5000

### 4️⃣ Terminal 4: EV_CP_E (Engine - Hardware)
Simula el poste físico. Importante: Arrancarlo antes que el Monitor.

```Bash
py EV_CP_E.py 8001 127.0.0.1:9092 MAD-01
```
Señal de vida: Engine MAD-01 INICIADO. Esperando Monitor...

### 5️⃣ Terminal 5: EV_CP_M (Monitor - Software)
El cliente inteligente. Se conectará al Registry, bajará claves, hablará con el Engine y conectará con Central.

```Bash
py EV_CP_M.py 127.0.0.1 8000 127.0.0.1 8001 MAD-01
```
Señal de vida:

[Monitor] ¡REGISTRO EXITOSO! Token recibido...

[Monitor] 🟢 CONEXIÓN ESTABLECIDA con Central.

En la Central (Terminal 3), el log dirá: CP 'MAD-01' registrado... Estado: ACTIVADO.

### 6️⃣ Terminal 6: EV_W (Weather - El Clima)
El control automático de temperatura.

```Bash
py EV_W.py
```
Señal de vida: Empezará a imprimir la temperatura de Madrid, Barcelona, etc. cada 4 segundos.

### 7️⃣ Terminal 7: Frontend (La Web)
No es una terminal negra, es tu navegador.

Ve a la carpeta frontend.

Haz doble clic en index.html.

Señal de vida: Verás la tabla con MAD-01 en color VERDE y los logs cargando a la derecha.