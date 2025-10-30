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
