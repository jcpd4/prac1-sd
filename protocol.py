# Fichero: protocol.py
# Implementación del protocolo estándar <STX><DATA><ETX><LRC>
# Para comunicación entre módulos del sistema distribuido

import struct
import logging

# Constantes del protocolo
STX = 0x02  # Start of Text
ETX = 0x03  # End of Text
ACK = 0x06  # Acknowledgment
NACK = 0x15  # Negative Acknowledgment

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ProtocolError(Exception):
    """Excepción para errores del protocolo"""
    pass

def calculate_lrc(data):
    """
    Calcula el LRC (Longitudinal Redundancy Check) como XOR de todos los bytes del mensaje.
    
    Args:
        data (bytes): Datos para calcular LRC
        
    Returns:
        int: Valor LRC calculado
    """
    if not data:
        return 0
    
    lrc = 0
    for byte in data:
        lrc ^= byte
    return lrc & 0xFF  # Asegurar que sea un byte

def create_message(data):
    """
    Crea un mensaje completo con el protocolo <STX><DATA><ETX><LRC>.
    
    Args:
        data (str): Datos del mensaje como string
        
    Returns:
        bytes: Mensaje completo codificado
    """
    try:
        # Convertir string a bytes
        data_bytes = data.encode('utf-8')
        
        # Crear mensaje completo
        message = struct.pack('B', STX) + data_bytes + struct.pack('B', ETX)
        
        # Calcular LRC del mensaje completo (incluyendo STX y ETX)
        lrc = calculate_lrc(message)
        
        # Añadir LRC al final
        complete_message = message + struct.pack('B', lrc)
        
        logger.debug(f"Mensaje creado: {data} -> {complete_message.hex()}")
        return complete_message
        
    except Exception as e:
        logger.error(f"Error creando mensaje: {e}")
        raise ProtocolError(f"Error creando mensaje: {e}")

def parse_message(raw_data):
    """
    Parsea un mensaje recibido y valida el protocolo.
    
    Args:
        raw_data (bytes): Datos recibidos
        
    Returns:
        tuple: (data_string, is_valid, lrc_error)
    """
    try:
        if len(raw_data) < 3:  # Mínimo: STX + ETX + LRC
            logger.warning("Mensaje demasiado corto")
            return None, False, False
        
        # Verificar STX
        if raw_data[0] != STX:
            logger.warning(f"STX incorrecto: {raw_data[0]:02x}")
            return None, False, False
        
        # Buscar ETX
        etx_pos = -1
        for i in range(1, len(raw_data)):
            if raw_data[i] == ETX:
                etx_pos = i
                break
        
        if etx_pos == -1:
            logger.warning("ETX no encontrado")
            return None, False, False
        
        # Extraer datos (entre STX y ETX)
        data_bytes = raw_data[1:etx_pos]
        
        # Verificar LRC
        if etx_pos + 1 >= len(raw_data):
            logger.warning("LRC no encontrado")
            return None, False, False
        
        received_lrc = raw_data[etx_pos + 1]
        message_without_lrc = raw_data[:etx_pos + 1]  # STX + DATA + ETX
        calculated_lrc = calculate_lrc(message_without_lrc)
        
        lrc_valid = (received_lrc == calculated_lrc)
        
        if not lrc_valid:
            logger.warning(f"LRC inválido: recibido={received_lrc:02x}, calculado={calculated_lrc:02x}")
            return None, False, True
        
        # Decodificar datos
        try:
            data_string = data_bytes.decode('utf-8')
            logger.debug(f"Mensaje parseado correctamente: {data_string}")
            return data_string, True, False
        except UnicodeDecodeError as e:
            logger.error(f"Error decodificando datos: {e}")
            return None, False, False
            
    except Exception as e:
        logger.error(f"Error parseando mensaje: {e}")
        return None, False, False

def create_ack():
    """Crea un mensaje ACK."""
    return struct.pack('B', ACK)

def create_nack():
    """Crea un mensaje NACK."""
    return struct.pack('B', NACK)

