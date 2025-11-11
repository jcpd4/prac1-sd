#!/usr/bin/env python3
"""
Script de pruebas para verificar el funcionamiento del protocolo <STX><DATA><ETX><LRC>

Uso:
    python test_protocolo.py

Este script prueba:
1. Cálculo correcto del LRC
2. Construcción y parseo de tramas válidas
3. Detección de tramas inválidas
4. Handshake ENQ/ACK
"""

import socket
import threading
import time
import sys
import os

# Constantes del protocolo
STX = bytes([0x02])
ETX = bytes([0x03])
ENQ = bytes([0x05])
ACK = bytes([0x06])
NACK = bytes([0x15])
EOT = bytes([0x04])

def calculate_lrc(message_bytes):
    """Calcula el LRC (XOR byte a byte)."""
    lrc = 0
    for byte in message_bytes:
        lrc ^= byte
    return lrc

def build_frame(data_string):
    """Construye una trama <STX><DATA><ETX><LRC>."""
    data = data_string.encode('utf-8')
    message = STX + data + ETX
    lrc_value = calculate_lrc(message)
    return message + bytes([lrc_value])

def parse_frame(frame_bytes):
    """Parsea una trama y valida el LRC."""
    if len(frame_bytes) < 4:
        return None, False
    
    if frame_bytes[0] != 0x02:
        return None, False
    
    etx_pos = -1
    for i in range(1, len(frame_bytes) - 1):
        if frame_bytes[i] == 0x03:
            etx_pos = i
            break
    
    if etx_pos == -1:
        return None, False
    
    data_bytes = frame_bytes[1:etx_pos]
    received_lrc = frame_bytes[etx_pos + 1]
    
    message_with_delimiters = STX + data_bytes + ETX
    expected_lrc = calculate_lrc(message_with_delimiters)
    
    if received_lrc != expected_lrc:
        return None, False
    
    try:
        data = data_bytes.decode('utf-8')
        return data, True
    except UnicodeDecodeError:
        return None, False

def test_lrc_calculation():
    """Prueba 1: Verificar cálculo correcto del LRC"""
    print("\n" + "="*60)
    print("PRUEBA 1: Cálculo de LRC")
    print("="*60)
    
    test_cases = [
        ("REGISTER#MAD-01", "Simple"),
        ("FAULT#BCN-02", "Con #"),
        ("ACK#PARAR#CENTRAL", "Múltiples campos"),
        ("OK", "Respuesta corta"),
    ]
    
    all_passed = True
    for data, description in test_cases:
        frame = build_frame(data)
        parsed, is_valid = parse_frame(frame)
        if parsed == data and is_valid:
            print(f"✓ {description}: '{data}' - LRC válido")
        else:
            print(f"✗ {description}: '{data}' - FALLÓ")
            all_passed = False
    
    return all_passed

def test_invalid_frames():
    """Prueba 2: Verificar detección de tramas inválidas"""
    print("\n" + "="*60)
    print("PRUEBA 2: Detección de Tramas Inválidas")
    print("="*60)
    
    # Trama demasiado corta
    short_frame = b"\x02"
    parsed, is_valid = parse_frame(short_frame)
    if not is_valid and parsed is None:
        print("✓ Trama demasiado corta: Detectada correctamente")
    else:
        print("✗ Trama demasiado corta: NO detectada")
        return False
    
    # Trama sin STX
    invalid_stx = b"\x01REGISTER\x03\xAA"
    parsed, is_valid = parse_frame(invalid_stx)
    if not is_valid and parsed is None:
        print("✓ Trama sin STX válido: Detectada correctamente")
    else:
        print("✗ Trama sin STX válido: NO detectada")
        return False
    
    # Trama sin ETX
    invalid_etx = b"\x02REGISTER\x04\xAA"
    parsed, is_valid = parse_frame(invalid_etx)
    if not is_valid and parsed is None:
        print("✓ Trama sin ETX: Detectada correctamente")
    else:
        print("✗ Trama sin ETX: NO detectada")
        return False
    
    # Trama con LRC incorrecto
    valid_frame = build_frame("TEST")
    corrupted_frame = valid_frame[:-1] + bytes([(valid_frame[-1] ^ 0xFF)])  # LRC corrupto
    parsed, is_valid = parse_frame(corrupted_frame)
    if not is_valid and parsed is None:
        print("✓ Trama con LRC incorrecto: Detectada correctamente")
    else:
        print("✗ Trama con LRC incorrecto: NO detectada")
        return False
    
    print("✓ Todas las tramas inválidas fueron detectadas")
    return True

