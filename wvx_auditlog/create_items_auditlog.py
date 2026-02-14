#!/usr/bin/env python3
"""
Script para crear items de audit log en Zabbix con prefijo de operación.
Uso: python3 create_audit_items.py <wvx_operacion> [host_name]

Ejemplos:
  python3 create_audit_items.py stargroup
  python3 create_audit_items.py stargroup monitoralo
  python3 create_audit_items.py aloglobal zabbix-server
"""

import json
import requests
import sys
import os

# ========= VALIDACIÓN DE ARGUMENTOS =========
if len(sys.argv) < 2:
    print("Error: Debe proporcionar el nombre de la operación Wolkvox")
    print()
    print("Uso: python3 create_audit_items.py <wvx_operacion> [host_name]")
    print()
    print("Ejemplos:")
    print("  python3 create_audit_items.py stargroup")
    print("  python3 create_audit_items.py stargroup monitoralo")
    print("  python3 create_audit_items.py aloglobal zabbix-server")
    sys.exit(1)

WVX_OPERACION = sys.argv[1]
HOST_NAME = sys.argv[2] if len(sys.argv) > 2 else "monitoralo"

# ========= ZABBIX =========
ZBX_URL   = "http://ip/zabbix/api_jsonrpc.php"
ZBX_USER  = "user"
ZBX_PASS  = "pass"

