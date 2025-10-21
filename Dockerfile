# Dockerfile para el sistema de puntos de recarga EV
# Imagen base con Python 3.9
FROM python:3.9-slim

# Metadatos
LABEL maintainer="Sistema Distribuidos - Práctica 1"
LABEL description="Sistema de puntos de recarga para vehículos eléctricos"

# Variables de entorno
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Crear directorio de trabajo
WORKDIR /app

# Copiar archivos de requisitos
COPY requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY . .

# Crear directorio para logs
RUN mkdir -p /app/logs

# Crear directorio para base de datos
RUN mkdir -p /app/data

# Exponer puertos (se configurarán en docker-compose)
EXPOSE 8000 8001 8002 9092

# Comando por defecto (se sobrescribirá en docker-compose)
CMD ["python", "EV_Central.py", "8000", "kafka:9092"]
