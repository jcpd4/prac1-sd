# Fichero: test_system.py
# Suite de pruebas para el sistema de puntos de recarga EV
# Incluye pruebas unitarias y de integración

import unittest
import threading
import time
import json
import socket
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Añadir el directorio actual al path para importar módulos
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Importar módulos del sistema
import database
import protocol
from logging_config import setup_logging
from error_handler import ErrorHandler, ConnectionManager
from config import Config

class TestDatabase(unittest.TestCase):
    """Pruebas para el módulo de base de datos"""
    
    def setUp(self):
        """Configuración inicial para cada prueba"""
        database.CP_DATA.clear()
        database.DRIVER_DATA.clear()
        database.TRANSACTION_HISTORY.clear()
        database.setup_database()
    
    def test_register_cp(self):
        """Prueba registro de punto de recarga"""
        database.register_cp("TEST-CP", "Test Location", 0.25)
        
        cp_data = database.CP_DATA.get("TEST-CP")
        self.assertIsNotNone(cp_data)
        self.assertEqual(cp_data['id'], "TEST-CP")
        self.assertEqual(cp_data['location'], "Test Location")
        self.assertEqual(cp_data['price'], 0.25)
        self.assertEqual(cp_data['status'], 'DESCONECTADO')
    
    def test_update_cp_status(self):
        """Prueba actualización de estado de CP"""
        database.register_cp("TEST-CP", "Test Location")
        database.update_cp_status("TEST-CP", "ACTIVADO")
        
        status = database.get_cp_status("TEST-CP")
        self.assertEqual(status, "ACTIVADO")
    
    def test_update_cp_consumption(self):
        """Prueba actualización de consumo"""
        database.register_cp("TEST-CP", "Test Location")
        database.update_cp_consumption("TEST-CP", 10.5, 2.625, "DRIVER-101")
        
        cp_data = database.CP_DATA.get("TEST-CP")
        self.assertEqual(cp_data['status'], 'SUMINISTRANDO')
        self.assertEqual(cp_data['kwh'], 10.5)
        self.assertEqual(cp_data['importe'], 2.625)
        self.assertEqual(cp_data['driver_id'], "DRIVER-101")
    
    def test_register_driver(self):
        """Prueba registro de conductor"""
        database.register_driver("DRIVER-101", "Test Driver", "test@example.com")
        
        driver_data = database.get_driver("DRIVER-101")
        self.assertIsNotNone(driver_data)
        self.assertEqual(driver_data['id'], "DRIVER-101")
        self.assertEqual(driver_data['name'], "Test Driver")
        self.assertEqual(driver_data['email'], "test@example.com")
    
    def test_transaction_management(self):
        """Prueba gestión de transacciones"""
        # Iniciar transacción
        transaction_id = database.start_transaction("TEST-CP", "DRIVER-101")
        self.assertIsNotNone(transaction_id)
        
        # Finalizar transacción
        result = database.end_transaction(transaction_id, 15.0, 3.75, "COMPLETADA")
        self.assertTrue(result)
        
        # Verificar historial
        history = database.get_transaction_history(driver_id="DRIVER-101")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['kwh'], 15.0)
        self.assertEqual(history[0]['importe'], 3.75)

class TestProtocol(unittest.TestCase):
    """Pruebas para el módulo de protocolo"""
    
    def test_create_message(self):
        """Prueba creación de mensaje con protocolo"""
        data = "REGISTER#CP01#Madrid#0.25"
        message = protocol.create_message(data)
        
        # Verificar estructura del mensaje
        self.assertEqual(message[0], protocol.STX)
        self.assertEqual(message[-2], protocol.ETX)
        
        # Verificar LRC
        lrc = protocol.calculate_lrc(message[:-1])
        self.assertEqual(message[-1], lrc)
    
    def test_parse_message(self):
        """Prueba parsing de mensaje"""
        data = "REGISTER#CP01#Madrid#0.25"
        message = protocol.create_message(data)
        
        parsed_data, is_valid, lrc_error = protocol.parse_message(message)
        
        self.assertTrue(is_valid)
        self.assertFalse(lrc_error)
        self.assertEqual(parsed_data, data)
    
    def test_parse_invalid_message(self):
        """Prueba parsing de mensaje inválido"""
        invalid_message = b"INVALID_MESSAGE"
        
        parsed_data, is_valid, lrc_error = protocol.parse_message(invalid_message)
        
        self.assertFalse(is_valid)
    
    def test_command_parsing(self):
        """Prueba parsing de comandos"""
        command, params = protocol.parse_command_message("REGISTER#CP01#Madrid#0.25")
        
        self.assertEqual(command, "REGISTER")
        self.assertEqual(params, ["CP01", "Madrid", "0.25"])
    
    def test_lrc_calculation(self):
        """Prueba cálculo de LRC"""
        data = b"REGISTER#CP01#Madrid"
        lrc = protocol.calculate_lrc(data)
        
        # LRC debe ser un byte
        self.assertIsInstance(lrc, int)
        self.assertGreaterEqual(lrc, 0)
        self.assertLessEqual(lrc, 255)

