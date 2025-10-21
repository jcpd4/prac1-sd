# Fichero: config.py
# Configuración centralizada del sistema de puntos de recarga EV
# Permite configurar el sistema sin modificar el código

import os
import json
from typing import Dict, Any, Optional
from pathlib import Path

class Config:
    """Configuración centralizada del sistema"""
    
    def __init__(self, config_file: str = "config.json"):
        self.config_file = Path(config_file)
        self.config = self._load_default_config()
        self._load_config()
    
    def _load_default_config(self) -> Dict[str, Any]:
        """Carga configuración por defecto"""
        return {
            # Configuración de la Central
            "central": {
                "host": "0.0.0.0",
                "port": 8000,
                "max_connections": 10,
                "timeout": 30,
                "panel_refresh_interval": 2
            },
            
            # Configuración de Kafka
            "kafka": {
                "bootstrap_servers": ["localhost:9092"],
                "topics": {
                    "driver_requests": "driver_requests",
                    "cp_telemetry": "cp_telemetry",
                    "central_actions": "central_actions",
                    "driver_notifications": "driver_notifications"
                },
                "producer": {
                    "acks": "all",
                    "retries": 3,
                    "linger_ms": 5,
                    "batch_size": 16384
                },
                "consumer": {
                    "auto_offset_reset": "latest",
                    "enable_auto_commit": True,
                    "session_timeout_ms": 30000
                }
            },
            
            # Configuración de Base de Datos
            "database": {
                "use_sqlite": True,
                "db_file": "ev_charging.db",
                "backup_interval": 3600,  # segundos
                "max_backups": 7
            },
            
            # Configuración de Logging
            "logging": {
                "level": "INFO",
                "log_to_file": True,
                "log_to_console": True,
                "log_dir": "logs",
                "max_log_size": 10485760,  # 10MB
                "backup_count": 5
            },
            
            # Configuración de Puntos de Recarga
            "charging_points": {
                "default_price": 0.25,  # €/kWh
                "max_charging_time": 3600,  # segundos
                "health_check_interval": 1,  # segundos
                "telemetry_interval": 1  # segundos
            },
            
            # Configuración de Drivers
            "drivers": {
                "max_concurrent_requests": 5,
                "request_timeout": 30,  # segundos
                "batch_delay": 4  # segundos entre requests en batch
            },
            
            # Configuración de Red
            "network": {
                "socket_timeout": 30,
                "connection_retry_attempts": 3,
                "connection_retry_delay": 5,  # segundos
                "keepalive": True
            },
            
            # Configuración de Seguridad
            "security": {
                "enable_encryption": False,
                "cert_file": None,
                "key_file": None,
                "ca_file": None
            },
            
            # Configuración de Monitoreo
            "monitoring": {
                "enable_metrics": True,
                "metrics_interval": 60,  # segundos
                "alert_thresholds": {
                    "error_rate": 0.1,
                    "response_time": 5.0,  # segundos
                    "connection_failures": 3
                }
            }
        }
    
    def _load_config(self):
        """Carga configuración desde archivo"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    file_config = json.load(f)
                    self._merge_config(self.config, file_config)
            except Exception as e:
                print(f"Error cargando configuración: {e}")
                print("Usando configuración por defecto")
    
    def _merge_config(self, base: Dict[str, Any], override: Dict[str, Any]):
        """Fusiona configuración de archivo con configuración por defecto"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value
    
    def save_config(self):
        """Guarda configuración actual en archivo"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error guardando configuración: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Obtiene un valor de configuración"""
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any):
        """Establece un valor de configuración"""
        keys = key.split('.')
        config = self.config
        
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        config[keys[-1]] = value
    
    def get_central_config(self) -> Dict[str, Any]:
        """Obtiene configuración de la Central"""
        return self.get("central", {})
    
    def get_kafka_config(self) -> Dict[str, Any]:
        """Obtiene configuración de Kafka"""
        return self.get("kafka", {})
    
    def get_database_config(self) -> Dict[str, Any]:
        """Obtiene configuración de base de datos"""
        return self.get("database", {})
    
    def get_logging_config(self) -> Dict[str, Any]:
        """Obtiene configuración de logging"""
        return self.get("logging", {})
    
    def get_charging_points_config(self) -> Dict[str, Any]:
        """Obtiene configuración de puntos de recarga"""
        return self.get("charging_points", {})
    
    def get_drivers_config(self) -> Dict[str, Any]:
        """Obtiene configuración de drivers"""
        return self.get("drivers", {})
    
    def get_network_config(self) -> Dict[str, Any]:
        """Obtiene configuración de red"""
        return self.get("network", {})
    
    def get_security_config(self) -> Dict[str, Any]:
        """Obtiene configuración de seguridad"""
        return self.get("security", {})
    
    def get_monitoring_config(self) -> Dict[str, Any]:
        """Obtiene configuración de monitoreo"""
        return self.get("monitoring", {})
    
    def update_from_env(self):
        """Actualiza configuración desde variables de entorno"""
        env_mappings = {
            "CENTRAL_PORT": "central.port",
            "CENTRAL_HOST": "central.host",
            "KAFKA_BROKER": "kafka.bootstrap_servers",
            "DB_FILE": "database.db_file",
            "LOG_LEVEL": "logging.level",
            "CP_DEFAULT_PRICE": "charging_points.default_price"
        }
        
        for env_var, config_key in env_mappings.items():
            value = os.getenv(env_var)
            if value is not None:
                # Convertir tipos según sea necesario
                if config_key.endswith('.port'):
                    value = int(value)
                elif config_key.endswith('.bootstrap_servers'):
                    value = [value]
                elif config_key.endswith('.default_price'):
                    value = float(value)
                
                self.set(config_key, value)
    
    def validate_config(self) -> List[str]:
        """Valida la configuración y devuelve errores"""
        errors = []
        
        # Validar puertos
        central_port = self.get("central.port")
        if not isinstance(central_port, int) or central_port < 1 or central_port > 65535:
            errors.append("central.port debe ser un número entre 1 y 65535")
        
        # Validar configuración de Kafka
        kafka_servers = self.get("kafka.bootstrap_servers")
        if not isinstance(kafka_servers, list) or len(kafka_servers) == 0:
            errors.append("kafka.bootstrap_servers debe ser una lista no vacía")
        
        # Validar configuración de base de datos
        db_file = self.get("database.db_file")
        if not isinstance(db_file, str) or len(db_file) == 0:
            errors.append("database.db_file debe ser una cadena no vacía")
        
        # Validar configuración de logging
        log_level = self.get("logging.level")
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if log_level not in valid_levels:
            errors.append(f"logging.level debe ser uno de: {valid_levels}")
        
        return errors
    
    def get_component_config(self, component: str) -> Dict[str, Any]:
        """Obtiene configuración específica para un componente"""
        component_configs = {
            "central": {
                "host": self.get("central.host"),
                "port": self.get("central.port"),
                "max_connections": self.get("central.max_connections"),
                "timeout": self.get("central.timeout"),
                "panel_refresh_interval": self.get("central.panel_refresh_interval")
            },
            "engine": {
                "health_check_interval": self.get("charging_points.health_check_interval"),
                "telemetry_interval": self.get("charging_points.telemetry_interval"),
                "default_price": self.get("charging_points.default_price"),
                "max_charging_time": self.get("charging_points.max_charging_time")
            },
            "monitor": {
                "health_check_interval": self.get("charging_points.health_check_interval"),
                "connection_retry_attempts": self.get("network.connection_retry_attempts"),
                "connection_retry_delay": self.get("network.connection_retry_delay")
            },
            "driver": {
                "max_concurrent_requests": self.get("drivers.max_concurrent_requests"),
                "request_timeout": self.get("drivers.request_timeout"),
                "batch_delay": self.get("drivers.batch_delay")
            }
        }
        
        return component_configs.get(component, {})

