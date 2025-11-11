# Módulo auxiliar para UI del Monitor - similar a EV_Central
import os
from datetime import datetime

def clear_screen():
    """Limpia la pantalla de la consola."""
    os.system('cls' if os.name == 'nt' else 'clear')

def display_monitor_panel(cp_id, engine_status, central_status, connection_info, health_messages, protocol_messages):
    """
    Muestra el panel de monitorización del CP Monitor.
    
    Args:
        cp_id: ID del Charging Point
        engine_status: Estado del Engine ("OK", "KO", "Desconectado")
        central_status: Estado de conexión con Central ("Conectado", "Desconectado")
        connection_info: Diccionario con info de conexiones
        health_messages: Lista de últimos mensajes de health check (máximo 5)
        protocol_messages: Lista de últimos mensajes del protocolo (máximo 5)
    """
    clear_screen()
    
    # --- Encabezado ---
    print("=" * 80)
    print(f"  EV CHARGING POINT MONITOR: {cp_id}")
    print("=" * 80)
    print(f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # --- Sección 1: Estado de Conexiones ---
    print("-" * 80)
    print("*** ESTADO DE CONEXIONES ***")
    print("-" * 80)
    
    # Estado Engine
    engine_color = "✓" if engine_status == "OK" else ("✗" if engine_status == "KO" else "⚠")
    print(f"  Engine (Local):")
    print(f"    Estado: {engine_color} {engine_status}")
    if connection_info.get('engine'):
        print(f"    Conectado en: {connection_info['engine']}")
    
    # Estado Central
    central_color = "✓" if central_status == "Conectado" else "✗"
    print(f"  Central:")
    print(f"    Estado: {central_color} {central_status}")
    if connection_info.get('central'):
        print(f"    Conectado en: {connection_info['central']}")
    
    print()
    
    # --- Sección 2: Health Checks Recientes ---
    print("-" * 80)
    print("*** HEALTH CHECKS RECIENTES (últimos 5) ***")
    print("-" * 80)
    if health_messages:
        for msg in health_messages[-5:]:
            print(f"  {msg}")
    else:
        print("  No hay health checks registrados aún.")
    print()
    
    # --- Sección 3: Mensajes del Protocolo ---
    print("-" * 80)
    print("*** MENSAJES DEL PROTOCOLO (últimos 5) ***")
    print("-" * 80)
    if protocol_messages:
        for msg in protocol_messages[-5:]:
            print(f"  {msg}")
    else:
        print("  No hay mensajes del protocolo.")
    print()
    
    # --- Pie ---
    print("=" * 80)