class TestErrorHandler(unittest.TestCase):
    """Pruebas para el manejador de errores"""
    
    def setUp(self):
        """Configuración inicial"""
        self.logger = setup_logging("test", log_level=logging.DEBUG, log_to_file=False)
        self.error_handler = ErrorHandler("test_component", self.logger)
    
    def test_handle_retryable_error(self):
        """Prueba manejo de error recuperable"""
        error = protocol.RetryableError("Test error")
        result = self.error_handler.handle_error(error, "test_context")
        
        # Debería intentar recuperación
        self.assertTrue(result)
    
    def test_handle_non_retryable_error(self):
        """Prueba manejo de error no recuperable"""
        error = protocol.NonRetryableError("Test error")
        result = self.error_handler.handle_error(error, "test_context")
        
        # No debería intentar recuperación
        self.assertFalse(result)
    
    def test_circuit_breaker(self):
        """Prueba circuit breaker"""
        # Simular múltiples errores
        for i in range(6):
            error = protocol.RetryableError(f"Test error {i}")
            self.error_handler.handle_error(error, "test_context")
        
        # Circuit breaker debería estar activo
        self.assertTrue(self.error_handler.circuit_breakers.get("test_context_RetryableError", False))
    
    def test_error_stats(self):
        """Prueba estadísticas de errores"""
        error = protocol.RetryableError("Test error")
        self.error_handler.handle_error(error, "test_context")
        
        stats = self.error_handler.get_error_stats()
        self.assertIn("error_counts", stats)
        self.assertIn("circuit_breakers", stats)

class TestConnectionManager(unittest.TestCase):
    """Pruebas para el gestor de conexiones"""
    
    def setUp(self):
        """Configuración inicial"""
        self.logger = setup_logging("test", log_level=logging.DEBUG, log_to_file=False)
        self.conn_manager = ConnectionManager("test_component", self.logger)
    
    def test_add_connection(self):
        """Prueba añadir conexión"""
        mock_connection = Mock()
        self.conn_manager.add_connection("test_conn", mock_connection)
        
        self.assertIn("test_conn", self.conn_manager.connections)
        self.assertTrue(self.conn_manager.connection_status["test_conn"])
    
    def test_get_connection(self):
        """Prueba obtener conexión"""
        mock_connection = Mock()
        self.conn_manager.add_connection("test_conn", mock_connection)
        
        retrieved_conn = self.conn_manager.get_connection("test_conn")
        self.assertEqual(retrieved_conn, mock_connection)
    
    def test_mark_connection_failed(self):
        """Prueba marcar conexión como fallida"""
        mock_connection = Mock()
        self.conn_manager.add_connection("test_conn", mock_connection)
        
        error = Exception("Test error")
        self.conn_manager.mark_connection_failed("test_conn", error)
        
        self.assertFalse(self.conn_manager.connection_status["test_conn"])
    
    def test_connection_stats(self):
        """Prueba estadísticas de conexiones"""
        mock_connection = Mock()
        self.conn_manager.add_connection("test_conn", mock_connection)
        
        stats = self.conn_manager.get_connection_stats()
        self.assertEqual(stats['total_connections'], 1)
        self.assertEqual(stats['active_connections'], 1)

class TestConfig(unittest.TestCase):
    """Pruebas para el módulo de configuración"""
    
    def setUp(self):
        """Configuración inicial"""
        self.config = Config()
    
    def test_default_config(self):
        """Prueba configuración por defecto"""
        self.assertIsNotNone(self.config.get("central.port"))
        self.assertIsNotNone(self.config.get("kafka.bootstrap_servers"))
        self.assertIsNotNone(self.config.get("database.db_file"))
    
    def test_set_get_config(self):
        """Prueba establecer y obtener configuración"""
        self.config.set("test.value", "test_data")
        value = self.config.get("test.value")
        self.assertEqual(value, "test_data")
    
    def test_component_config(self):
        """Prueba configuración de componentes"""
        central_config = self.config.get_component_config("central")
        self.assertIn("host", central_config)
        self.assertIn("port", central_config)
    
    def test_config_validation(self):
        """Prueba validación de configuración"""
        errors = self.config.validate_config()
        self.assertEqual(len(errors), 0)  # Configuración por defecto debería ser válida

