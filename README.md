# Sistema Distribuido de Puntos de Recarga para Vehículos Eléctricos

## Descripción del Proyecto

Este proyecto implementa un sistema distribuido para la gestión de puntos de recarga de vehículos eléctricos, desarrollado como práctica de la asignatura Sistemas Distribuidos. El sistema incluye una central de control, puntos de recarga distribuidos, y aplicaciones para conductores.

## Arquitectura del Sistema

### Componentes Principales

1. **EV_Central.py** - Central de control y monitorización
2. **EV_CP_E.py** - Engine del punto de recarga
3. **EV_CP_M.py** - Monitor del punto de recarga
4. **EV_Driver.py** - Aplicación del conductor
5. **database.py** - Base de datos del sistema
6. **protocol.py** - Protocolo de comunicación estándar

### Tecnologías Utilizadas

- **Python 3.9+** - Lenguaje de programación
- **Docker & Docker Compose** - Contenedorización y orquestación
- **Apache Kafka** - Sistema de mensajería asíncrona
- **SQLite** - Base de datos (opcional)
- **Sockets TCP/IP** - Comunicación síncrona
- **Protocolo <STX><DATA><ETX><LRC>** - Estándar de comunicación

## Instalación y Configuración

### Requisitos Previos

- Python 3.9 o superior
- Docker y Docker Compose
- Git (para clonar el repositorio)

### Instalación Local (Desarrollo)

1. **Clonar el repositorio:**
   ```bash
   git clone <repository-url>
   cd prac1-sd
   ```

2. **Instalar dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configurar Kafka (opcional para desarrollo local):**
   ```bash
   # Instalar y ejecutar Kafka localmente
   # O usar Docker Compose para desarrollo
   ```

### Despliegue con Docker

#### Opción 1: Despliegue Completo en un Solo PC (Pruebas)

```bash
# Ejecutar todo el sistema en un PC
./deploy.sh all
# O en Windows:
deploy.bat all
```

#### Opción 2: Despliegue Distribuido en 3 PCs

**PC 1 - Central:**
```bash
./deploy.sh central
```

**PC 2 - Punto de Recarga 1:**
```bash
./deploy.sh cp1
```

**PC 3 - Punto de Recarga 2:**
```bash
./deploy.sh cp2
```

## Uso del Sistema

### Iniciar el Sistema

1. **Central (PC 1):**
   ```bash
   python EV_Central.py 8000 localhost:9092
   ```

2. **Punto de Recarga - Engine (PC 2/3):**
   ```bash
   python EV_CP_E.py 8001 localhost:9092 MAD-01
   ```

3. **Punto de Recarga - Monitor (PC 2/3):**
   ```bash
   python EV_CP_M.py localhost 8000 localhost 8001 MAD-01
   ```

4. **Driver (Cualquier PC):**
   ```bash
   python EV_Driver.py localhost:9092 DRIVER-101
   ```

### Comandos del Sistema

#### Central
- `P <CP_ID>` - Parar un punto de recarga específico
- `R <CP_ID>` - Reanudar un punto de recarga específico
- `PT` - Parar todos los puntos de recarga
- `RT` - Reanudar todos los puntos de recarga
- `Q` - Salir del sistema

#### Engine (Punto de Recarga)
- `F` - Simular avería
- `R` - Simular recuperación
- `I` - Iniciar suministro (simular enchufar vehículo)
- `E` - Finalizar suministro (simular desenchufar vehículo)

#### Driver
- `SOLICITAR <CP_ID>` - Solicitar recarga en un punto específico
- `BATCH <archivo>` - Procesar múltiples solicitudes desde archivo
- `Q` - Salir de la aplicación

## Estados del Sistema

### Estados de Puntos de Recarga

- **ACTIVADO** (Verde) - Punto disponible para recarga
- **SUMINISTRANDO** (Azul) - Punto suministrando energía
- **AVERIADO** (Rojo) - Punto con avería
- **FUERA_DE_SERVICIO** (Naranja) - Punto parado por la central
- **DESCONECTADO** (Gris) - Punto no conectado al sistema

### Flujo de Comunicación

1. **Registro:** Los puntos de recarga se registran en la central
2. **Autorización:** Los conductores solicitan recarga a través de Kafka
3. **Suministro:** La central autoriza y el punto inicia el suministro
4. **Telemetría:** El punto envía datos de consumo cada segundo
5. **Finalización:** Se genera ticket final con consumo y coste