def is_ack(data):
    """Verifica si los datos son un ACK."""
    return len(data) == 1 and data[0] == ACK

def is_nack(data):
    """Verifica si los datos son un NACK."""
    return len(data) == 1 and data[0] == NACK

def send_protocol_message(socket, message_data, timeout=5):
    """
    Envía un mensaje usando el protocolo y espera confirmación.
    
    Args:
        socket: Socket conectado
        message_data (str): Datos del mensaje
        timeout (int): Timeout en segundos
        
    Returns:
        bool: True si se recibió ACK, False si NACK o error
    """
    try:
        # Crear mensaje con protocolo
        protocol_message = create_message(message_data)
        
        # Enviar mensaje
        socket.sendall(protocol_message)
        logger.debug(f"Mensaje enviado: {message_data}")
        
        # Configurar timeout
        socket.settimeout(timeout)
        
        # Esperar respuesta
        response = socket.recv(1)
        
        if is_ack(response):
            logger.debug("ACK recibido")
            return True
        elif is_nack(response):
            logger.warning("NACK recibido")
            return False
        else:
            logger.warning(f"Respuesta inválida: {response.hex()}")
            return False
            
    except Exception as e:
        logger.error(f"Error enviando mensaje: {e}")
        return False
    finally:
        # Restaurar timeout por defecto
        socket.settimeout(None)

def receive_protocol_message(socket, timeout=5):
    """
    Recibe un mensaje usando el protocolo.
    
    Args:
        socket: Socket conectado
        timeout (int): Timeout en segundos
        
    Returns:
        tuple: (data_string, is_valid, should_ack)
    """
    try:
        socket.settimeout(timeout)
        
        # Leer datos
        raw_data = socket.recv(1024)
        if not raw_data:
            logger.warning("Conexión cerrada por el cliente")
            return None, False, False
        
        # Parsear mensaje
        data_string, is_valid, lrc_error = parse_message(raw_data)
        
        if is_valid:
            # Enviar ACK
            socket.sendall(create_ack())
            logger.debug("ACK enviado")
            return data_string, True, True
        else:
            # Enviar NACK
            socket.sendall(create_nack())
            logger.warning("NACK enviado")
            return data_string, False, True
            
    except Exception as e:
        logger.error(f"Error recibiendo mensaje: {e}")
        return None, False, False
    finally:
        socket.settimeout(None)

# Funciones de utilidad para el sistema
def create_register_message(cp_id, location, price=None):
    """Crea mensaje de registro de CP."""
    if price is not None:
        return f"REGISTER#{cp_id}#{location}#{price}"
    else:
        return f"REGISTER#{cp_id}#{location}"

def create_fault_message(cp_id):
    """Crea mensaje de avería."""
    return f"FAULT#{cp_id}"

def create_recover_message(cp_id):
    """Crea mensaje de recuperación."""
    return f"RECOVER#{cp_id}"

def create_command_message(command, cp_id=None):
    """Crea mensaje de comando."""
    if cp_id:
        return f"{command}#{cp_id}"
    else:
        return command

def parse_command_message(data_string):
    """
    Parsea un mensaje de comando y extrae sus componentes.
    
    Args:
        data_string (str): Mensaje parseado
        
    Returns:
        tuple: (command, params)
    """
    if not data_string:
        return None, []
    
    parts = data_string.split('#')
    command = parts[0].upper()
    params = parts[1:] if len(parts) > 1 else []
    
    return command, params

# Ejemplo de uso
if __name__ == "__main__":
    # Test del protocolo
    test_data = "REGISTER#CP01#Madrid#0.25"
    
    # Crear mensaje
    message = create_message(test_data)
    print(f"Mensaje creado: {message.hex()}")
    
    # Parsear mensaje
    parsed_data, is_valid, lrc_error = parse_message(message)
    print(f"Datos parseados: {parsed_data}")
    print(f"Válido: {is_valid}")
    print(f"Error LRC: {lrc_error}")
    
    # Test de comando
    command, params = parse_command_message(parsed_data)
    print(f"Comando: {command}")
    print(f"Parámetros: {params}")