class TestSystemIntegration(unittest.TestCase):
    """Pruebas de integración del sistema"""
    
    def setUp(self):
        """Configuración inicial"""
        database.CP_DATA.clear()
        database.DRIVER_DATA.clear()
        database.TRANSACTION_HISTORY.clear()
        database.setup_database()
    
    def test_cp_registration_flow(self):
        """Prueba flujo completo de registro de CP"""
        # Registrar CP
        database.register_cp("TEST-CP", "Test Location", 0.25)
        database.update_cp_status("TEST-CP", "ACTIVADO")
        
        # Verificar estado
        status = database.get_cp_status("TEST-CP")
        self.assertEqual(status, "ACTIVADO")
        
        # Simular suministro
        database.update_cp_consumption("TEST-CP", 10.0, 2.5, "DRIVER-101")
        
        # Verificar estado de suministro
        cp_data = database.CP_DATA.get("TEST-CP")
        self.assertEqual(cp_data['status'], 'SUMINISTRANDO')
        self.assertEqual(cp_data['kwh'], 10.0)
        self.assertEqual(cp_data['importe'], 2.5)
    
    def test_driver_transaction_flow(self):
        """Prueba flujo completo de transacción de conductor"""
        # Registrar conductor
        database.register_driver("DRIVER-101", "Test Driver")
        
        # Iniciar transacción
        transaction_id = database.start_transaction("TEST-CP", "DRIVER-101")
        
        # Simular suministro
        database.update_cp_consumption("TEST-CP", 15.0, 3.75, "DRIVER-101")
        
        # Finalizar transacción
        database.end_transaction(transaction_id, 15.0, 3.75, "COMPLETADA")
        
        # Verificar historial
        history = database.get_transaction_history(driver_id="DRIVER-101")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['kwh'], 15.0)
        self.assertEqual(history[0]['importe'], 3.75)

def run_performance_tests():
    """Ejecuta pruebas de rendimiento"""
    print("Ejecutando pruebas de rendimiento...")
    
    # Prueba de rendimiento de base de datos
    start_time = time.time()
    
    for i in range(1000):
        database.register_cp(f"CP-{i}", f"Location {i}", 0.25)
        database.update_cp_status(f"CP-{i}", "ACTIVADO")
    
    end_time = time.time()
    print(f"Registro de 1000 CPs: {end_time - start_time:.2f} segundos")
    
    # Prueba de rendimiento de protocolo
    start_time = time.time()
    
    for i in range(1000):
        data = f"REGISTER#CP-{i}#Location {i}#0.25"
        message = protocol.create_message(data)
        parsed_data, is_valid, lrc_error = protocol.parse_message(message)
    
    end_time = time.time()
    print(f"Procesamiento de 1000 mensajes: {end_time - start_time:.2f} segundos")

def run_stress_tests():
    """Ejecuta pruebas de estrés"""
    print("Ejecutando pruebas de estrés...")
    
    # Prueba de concurrencia
    def worker(worker_id):
        for i in range(100):
            cp_id = f"CP-{worker_id}-{i}"
            database.register_cp(cp_id, f"Location {worker_id}-{i}", 0.25)
            database.update_cp_status(cp_id, "ACTIVADO")
    
    threads = []
    start_time = time.time()
    
    for i in range(10):
        thread = threading.Thread(target=worker, args=(i,))
        threads.append(thread)
        thread.start()
    
    for thread in threads:
        thread.join()
    
    end_time = time.time()
    print(f"1000 operaciones concurrentes: {end_time - start_time:.2f} segundos")
    print(f"CPs registrados: {len(database.CP_DATA)}")

if __name__ == "__main__":
    # Configurar logging para pruebas
    import logging
    logging.basicConfig(level=logging.WARNING)  # Reducir verbosidad
    
    # Ejecutar pruebas unitarias
    print("Ejecutando pruebas unitarias...")
    unittest.main(argv=[''], exit=False, verbosity=2)
    
    # Ejecutar pruebas de rendimiento
    run_performance_tests()
    
    # Ejecutar pruebas de estrés
    run_stress_tests()
    
    print("\nTodas las pruebas completadas.")

