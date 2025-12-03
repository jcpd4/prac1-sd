import pathlib

path = pathlib.Path("EV_CP_E.py")
text = path.read_text(encoding="utf-8")
normalized = text.replace("\r\n", "\n")
old = """            try:\n                if LAST_MONITOR_TS and (time.time() - LAST_MONITOR_TS) > 6:\n                    print(\"[ENGINE] Monitor no responde. Finalizando suministro de forma segura.\")\n                    with status_lock:\n                        ENGINE_STATUS['is_charging'] = False\n                    # Enviar fin normal con los acumulados actuales\n                    send_telemetry_message({\n                        \"type\": \"SUPPLY_END\",\n                        \"cp_id\": cp_id,\n                        \"user_id\": driver_id,\n                        \"kwh\": round(total_kwh, 3),\n                        \"importe\": round(total_importe, 2)\n                    })\n                    break\n            except Exception:\n                pass\n"""
new = """            try:\n                if LAST_MONITOR_TS and (time.time() - LAST_MONITOR_TS) > 6:\n                    print(\"[ENGINE] Monitor no responde. Interrumpiendo suministro y notificando a Central.\")\n                    with status_lock:\n                        ENGINE_STATUS['is_charging'] = False\n                    send_telemetry_message({\n                        \"type\": \"SUPPLY_ERROR\",\n                        \"cp_id\": cp_id,\n                        \"user_id\": driver_id,\n                        \"reason\": \"Carga interrumpida: Monitor desconectado\",\n                        \"kwh_partial\": round(total_kwh, 3),\n                        \"importe_partial\": round(total_importe, 2)\n                    })\n                    aborted_due_to_fault = True\n                    return\n            except Exception:\n                pass\n"""
if old not in normalized:
    raise SystemExit('bloque original no encontrado')
updated = normalized.replace(old, new, 1)
path.write_text(updated.replace("\n", "\r\n"), encoding="utf-8")
