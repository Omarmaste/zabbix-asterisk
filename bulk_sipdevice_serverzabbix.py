#!/usr/bin/env python3
import os, re, sys, subprocess, requests

# ========= CONFIG =========
ZBX_URL   = os.environ.get("ZBX_URL",   "http://<IP>/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",  "admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",  "admin")
HOST_NAME = os.environ.get("ZBX_HOST",  "gatewayp")  # nombre EXACTO del host en Zabbix

# Por defecto NO verificar TLS (útil para HTTP o HTTPS self-signed).
# Para verificar, exporta ZBX_VERIFY_TLS=true
VERIFY_TLS = os.environ.get("ZBX_VERIFY_TLS", "false").lower() == "true"

ASTERISK_BIN = os.environ.get("ASTERISK_BIN", "/usr/sbin/asterisk")

# Ítem (Zabbix 6.x: history/trends en segundos)
ITEM_DELAY      = "1m"                          # Update interval
ITEM_SCHEDULE   = "50s/1-7,00:00-24:00"         # Flexible scheduling
ITEM_HISTORY_S  = 7776000                       # 90d en segundos
ITEM_TRENDS_S   = 31536000                      # 365d en segundos
ITEM_VALUE_TYPE = 3   # Numeric (unsigned)
ITEM_TYPE       = 0   # Zabbix agent
ITEM_UNITS      = "ms"

session = requests.Session()

def api(method, params, auth=None):
    payload = {"jsonrpc":"2.0","method":method,"params":params,"id":1}
    if auth: payload["auth"] = auth
    r = session.post(ZBX_URL, json=payload, verify=VERIFY_TLS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"API error: {data['error']}")
    return data["result"]

def login():
    return api("user.login", {"user": ZBX_USER, "password": ZBX_PASS})

def get_hostid(auth):
    res = api("host.get", {"filter":{"host":[HOST_NAME]}, "output":["hostid","host","name"]}, auth)
    if not res:
        res = api("host.get", {"filter":{"name":[HOST_NAME]}, "output":["hostid","host","name"]}, auth)
    if not res:
        raise RuntimeError(f"No se encontró el host '{HOST_NAME}' en Zabbix")
    return res[0]["hostid"]

def get_agent_interfaceid(auth, hostid):
    ifs = api("hostinterface.get", {"hostids": hostid, "output":["interfaceid","type"]}, auth)
    for i in ifs:
        if str(i.get("type")) == "1":  # 1 = Zabbix agent
            return i["interfaceid"]
    if ifs:
        return ifs[0]["interfaceid"]
    raise RuntimeError("El host no tiene interfaces. Agrega una interfaz de agente en Zabbix.")

def get_peers_from_asterisk():
    # Ejecuta en LOCAL (servidor Asterisk)
    cmd = [ASTERISK_BIN, "-rx", "sip show peers"]
    out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    if not isinstance(out, str):
        out = out.decode("utf-8", errors="ignore")

    peers = set()
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # Filtrar cabeceras y resúmenes
        if line.lower().startswith("name/username"):
            continue
        if "Monitored:" in line or "objects found" in line or "sip peers" in line.lower() or "sip devices" in line.lower():
            continue

        parts = line.split()
        first = parts[0] if parts else ""
        # Asegurar formato "name/username" en la primera columna
        if "/" not in first:
            continue
        if first.lower() == "name/username":
            continue

        peer = first.split("/", 1)[0].strip()
        if peer:
            peers.add(peer)

    return sorted(peers)

def item_exists(auth, hostid, key_):
    res = api("item.get", {"hostids": hostid, "filter":{"key_": key_}, "output":["itemid"]}, auth)
    return bool(res)

def create_item(auth, hostid, interfaceid, peer_name, key_):
    params = {
        "hostid": hostid,
        "interfaceid": interfaceid,
        # Prefijo para ordenar en UI
        "name": f"sip_status_{peer_name}",
        "key_": key_,                  # mantiene 'asterisk.<peer>' para compatibilidad con tus UserParameters
        "type": ITEM_TYPE,             # agent
        "value_type": ITEM_VALUE_TYPE, # Numeric (unsigned)
        "units": ITEM_UNITS,           # ms
        "delay": ITEM_DELAY,           # 1m
        "schedule": ITEM_SCHEDULE,     # flexible 50s todo el día
        "history": ITEM_HISTORY_S,     # 90d (segundos)
        "trends": ITEM_TRENDS_S,       # 365d (segundos)
        "status": 0                    # enabled
    }
    return api("item.create", params, auth)

def main():
    try:
        auth = login()
        hostid = get_hostid(auth)
        ifaceid = get_agent_interfaceid(auth, hostid)

        peers = get_peers_from_asterisk()
        if not peers:
            print("No se detectaron peers desde 'sip show peers'.")
            sys.exit(1)

        created = skipped = 0
        for p in peers:
            key_ = f"asterisk.{p}"
            if item_exists(auth, hostid, key_):
                skipped += 1
                print(f"[SKIP] ya existe: {key_}")
                continue
            res = create_item(auth, hostid, ifaceid, p, key_)
            print(f"[OK] creado: {key_} -> itemid={res['itemids'][0]}")
            created += 1

        print(f"\nResumen: creados={created}, existentes={skipped}")

    except subprocess.CalledProcessError as e:
        print(f"ERROR ejecutando asterisk: {e.output}")
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(2)

if __name__ == "__main__":
    main()