## Protocolo de Comunicación

El sistema implementa el protocolo estándar `<STX><DATA><ETX><LRC>`:

- **STX (0x02)** - Start of Text
- **DATA** - Datos del mensaje
- **ETX (0x03)** - End of Text  
- **LRC** - Longitudinal Redundancy Check (XOR de todos los bytes)

### Tipos de Mensajes

- `REGISTER#CP_ID#LOCATION#PRICE` - Registro de punto de recarga
- `FAULT#CP_ID` - Reporte de avería
- `RECOVER#CP_ID` - Recuperación de avería
- `PARAR#CP_ID` - Comando para parar
- `REANUDAR#CP_ID` - Comando para reanudar

## Configuración Avanzada

### Variables de Entorno

```bash
# Central
CENTRAL_PORT=8000
KAFKA_BROKER=localhost:9092

# Engine
ENGINE_PORT=8001
CP_ID=MAD-01

# Monitor
CENTRAL_IP=localhost
CENTRAL_PORT=8000
ENGINE_IP=localhost
ENGINE_PORT=8001

# Driver
CLIENT_ID=DRIVER-101
```

### Configuración de Base de Datos

El sistema soporta dos modos de base de datos:

1. **Modo Memoria** (por defecto):
   ```python
   USE_SQLITE = False
   ```

2. **Modo SQLite** (persistente):
   ```python
   USE_SQLITE = True
   ```

## Monitoreo y Logs

### Panel de Monitorización

La central muestra un panel en tiempo real con:
- Estado de todos los puntos de recarga
- Peticiones de conductores
- Mensajes del sistema
- Estadísticas de consumo

### Logs del Sistema

Los logs se almacenan en:
- `./logs/` - Directorio de logs
- `./data/` - Directorio de datos persistentes

### Comandos de Monitoreo

```bash
# Ver logs de todos los servicios
./deploy.sh logs

# Ver logs de un servicio específico
./deploy.sh logs central

# Parar todos los servicios
./deploy.sh stop

# Limpiar sistema
./deploy.sh clean
```

## Solución de Problemas

### Problemas Comunes

1. **Error de conexión a Kafka:**
   - Verificar que Kafka esté ejecutándose
   - Comprobar la configuración del broker

2. **Error de conexión entre componentes:**
   - Verificar que los puertos estén disponibles
   - Comprobar la configuración de red

3. **Error de base de datos:**
   - Verificar permisos de escritura en `./data/`
   - Comprobar la configuración de SQLite

### Logs de Depuración

```bash
# Habilitar logs detallados
export PYTHONPATH=.
python -u EV_Central.py 8000 localhost:9092
```

## Estructura del Proyecto

```
prac1-sd/
├── EV_Central.py              # Central de control
├── EV_CP_E.py                 # Engine del punto de recarga
├── EV_CP_M.py                 # Monitor del punto de recarga
├── EV_Driver.py               # Aplicación del conductor
├── database.py                # Base de datos
├── protocol.py                # Protocolo de comunicación
├── requirements.txt           # Dependencias Python
├── Dockerfile                # Imagen Docker
├── docker-compose.yml        # Orquestación completa
├── docker-compose-distributed.yml  # Orquestación distribuida
├── deploy.sh                 # Script de despliegue (Linux/Mac)
├── deploy.bat                # Script de despliegue (Windows)
├── README.md                 # Documentación
├── data/                     # Datos persistentes
└── logs/                     # Logs del sistema
```

## Contribución

Para contribuir al proyecto:

1. Fork del repositorio
2. Crear rama de feature (`git checkout -b feature/nueva-funcionalidad`)
3. Commit de cambios (`git commit -am 'Añadir nueva funcionalidad'`)
4. Push a la rama (`git push origin feature/nueva-funcionalidad`)
5. Crear Pull Request

## Licencia

Este proyecto está desarrollado como práctica académica para la asignatura Sistemas Distribuidos.

## Contacto

Para preguntas o soporte, contactar con el equipo de desarrollo.

---

**Nota:** Este sistema está diseñado para fines académicos y de demostración. Para uso en producción, se recomienda implementar medidas adicionales de seguridad y robustez.

