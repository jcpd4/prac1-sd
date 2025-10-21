# Fichero: logging_config.py
# Configuración centralizada del sistema de logging
# Para el sistema de puntos de recarga EV

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path

class ColoredFormatter(logging.Formatter):
    """Formateador con colores para terminal"""
    
    # Códigos de color ANSI
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Verde
        'WARNING': '\033[33m',   # Amarillo
        'ERROR': '\033[31m',     # Rojo
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'       # Reset
    }
    
    def format(self, record):
        # Añadir color al nivel de log
        if record.levelname in self.COLORS:
            record.levelname = f"{self.COLORS[record.levelname]}{record.levelname}{self.COLORS['RESET']}"
        
        return super().format(record)

def setup_logging(component_name, log_level=logging.INFO, log_to_file=True, log_to_console=True):
    """
    Configura el sistema de logging para un componente del sistema.
    
    Args:
        component_name (str): Nombre del componente (central, engine, monitor, driver)
        log_level: Nivel de logging (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_to_file (bool): Si escribir logs a archivo
        log_to_console (bool): Si mostrar logs en consola
        
    Returns:
        logging.Logger: Logger configurado
    """
    
    # Crear directorio de logs si no existe
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Configurar logger
    logger = logging.getLogger(component_name)
    logger.setLevel(log_level)
    
    # Limpiar handlers existentes
    logger.handlers.clear()
    
    # Formato de logs
    log_format = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # Handler para consola (con colores)
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_formatter = ColoredFormatter(log_format, date_format)
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
    
    # Handler para archivo
    if log_to_file:
        # Archivo principal del componente
        log_file = log_dir / f"{component_name}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_formatter = logging.Formatter(log_format, date_format)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        
        # Archivo rotativo para logs grandes
        rotating_file = log_dir / f"{component_name}_rotating.log"
        rotating_handler = logging.handlers.RotatingFileHandler(
            rotating_file, 
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        rotating_handler.setLevel(log_level)
        rotating_handler.setFormatter(file_formatter)
        logger.addHandler(rotating_handler)
    
    # Handler para errores críticos (archivo separado)
    if log_to_file:
        error_file = log_dir / f"{component_name}_errors.log"
        error_handler = logging.FileHandler(error_file, encoding='utf-8')
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_formatter)
        logger.addHandler(error_handler)
    
    return logger

def get_system_logger():
    """Obtiene el logger del sistema completo"""
    return setup_logging("system", log_level=logging.INFO)

def get_central_logger():
    """Obtiene el logger de la central"""
    return setup_logging("central", log_level=logging.INFO)

def get_engine_logger(cp_id):
    """Obtiene el logger del engine de un CP específico"""
    return setup_logging(f"engine_{cp_id}", log_level=logging.INFO)

def get_monitor_logger(cp_id):
    """Obtiene el logger del monitor de un CP específico"""
    return setup_logging(f"monitor_{cp_id}", log_level=logging.INFO)

def get_driver_logger(driver_id):
    """Obtiene el logger de un driver específico"""
    return setup_logging(f"driver_{driver_id}", log_level=logging.INFO)

def get_database_logger():
    """Obtiene el logger de la base de datos"""
    return setup_logging("database", log_level=logging.INFO)

def get_protocol_logger():
    """Obtiene el logger del protocolo"""
    return setup_logging("protocol", log_level=logging.DEBUG)

def log_system_event(logger, event_type, message, **kwargs):
    """
    Registra un evento del sistema con información estructurada.
    
    Args:
        logger: Logger a usar
        event_type (str): Tipo de evento (CONNECTION, TRANSACTION, ERROR, etc.)
        message (str): Mensaje del evento
        **kwargs: Información adicional del evento
    """
    event_data = {
        'timestamp': datetime.now().isoformat(),
        'event_type': event_type,
        'message': message,
        **kwargs
    }
    
    # Log estructurado
    logger.info(f"EVENT[{event_type}] {message} | Data: {event_data}")

def log_transaction(logger, transaction_id, action, cp_id=None, driver_id=None, **kwargs):
    """
    Registra una transacción del sistema.
    
    Args:
        logger: Logger a usar
        transaction_id: ID de la transacción
        action (str): Acción realizada
        cp_id: ID del punto de recarga
        driver_id: ID del conductor
        **kwargs: Información adicional
    """
    log_system_event(
        logger, 
        "TRANSACTION", 
        f"Transaction {transaction_id}: {action}",
        transaction_id=transaction_id,
        action=action,
        cp_id=cp_id,
        driver_id=driver_id,
        **kwargs
    )