# Instancia global de configuración
config = Config()

# Funciones de conveniencia
def get_config() -> Config:
    """Obtiene la instancia global de configuración"""
    return config

def get_central_config() -> Dict[str, Any]:
    """Obtiene configuración de la Central"""
    return config.get_central_config()

def get_kafka_config() -> Dict[str, Any]:
    """Obtiene configuración de Kafka"""
    return config.get_kafka_config()

def get_database_config() -> Dict[str, Any]:
    """Obtiene configuración de base de datos"""
    return config.get_database_config()

def get_logging_config() -> Dict[str, Any]:
    """Obtiene configuración de logging"""
    return config.get_logging_config()

# Ejemplo de uso
if __name__ == "__main__":
    # Crear configuración
    cfg = Config()
    
    # Mostrar configuración actual
    print("Configuración actual:")
    print(json.dumps(cfg.config, indent=2))
    
    # Validar configuración
    errors = cfg.validate_config()
    if errors:
        print("Errores de configuración:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("Configuración válida")
    
    # Mostrar configuración de componentes
    print("\nConfiguración de Central:")
    print(json.dumps(cfg.get_component_config("central"), indent=2))
    
    print("\nConfiguración de Kafka:")
    print(json.dumps(cfg.get_component_config("engine"), indent=2))

