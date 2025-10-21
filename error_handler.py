# Fichero: error_handler.py
# Sistema de manejo de errores y recuperación para el sistema distribuido EV
# Implementa patrones de recuperación automática y manejo robusto de fallos

import logging
import time
import threading
from functools import wraps
from typing import Callable, Any, Optional, Dict, List
import socket
import json
from kafka.errors import KafkaError, NoBrokersAvailable
from logging_config import log_error, log_system_event

class SystemError(Exception):
    """Excepción base para errores del sistema"""
    pass

class ConnectionError(SystemError):
    """Error de conexión"""
    pass

class ProtocolError(SystemError):
    """Error de protocolo"""
    pass

class DatabaseError(SystemError):
    """Error de base de datos"""
    pass

class KafkaError(SystemError):
    """Error de Kafka"""
    pass

class RetryableError(SystemError):
    """Error que puede ser reintentado"""
    pass

class NonRetryableError(SystemError):
    """Error que no debe ser reintentado"""
    pass

class ErrorHandler:
    """Manejador centralizado de errores del sistema"""
    
    def __init__(self, component_name: str, logger: logging.Logger):
        self.component_name = component_name
        self.logger = logger
        self.error_counts: Dict[str, int] = {}
        self.last_error_time: Dict[str, float] = {}
        self.circuit_breakers: Dict[str, bool] = {}
        
    def handle_error(self, error: Exception, context: str = "", **kwargs) -> bool:
        """
        Maneja un error y decide si debe ser reintentado.
        
        Args:
            error: Excepción capturada
            context: Contexto donde ocurrió el error
            **kwargs: Información adicional
            
        Returns:
            bool: True si el error fue manejado, False si debe propagarse
        """
        error_type = type(error).__name__
        error_key = f"{context}_{error_type}"
        
        # Incrementar contador de errores
        self.error_counts[error_key] = self.error_counts.get(error_key, 0) + 1
        self.last_error_time[error_key] = time.time()
        
        # Log del error
        log_error(
            self.logger,
            error_type,
            f"Error en {context}: {str(error)}",
            exception=error,
            component=self.component_name,
            context=context,
            error_count=self.error_counts[error_key],
            **kwargs
        )
        
        # Determinar si es un error recuperable
        if isinstance(error, RetryableError):
            return self._handle_retryable_error(error, context, **kwargs)
        elif isinstance(error, NonRetryableError):
            return self._handle_non_retryable_error(error, context, **kwargs)
        else:
            return self._handle_unknown_error(error, context, **kwargs)
    
    def _handle_retryable_error(self, error: Exception, context: str, **kwargs) -> bool:
        """Maneja errores que pueden ser reintentados"""
        error_key = f"{context}_{type(error).__name__}"
        
        # Verificar circuit breaker
        if self.circuit_breakers.get(error_key, False):
            log_system_event(
                self.logger,
                "CIRCUIT_BREAKER",
                f"Circuit breaker activo para {error_key}",
                component=self.component_name,
                context=context
            )
            return False
        
        # Activar circuit breaker si hay muchos errores
        if self.error_counts[error_key] > 5:
            self.circuit_breakers[error_key] = True
            log_system_event(
                self.logger,
                "CIRCUIT_BREAKER_ACTIVATED",
                f"Circuit breaker activado para {error_key}",
                component=self.component_name,
                context=context,
                error_count=self.error_counts[error_key]
            )
            return False
        
        # Intentar recuperación
        return self._attempt_recovery(error, context, **kwargs)
    
    def _handle_non_retryable_error(self, error: Exception, context: str, **kwargs) -> bool:
        """Maneja errores que no deben ser reintentados"""
        log_system_event(
            self.logger,
            "NON_RETRYABLE_ERROR",
            f"Error no recuperable en {context}",
            component=self.component_name,
            context=context,
            error=str(error)
        )
        return False
    
    def _handle_unknown_error(self, error: Exception, context: str, **kwargs) -> bool:
        """Maneja errores desconocidos"""
        # Por defecto, intentar recuperación para errores desconocidos
        return self._attempt_recovery(error, context, **kwargs)
    
    def _attempt_recovery(self, error: Exception, context: str, **kwargs) -> bool:
        """Intenta recuperación del error"""
        try:
            # Estrategias de recuperación específicas
            if isinstance(error, ConnectionError):
                return self._recover_connection(error, context, **kwargs)
            elif isinstance(error, KafkaError):
                return self._recover_kafka(error, context, **kwargs)
            elif isinstance(error, DatabaseError):
                return self._recover_database(error, context, **kwargs)
            else:
                # Recuperación genérica
                return self._generic_recovery(error, context, **kwargs)
        except Exception as recovery_error:
            log_error(
                self.logger,
                "RECOVERY_FAILED",
                f"Fallo en recuperación: {str(recovery_error)}",
                exception=recovery_error,
                component=self.component_name,
                context=context
            )
            return False
    
    def _recover_connection(self, error: Exception, context: str, **kwargs) -> bool:
        """Recuperación para errores de conexión"""
        log_system_event(
            self.logger,
            "CONNECTION_RECOVERY",
            f"Intentando recuperar conexión en {context}",
            component=self.component_name,
            context=context
        )
        
        # Esperar un poco antes de reintentar
        time.sleep(1)
        return True
    
    def _recover_kafka(self, error: Exception, context: str, **kwargs) -> bool:
        """Recuperación para errores de Kafka"""
        log_system_event(
            self.logger,
            "KAFKA_RECOVERY",
            f"Intentando recuperar Kafka en {context}",
            component=self.component_name,
            context=context
        )
        
        # Esperar más tiempo para Kafka
        time.sleep(2)
        return True
    
    def _recover_database(self, error: Exception, context: str, **kwargs) -> bool:
        """Recuperación para errores de base de datos"""
        log_system_event(
            self.logger,
            "DATABASE_RECOVERY",
            f"Intentando recuperar base de datos en {context}",
            component=self.component_name,
            context=context
        )
        
        # Intentar reconectar a la base de datos
        time.sleep(0.5)
        return True
    
    def _generic_recovery(self, error: Exception, context: str, **kwargs) -> bool:
        """Recuperación genérica"""
        log_system_event(
            self.logger,
            "GENERIC_RECOVERY",
            f"Recuperación genérica en {context}",
            component=self.component_name,
            context=context
        )
        
        time.sleep(1)
        return True
    
    def reset_circuit_breaker(self, context: str, error_type: str = None):
        """Resetea el circuit breaker para un contexto"""
        if error_type:
            error_key = f"{context}_{error_type}"
        else:
            # Resetear todos los circuit breakers del contexto
            for key in list(self.circuit_breakers.keys()):
                if key.startswith(context):
                    self.circuit_breakers[key] = False
            return
        
        self.circuit_breakers[error_key] = False
        self.error_counts[error_key] = 0
        
        log_system_event(
            self.logger,
            "CIRCUIT_BREAKER_RESET",
            f"Circuit breaker reseteado para {error_key}",
            component=self.component_name,
            context=context
        )
    
    def get_error_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas de errores"""
        return {
            'error_counts': self.error_counts.copy(),
            'circuit_breakers': self.circuit_breakers.copy(),
            'last_error_times': self.last_error_time.copy()
        }

def retry_on_error(max_retries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    Decorador para reintentar operaciones en caso de error.
    
    Args:
        max_retries: Número máximo de reintentos
        delay: Delay inicial entre reintentos
        backoff: Factor de multiplicación del delay
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_error = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        raise last_error
            
            return None
        return wrapper
    return decorator

def handle_socket_errors(func: Callable) -> Callable:
    """Decorador para manejar errores de socket"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except socket.error as e:
            raise ConnectionError(f"Socket error: {e}") from e
        except ConnectionRefusedError as e:
            raise ConnectionError(f"Connection refused: {e}") from e
        except ConnectionResetError as e:
            raise ConnectionError(f"Connection reset: {e}") from e
        except TimeoutError as e:
            raise ConnectionError(f"Connection timeout: {e}") from e
    return wrapper

