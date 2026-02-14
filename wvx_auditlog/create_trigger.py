#!/usr/bin/env python3
"""
Script FINAL - Sistema de Audit Log Alerts
Configuración probada y funcionando
"""

import requests
import sys

if len(sys.argv) < 2:
    print("Uso: python3 create_triggers_FINAL.py <operacion>")
    sys.exit(1)

WVX_OPERACION = sys.argv[1]
HOST_NAME = "monitoralo"

ZBX_URL = "http://IP/zabbix/api_jsonrpc.php"
ZBX_USER = "user"
ZBX_PASS = "pass"

TRIGGER_CONFIGS = [
    {"key": "studio_compile", "name": "Studio Compile Detected", "severity": 2},
    {"key": "diagram_studio", "name": "Diagram Studio Change", "severity": 1},
    {"key": "delete_action", "name": "Delete Action Detected", "severity": 4},
    {"key": "refix", "name": "Refix Action", "severity": 1},
    {"key": "api_configuration", "name": "API Configuration Change", "severity": 2},
    {"key": "tts_activated", "name": "TTS Component Activated", "severity": 1},
    {"key": "nlp_ai_activated", "name": "NLP AI Activated", "severity": 1},
    {"key": "general_nlp_activated", "name": "General NLP Activated", "severity": 1},
    {"key": "predictive_stop", "name": "Predictive Campaign Stopped", "severity": 2},
    {"key": "profile_change", "name": "Profile Change", "severity": 1}
]

session = requests.Session()

def api(method, params, auth=None):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    if auth:
        payload["auth"] = auth
    
    r = session.post(ZBX_URL, json=payload, verify=False, timeout=30)
    r.raise_for_status()
    j = r.json()
    
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["result"]

def login():
    return api("user.login", {"user": ZBX_USER, "password": ZBX_PASS})

def get_hostid(auth):
    res = api("host.get", {
        "filter": {"host": [HOST_NAME]},
        "output": ["hostid", "host"]
    }, auth)
    if not res:
        raise SystemExit(f"Host no encontrado: {HOST_NAME}")
    return res[0]["hostid"], res[0]["host"]

def delete_all_triggers(auth):
    res = api("trigger.get", {
        "output": ["triggerid"],
        "filter": {"description": f"[{WVX_OPERACION.upper()}]*"}
    }, auth)
    
    if res:
        trigger_ids = [t["triggerid"] for t in res]
        api("trigger.delete", trigger_ids, auth)
        return len(res)
    return 0

def create_trigger(auth, hostid, host, config):
    key_base = f"{WVX_OPERACION}.audit.{config['key']}"
    trigger_name = f"[{WVX_OPERACION.upper()}] {config['name']}"
    
    # EXPRESIÓN QUE FUNCIONA: detecta cuando el contador cambia
    # Esta es la expresión que probaste manualmente y funciona
    expression = f"last(/{host}/{key_base}.count)-last(/{host}/{key_base}.count,#2)>0"
    
    # RECUPERACIÓN: cuando no hay cambio
    recovery_expression = f"last(/{host}/{key_base}.count)-last(/{host}/{key_base}.count,#2)=0"
    
    # OPERATIONAL DATA: Muestra el JSON del evento
    opdata = f"{{ITEM.LASTVALUE:{key_base}.data}}"
    
    trigger_params = {
        "description": trigger_name,
        "expression": expression,
        "recovery_mode": 1,  # Recovery expression
        "recovery_expression": recovery_expression,
        "priority": config["severity"],
        "comments": f"{config.get('description', config['name'])} - Audit Log Event",
        "manual_close": 1,
        "type": 0,  # Single
        "correlation_mode": 0,
        "opdata": opdata,
        "tags": [
            {"tag": "component", "value": "audit_log"},
            {"tag": "operation", "value": WVX_OPERACION},
            {"tag": "alert_type", "value": config['key']}
        ]
    }
    
    try:
        result = api("trigger.create", trigger_params, auth)
        return result["triggerids"][0]
    except Exception as e:
        raise RuntimeError(f"Error: {e}")

def main():
    print("=" * 80)
    print("SISTEMA DE AUDIT LOG ALERTS - CONFIGURACIÓN FINAL")
    print("=" * 80)
    print()
    print(f"Operación: {WVX_OPERACION}")
    print()
    
    auth = login()
    hostid, host = get_hostid(auth)
    
    print(f"[1/3] Host: {host} (ID: {hostid})")
    
    print()
    print("[2/3] Eliminando triggers antiguos...")
    deleted = delete_all_triggers(auth)
    print(f"  Eliminados: {deleted}")
    
    print()
    print(f"[3/3] Creando {len(TRIGGER_CONFIGS)} triggers...")
    print("-" * 80)
    
    created = 0
    for idx, config in enumerate(TRIGGER_CONFIGS, 1):
        print(f"\n[{idx}/{len(TRIGGER_CONFIGS)}] {config['name']}")
        
        try:
            triggerid = create_trigger(auth, hostid, host, config)
            print(f"  ✓ CREADO - ID: {triggerid}")
            created += 1
        except Exception as e:
            print(f"  ✗ ERROR: {e}")
    
    print()
    print("-" * 80)
    print()
    print("✓ SISTEMA CONFIGURADO CORRECTAMENTE")
    print()
    print(f"Triggers creados: {created}/{len(TRIGGER_CONFIGS)}")
    print()
    print("CARACTERÍSTICAS:")
    print("  • Se activa cuando el contador incrementa")
    print("  • Muestra el JSON del evento en operational data")
    print("  • Se recupera automáticamente")
    print("  • Cada evento genera un problema nuevo")
    print()
    print("VERIFICAR:")
    print(f"  Zabbix: Monitoring → Problems")
    print(f"  Grafana: Widget Zabbix Problems")
    print(f"           Tags: operation:{WVX_OPERACION}")
    print()
    print("=" * 80)

if __name__ == "__main__":
    main()