def log_connection(logger, connection_type, status, remote_host=None, remote_port=None, **kwargs):
    """
    Registra eventos de conexión.
    
    Args:
        logger: Logger a usar
        connection_type (str): Tipo de conexión (SOCKET, KAFKA, etc.)
        status (str): Estado de la conexión (CONNECTED, DISCONNECTED, ERROR)
        remote_host: Host remoto
        remote_port: Puerto remoto
        **kwargs: Información adicional
    """
    log_system_event(
        logger,
        "CONNECTION",
        f"{connection_type} connection {status}",
        connection_type=connection_type,
        status=status,
        remote_host=remote_host,
        remote_port=remote_port,
        **kwargs
    )

def log_error(logger, error_type, message, exception=None, **kwargs):
    """
    Registra errores del sistema.
    
    Args:
        logger: Logger a usar
        error_type (str): Tipo de error
        message (str): Mensaje del error
        exception: Excepción capturada
        **kwargs: Información adicional
    """
    error_data = {
        'error_type': error_type,
        'message': message,
        **kwargs
    }
    
    if exception:
        error_data['exception'] = str(exception)
        error_data['exception_type'] = type(exception).__name__
    
    log_system_event(logger, "ERROR", f"{error_type}: {message}", **error_data)

def log_performance(logger, operation, duration_ms, **kwargs):
    """
    Registra métricas de rendimiento.
    
    Args:
        logger: Logger a usar
        operation (str): Operación realizada
        duration_ms (float): Duración en milisegundos
        **kwargs: Información adicional
    """
    log_system_event(
        logger,
        "PERFORMANCE",
        f"Operation {operation} completed in {duration_ms}ms",
        operation=operation,
        duration_ms=duration_ms,
        **kwargs
    )

# Configuración por defecto para desarrollo
def setup_development_logging():
    """Configura logging para desarrollo (más verboso)"""
    # Configurar nivel DEBUG para todos los componentes
    logging.getLogger().setLevel(logging.DEBUG)
    
    # Configurar logging de librerías externas
    logging.getLogger('kafka').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

def setup_production_logging():
    """Configura logging para producción (menos verboso)"""
    # Configurar nivel INFO para producción
    logging.getLogger().setLevel(logging.INFO)
    
    # Reducir logging de librerías externas
    logging.getLogger('kafka').setLevel(logging.ERROR)
    logging.getLogger('urllib3').setLevel(logging.ERROR)

# Función de utilidad para limpiar logs antiguos
def cleanup_old_logs(days_to_keep=7):
    """
    Limpia logs antiguos.
    
    Args:
        days_to_keep (int): Días de logs a mantener
    """
    log_dir = Path("logs")
    if not log_dir.exists():
        return
    
    cutoff_time = datetime.now().timestamp() - (days_to_keep * 24 * 60 * 60)
    
    for log_file in log_dir.glob("*.log"):
        if log_file.stat().st_mtime < cutoff_time:
            try:
                log_file.unlink()
                print(f"Eliminado log antiguo: {log_file}")
            except Exception as e:
                print(f"Error eliminando {log_file}: {e}")

# Ejemplo de uso
if __name__ == "__main__":
    # Configurar logging para pruebas
    logger = setup_logging("test", log_level=logging.DEBUG)
    
    # Probar diferentes tipos de logs
    logger.debug("Mensaje de debug")
    logger.info("Mensaje de información")
    logger.warning("Mensaje de advertencia")
    logger.error("Mensaje de error")
    logger.critical("Mensaje crítico")
    
    # Probar funciones especializadas
    log_system_event(logger, "TEST", "Evento de prueba", test_param="valor")
    log_transaction(logger, "TXN-001", "START", cp_id="MAD-01", driver_id="DRIVER-101")
    log_connection(logger, "SOCKET", "CONNECTED", remote_host="localhost", remote_port=8000)
    log_error(logger, "NETWORK_ERROR", "Conexión perdida", exception=Exception("Test error"))
    log_performance(logger, "DATABASE_QUERY", 150.5, query="SELECT * FROM cps")

