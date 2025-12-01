import database
import sys

# Asegúrate de que la base de datos esté configurada
database.setup_database()

# Obtener los logs de auditoría
logs = database.get_audit_logs(limit=200)

if not logs:
    print("--- NO SE ENCONTRARON LOGS DE AUDITORÍA ---")
    print("Asegúrate de que la Central haya estado corriendo y se hayan generado eventos.")
else:
    print(f"--- ÚLTIMOS {len(logs)} LOGS DE AUDITORÍA (PERSISTENCIA OK) ---")
    # Imprimir la cabecera
    print("{:<22} {:<15} {:<30} {:<6} {}".format("TIMESTAMP", "IP", "ACCIÓN", "CP_ID", "DESCRIPCIÓN"))
    print("-" * 100)
    
    # Imprimir cada log
    for log in logs:
        # Truncar descripción si es muy larga
        desc = log.get('description', '')[:60]
        print("{:<22} {:<15} {:<30} {:<6} {}".format(
            log['timestamp'], 
            log['source_ip'], 
            log['action'], 
            log['cp_id'] or 'N/A', 
            desc
        ))

print("-" * 100)