def handle_kafka_errors(func: Callable) -> Callable:
    """Decorador para manejar errores de Kafka"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except NoBrokersAvailable as e:
            raise KafkaError(f"No Kafka brokers available: {e}") from e
        except KafkaError as e:
            raise KafkaError(f"Kafka error: {e}") from e
    return wrapper

def handle_database_errors(func: Callable) -> Callable:
    """Decorador para manejar errores de base de datos"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            raise DatabaseError(f"Database error: {e}") from e
    return wrapper

class ConnectionManager:
    """Gestor de conexiones con recuperación automática"""
    
    def __init__(self, component_name: str, logger: logging.Logger):
        self.component_name = component_name
        self.logger = logger
        self.connections: Dict[str, Any] = {}
        self.connection_status: Dict[str, bool] = {}
        self.error_handler = ErrorHandler(component_name, logger)
    
    def add_connection(self, name: str, connection: Any):
        """Añade una conexión al gestor"""
        self.connections[name] = connection
        self.connection_status[name] = True
    
    def get_connection(self, name: str) -> Optional[Any]:
        """Obtiene una conexión por nombre"""
        if name in self.connections and self.connection_status.get(name, False):
            return self.connections[name]
        return None
    
    def mark_connection_failed(self, name: str, error: Exception):
        """Marca una conexión como fallida"""
        self.connection_status[name] = False
        self.error_handler.handle_error(error, f"connection_{name}")
        
        log_system_event(
            self.logger,
            "CONNECTION_FAILED",
            f"Conexión {name} marcada como fallida",
            component=self.component_name,
            connection_name=name,
            error=str(error)
        )
    
    def mark_connection_recovered(self, name: str):
        """Marca una conexión como recuperada"""
        self.connection_status[name] = True
        
        log_system_event(
            self.logger,
            "CONNECTION_RECOVERED",
            f"Conexión {name} recuperada",
            component=self.component_name,
            connection_name=name
        )
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas de conexiones"""
        return {
            'total_connections': len(self.connections),
            'active_connections': sum(1 for status in self.connection_status.values() if status),
            'connection_status': self.connection_status.copy(),
            'error_stats': self.error_handler.get_error_stats()
        }

# Ejemplo de uso
if __name__ == "__main__":
    import logging
    
    # Configurar logging
    logger = logging.getLogger("test")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
    
    # Crear error handler
    error_handler = ErrorHandler("test_component", logger)
    
    # Simular errores
    try:
        raise ConnectionError("Simulated connection error")
    except Exception as e:
        error_handler.handle_error(e, "test_connection")
    
    # Crear connection manager
    conn_manager = ConnectionManager("test_component", logger)
    
    # Simular conexión
    conn_manager.add_connection("test_conn", "mock_connection")
    print("Connection stats:", conn_manager.get_connection_stats())