# ========= TIPOS DE ALERTAS =========
# Cada tipo se detecta por patrón en el campo "action"
# Severidad: 1=Info, 2=Warning, 3=Average, 4=High
ALERT_TYPES = [
    {
        "key_base": "audit.studio_compile",
        "name": "Studio Compile",
        "description": "Compilación en wolkvox Studio",
        "severity": 2,  # WARNING
        "pattern": "wolkvox studio: compile"
    },
    {
        "key_base": "audit.diagram_studio",
        "name": "Diagram Studio",
        "description": "Cambios en Diagram Studio",
        "severity": 1,  # INFO
        "pattern": "DIAGRAM STUDIO:"
    },
    {
        "key_base": "audit.refix",
        "name": "Refix Action",
        "description": "Acciones de Refix",
        "severity": 1,  # INFO
        "pattern": "REFIX:"
    },
    {
        "key_base": "audit.api_configuration",
        "name": "API Configuration",
        "description": "Cambios en configuración de API",
        "severity": 1,  # INFO
        "pattern": "API Configuration"
    },
    {
        "key_base": "audit.tts_activated",
        "name": "TTS Component Activated",
        "description": "Componente TTS activado",
        "severity": 1,  # INFO
        "pattern": "The TTS component has been activated"
    },
    {
        "key_base": "audit.nlp_ai_activated",
        "name": "NLP AI Activated",
        "description": "Componente NLP AI activado",
        "severity": 1,  # INFO
        "pattern": "The NLP AI component has been activated"
    },
    {
        "key_base": "audit.general_nlp_activated",
        "name": "General NLP Activated",
        "description": "Componente General NLP activado",
        "severity": 1,  # INFO
        "pattern": "The General NLP component has been activated"
    },
    {
        "key_base": "audit.predictive_stop",
        "name": "Predictive Campaign Stop",
        "description": "Detención de campaña predictiva",
        "severity": 1,  # INFO
        "pattern": "PREDICTIVE: Stop campaign"
    },
    {
        "key_base": "audit.delete_action",
        "name": "Delete Action",
        "description": "Acción de eliminación",
        "severity": 2,  # WARNING
        "pattern": "Delete"
    },
    {
        "key_base": "audit.profile_change",
        "name": "Profile Change",
        "description": "Cambio de perfil de usuario",
        "severity": 1,  # INFO
        "pattern": "changed their profile"
    }
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
    res = api("host.get", {"filter": {"host": [HOST_NAME]}, "output": ["hostid"]}, auth)
    if not res:
        raise SystemExit(f"Host no encontrado: {HOST_NAME}")
    return res[0]["hostid"]

def item_by_key(auth, hostid, key_):
    res = api("item.get", {
        "hostids": hostid,
        "filter": {"key_": key_},
        "output": ["itemid"]
    }, auth)
    return res[0] if res else None

def create_item_pair(auth, hostid, alert_config):
    """Crea o actualiza PAR de items: .data (TEXT) y .count (NUMERIC)"""
    
    # Agregar prefijo de operación a la key
    key_base_original = alert_config["key_base"]
    key_base = f"{WVX_OPERACION}.{key_base_original}"
    
    name = alert_config["name"]
    desc = alert_config["description"]
    
    results = {"data": None, "count": None}
    
    # ===== ITEM 1: .data (TEXT) =====
    key_data = f"{key_base}.data"
    it_data = item_by_key(auth, hostid, key_data)
    
    item_data_params = {
        "name": f"[{WVX_OPERACION.upper()}] Audit - {name} - Data",
        "type": 2,  # Trapper
        "value_type": 4,  # Text
        "history": "30d",
        "trends": "0",
        "description": f"[{WVX_OPERACION}] {desc} - JSON data"
    }
    
    try:
        if it_data:
            item_data_params["itemid"] = it_data["itemid"]
            api("item.update", item_data_params, auth)
            results["data"] = ("updated", it_data["itemid"])
        else:
            item_data_params["hostid"] = hostid
            item_data_params["key_"] = key_data
            result = api("item.create", item_data_params, auth)
            results["data"] = ("created", result["itemids"][0])
    except Exception as e:
        raise RuntimeError(f"Error creando {key_data}: {e}")
    
    # ===== ITEM 2: .count (NUMERIC) =====
    key_count = f"{key_base}.count"
    it_count = item_by_key(auth, hostid, key_count)
    
    item_count_params = {
        "name": f"[{WVX_OPERACION.upper()}] Audit - {name} - Count",
        "type": 2,  # Trapper
        "value_type": 3,  # Numeric (unsigned)
        "history": "30d",
        "trends": "365d",
        "description": f"[{WVX_OPERACION}] {desc} - Event counter for alerts"
    }
    
    try:
        if it_count:
            item_count_params["itemid"] = it_count["itemid"]
            api("item.update", item_count_params, auth)
            results["count"] = ("updated", it_count["itemid"])
        else:
            item_count_params["hostid"] = hostid
            item_count_params["key_"] = key_count
            result = api("item.create", item_count_params, auth)
            results["count"] = ("created", result["itemids"][0])
    except Exception as e:
        raise RuntimeError(f"Error creando {key_count}: {e}")
    
    return results, key_base

def main():
    print("=" * 80)
    print("CREACIÓN DE ITEMS PARA AUDIT LOG ALERTS")
    print("=" * 80)
    print()
    print(f"Operación Wolkvox: {WVX_OPERACION}")
    print(f"Host Zabbix: {HOST_NAME}")
    print(f"Prefijo de items: {WVX_OPERACION}.audit.*")
    print()
    
    print("[1/3] Autenticando en Zabbix...")
    try:
        auth = login()
        hostid = get_hostid(auth)
        print(f"  ✓ OK - Host ID: {hostid}")
    except Exception as e:
        print(f"  ✗ ERROR: {e}")
        sys.exit(1)
    
    print()
    print(f"[2/3] Procesando {len(ALERT_TYPES)} tipos de alertas...")
    print(f"      (Cada tipo genera 2 items: .data y .count)")
    print("-" * 80)
    
    stats = {"data_created": 0, "data_updated": 0, 
             "count_created": 0, "count_updated": 0, "errors": 0}
    
    created_keys = []
    
    for idx, alert_config in enumerate(ALERT_TYPES, 1):
        print(f"\n[{idx}/{len(ALERT_TYPES)}] {alert_config['name']}")
        
        try:
            results, full_key = create_item_pair(auth, hostid, alert_config)
            
            print(f"  Key base: {full_key}")
            
            # Resultado .data
            action_data, itemid_data = results["data"]
            status_data = "✓ CREADO" if action_data == "created" else "✓ ACTUALIZADO"
            print(f"  {status_data} - {full_key}.data (ID: {itemid_data})")
            if action_data == "created":
                stats["data_created"] += 1
            else:
                stats["data_updated"] += 1
            
            # Resultado .count
            action_count, itemid_count = results["count"]
            status_count = "✓ CREADO" if action_count == "created" else "✓ ACTUALIZADO"
            print(f"  {status_count} - {full_key}.count (ID: {itemid_count})")
            if action_count == "created":
                stats["count_created"] += 1
            else:
                stats["count_updated"] += 1
            
            created_keys.append(full_key)
                
        except Exception as e:
            stats["errors"] += 1
            print(f"  ✗ ERROR: {e}")
    
    print()
    print("-" * 80)
    print()
    print("[3/3] RESUMEN")
    print(f"  Operación: {WVX_OPERACION}")
    print(f"  Host: {HOST_NAME}")
    print(f"  Total tipos de alerta: {len(ALERT_TYPES)}")
    print(f"  Items .data - Creados: {stats['data_created']}, Actualizados: {stats['data_updated']}")
    print(f"  Items .count - Creados: {stats['count_created']}, Actualizados: {stats['count_updated']}")
    print(f"  Errores: {stats['errors']}")
    print()
    
    if stats["errors"] > 0:
        print("  ⚠ Completado con errores")
        sys.exit(1)
    else:
        print("  ✓ Completado exitosamente")
        print()
        print("ITEMS CREADOS CON PREFIJO:")
        for key in created_keys[:5]:  # Mostrar primeros 5
            print(f"  - {key}.data")
            print(f"  - {key}.count")
        if len(created_keys) > 5:
            print(f"  ... y {(len(created_keys) - 5) * 2} más")
        print()
        print("PRÓXIMO PASO:")
        print(f"  Ejecutar: bash monitor_audit_log.sh {WVX_OPERACION}")
    
    print("=" * 80)

if __name__ == "__main__":
    main()
