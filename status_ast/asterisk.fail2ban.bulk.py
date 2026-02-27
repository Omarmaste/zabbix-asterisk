#!/usr/bin/env python3
# create_fail2ban_items.py
# Crea automáticamente los items de fail2ban en Zabbix via API

import json
import urllib.request
import urllib.error

# ─── CONFIGURACIÓN ───────────────────────────────────────────────
ZABBIX_URL    = "http://localhost:8082/api_jsonrpc.php"
ZABBIX_USER   = "Admin"
ZABBIX_PASS   = "zabbix"
HOST_NAME     = "Zabbix server"
# ─────────────────────────────────────────────────────────────────

ITEMS = [
    {
        "name": "Fail2ban Status",
        "key_":  "fail2ban.status",
        "description": "Estado del servicio fail2ban: 1=activo, 0=caido",
        "value_type": 3,  # Numeric unsigned
        "units": "",
    },
    {
        "name": "Fail2ban Banned Total",
        "key_":  "fail2ban.banned.total",
        "description": "Total de IPs baneadas en todas las jails",
        "value_type": 3,
        "units": "IPs",
    },
    {
        "name": "Fail2ban Banned Asterisk",
        "key_":  "fail2ban.banned.asterisk",
        "description": "IPs baneadas en jail asterisk-iptables",
        "value_type": 3,
        "units": "IPs",
    },
    {
        "name": "Fail2ban Banned SSH",
        "key_":  "fail2ban.banned.ssh",
        "description": "IPs baneadas en jail sshd",
        "value_type": 3,
        "units": "IPs",
    },
]

def zabbix_api(token, method, params):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method":  method,
        "params":  params,
        "id":      1,
        "auth":    token,
    }).encode("utf-8")

    req = urllib.request.Request(
        ZABBIX_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    if "error" in result:
        raise Exception(f"API error: {result['error']}")
    return result["result"]


def login():
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method":  "user.login",
        "params":  {"username": ZABBIX_USER, "password": ZABBIX_PASS},
        "id":      1,
    }).encode("utf-8")
    req = urllib.request.Request(
        ZABBIX_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if "error" in result:
        raise Exception(f"Login error: {result['error']}")
    return result["result"]


def get_host_id(token):
    result = zabbix_api(token, "host.get", {
        "filter": {"host": [HOST_NAME]},
        "output": ["hostid", "host"],
    })
    if not result:
        raise Exception(f"Host '{HOST_NAME}' no encontrado en Zabbix")
    return result[0]["hostid"]


def item_exists(token, hostid, key):
    result = zabbix_api(token, "item.get", {
        "hostids": hostid,
        "filter":  {"key_": key},
        "output":  ["itemid", "key_"],
    })
    return len(result) > 0


def create_item(token, hostid, item):
    result = zabbix_api(token, "item.create", {
        "hostid":      hostid,
        "name":        item["name"],
        "key_":        item["key_"],
        "type":        2,           # Zabbix trapper
        "value_type":  item["value_type"],
        "description": item["description"],
        "units":       item["units"],
        "history":     "31d",
        "trends":      "365d",
    })
    return result["itemids"][0]


def main():
    print("=" * 55)
    print("  Creación de Items Fail2ban en Zabbix")
    print("=" * 55)

    # Login
    print(f"\n[1] Conectando a Zabbix: {ZABBIX_URL}")
    token = login()
    print(f"    ✓ Login exitoso")

    # Obtener host
    print(f"\n[2] Buscando host: {HOST_NAME}")
    hostid = get_host_id(token)
    print(f"    ✓ Host encontrado (ID: {hostid})")

    # Crear items
    print(f"\n[3] Creando items (trapper):")
    created = 0
    skipped = 0

    for item in ITEMS:
        if item_exists(token, hostid, item["key_"]):
            print(f"    ⚠  Ya existe: {item['key_']} — omitido")
            skipped += 1
        else:
            item_id = create_item(token, hostid, item)
            print(f"    ✓  Creado:    {item['key_']} (ID: {item_id})")
            created += 1

    print(f"\n{'='*55}")
    print(f"  Resultado: {created} creados, {skipped} omitidos")
    print(f"{'='*55}")
    print(f"\n  Próximo paso: ejecutar el script bash de recolección")
    print(f"  bash /etc/zabbix/scripts/asterisk.fail2ban")


if __name__ == "__main__":
    main()