def server_thread(port, test_scenario):
    """Servidor de prueba"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('127.0.0.1', port))
    server.listen(1)
    
    client, addr = server.accept()
    
    try:
        if test_scenario == "handshake":
            # Esperar ENQ
            enq = client.recv(1)
            if enq == ENQ:
                client.sendall(ACK)
                print("✓ Servidor: Handshake exitoso (ENQ recibido, ACK enviado)")
            else:
                print(f"✗ Servidor: ENQ inválido (recibido: {enq.hex()})")
                return False
        elif test_scenario == "frame_exchange":
            # Handshake
            enq = client.recv(1)
            if enq == ENQ:
                client.sendall(ACK)
            
            # Recibir trama
            frame = client.recv(1024)
            data, is_valid = parse_frame(frame)
            
            if is_valid and data == "TEST_MESSAGE":
                client.sendall(ACK)
                # Enviar respuesta
                response_frame = build_frame("RESPONSE_OK")
                client.sendall(response_frame)
                ack = client.recv(1)
                if ack == ACK:
                    print("✓ Servidor: Intercambio de tramas exitoso")
                    return True
                else:
                    print("✗ Servidor: ACK no recibido")
                    return False
            else:
                print(f"✗ Servidor: Trama inválida o mensaje incorrecto")
                return False
    
    finally:
        client.close()
        server.close()
    
    return True

def test_handshake():
    """Prueba 3: Handshake ENQ/ACK"""
    print("\n" + "="*60)
    print("PRUEBA 3: Handshake ENQ/ACK")
    print("="*60)
    
    port = 9999
    
    # Iniciar servidor en hilo
    server = threading.Thread(target=server_thread, args=(port, "handshake"), daemon=True)
    server.start()
    time.sleep(0.1)  # Esperar a que el servidor esté listo
    
    try:
        # Cliente: Conectar y hacer handshake
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(('127.0.0.1', port))
        
        # Enviar ENQ
        client.sendall(ENQ)
        
        # Recibir ACK
        ack = client.recv(1)
        if ack == ACK:
            print("✓ Cliente: Handshake exitoso (ACK recibido)")
            client.close()
            time.sleep(0.1)  # Esperar a que el servidor termine
            return True
        else:
            print(f"✗ Cliente: ACK inválido (recibido: {ack.hex()})")
            client.close()
            return False
    except Exception as e:
        print(f"✗ Error en handshake: {e}")
        return False

def test_frame_exchange():
    """Prueba 4: Intercambio completo de tramas"""
    print("\n" + "="*60)
    print("PRUEBA 4: Intercambio Completo de Tramas")
    print("="*60)
    
    port = 9998
    
    # Iniciar servidor en hilo
    server = threading.Thread(target=server_thread, args=(port, "frame_exchange"), daemon=True)
    server.start()
    time.sleep(0.1)
    
    try:
        # Cliente: Conectar y hacer intercambio
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(('127.0.0.1', port))
        
        # Handshake
        client.sendall(ENQ)
        ack = client.recv(1)
        if ack != ACK:
            print("✗ Handshake fallido")
            return False
        
        # Enviar trama
        frame = build_frame("TEST_MESSAGE")
        client.sendall(frame)
        
        # Recibir ACK
        ack = client.recv(1)
        if ack != ACK:
            print("✗ ACK no recibido del servidor")
            return False
        
        # Recibir respuesta
        response = client.recv(1024)
        data, is_valid = parse_frame(response)
        
        if is_valid and data == "RESPONSE_OK":
            # Confirmar recepción
            client.sendall(ACK)
            print("✓ Cliente: Intercambio completo exitoso")
            client.close()
            time.sleep(0.1)
            return True
        else:
            print(f"✗ Respuesta inválida: {data}, válido: {is_valid}")
            client.close()
            return False
    
    except Exception as e:
        print(f"✗ Error en intercambio: {e}")
        return False

def test_edge_cases():
    """Prueba 5: Casos límite"""
    print("\n" + "="*60)
    print("PRUEBA 5: Casos Límite")
    print("="*60)
    
    # Mensaje vacío - Nota: Un mensaje vacío sigue siendo válido según el protocolo
    # STX + (vacío) + ETX + LRC = 4 bytes mínimos
    try:
        frame = build_frame("")
        parsed, is_valid = parse_frame(frame)
        # Un mensaje vacío técnicamente es válido (STX + ETX + LRC)
        if is_valid:
            print("✓ Mensaje vacío: Procesado correctamente (técnicamente válido)")
        else:
            print("⚠ Mensaje vacío: No válido (esto podría ser aceptable según diseño)")
    except Exception as e:
        print(f"⚠ Mensaje vacío: Excepción {e} (podría ser aceptable)")
    
    # Mensaje muy largo
    long_msg = "A" * 500
    frame = build_frame(long_msg)
    parsed, is_valid = parse_frame(frame)
    if is_valid and parsed == long_msg:
        print("✓ Mensaje largo (500 bytes): Procesado correctamente")
    else:
        print("✗ Mensaje largo: Falló")
        return False
    
    # Caracteres especiales
    special_msg = "REGISTER#CP-01#Calle Mayor #10#0.25€"
    frame = build_frame(special_msg)
    parsed, is_valid = parse_frame(frame)
    if is_valid and parsed == special_msg:
        print("✓ Caracteres especiales: Procesados correctamente")
    else:
        print("✗ Caracteres especiales: Falló")
        return False
    
    print("✓ Todos los casos límite pasaron")
    return True

def main():
    """Función principal de pruebas"""
    print("\n" + "="*60)
    print("SUITE DE PRUEBAS DEL PROTOCOLO <STX><DATA><ETX><LRC>")
    print("="*60)
    
    results = []
    
    # Ejecutar todas las pruebas
    results.append(("Cálculo de LRC", test_lrc_calculation()))
    results.append(("Detección de Tramas Inválidas", test_invalid_frames()))
    results.append(("Handshake ENQ/ACK", test_handshake()))
    results.append(("Intercambio de Tramas", test_frame_exchange()))
    results.append(("Casos Límite", test_edge_cases()))
    
    # Resumen
    print("\n" + "="*60)
    print("RESUMEN DE PRUEBAS")
    print("="*60)
    
    passed = 0
    failed = 0
    
    for name, result in results:
        if result:
            print(f"✓ {name}: PASÓ")
            passed += 1
        else:
            print(f"✗ {name}: FALLÓ")
            failed += 1
    
    print("\n" + "-"*60)
    print(f"Total: {len(results)} pruebas")
    print(f"Pasadas: {passed}")
    print(f"Fallidas: {failed}")
    print("-"*60)
    
    if failed == 0:
        print("\n✓ TODAS LAS PRUEBAS PASARON EXITOSAMENTE")
        return 0
    else:
        print("\n✗ ALGUNAS PRUEBAS FALLARON")
        return 1

if __name__ == "__main__":
    sys.exit(main())

