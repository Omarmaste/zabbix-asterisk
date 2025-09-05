#!/usr/bin/env python3
import os, re, sys, subprocess, requests

# ========= CONFIG =========
ZBX_URL   = os.environ.get("ZBX_URL",   "http://<IP>/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",  "admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",  "admin")
HOST_NAME = os.environ.get("ZBX_HOST",  "gatewayd")  # nombre EXACTO del host en Zabbix

# Para HTTPS con self-signed, deja VERIFY_TLS en false
VERIFY_TLS = os.environ.get("ZBX_VERIFY_TLS", "false").lower() == "true"

ASTERISK_BIN = os.environ.get("ASTERISK_BIN", "/usr/sbin/asterisk")

# Ítem (Zabbix 6.x: history/trends en segundos)
ITEM_DELAY      = "1m"                          # Update interval
ITEM_SCHEDULE   = "50s/1-7,00:00-24:00"         # Flexible scheduling
ITEM_HISTORY_S  = 7776000                       # 90d en segundos
ITEM_TRENDS_S   = 31536000                      # 365d en segundos
ITEM_VALUE_TYPE = 0   # 0 = Numeric (float)  |  3 = Numeric (unsigned)
ITEM_TYPE       = 0   # 0 = Zabbix agent
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

def get_endpoints_from_asterisk():
    """
    Devuelve lista de endpoints PJSIP a partir de:
      asterisk -rx "pjsip show endpoints"
    Extrae el token tras 'Endpoint:' (antes de un '/'), evita la línea plantilla '<Endpoint/...'.
    """
    cmd = [ASTERISK_BIN, "-rx", "pjsip show endpoints"]
    out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    if not isinstance(out, str):
        out = out.decode("utf-8", errors="ignore")

    eps = set()
    for line in out.splitlines():
        m = re.match(r'^\s*Endpoint:\s+(.*)$', line)
        if not m:
            continue
        rest = m.group(1).strip()
        if not rest:
            continue
        first = rest.split()[0]
        name = first.split('/', 1)[0].strip()
        # Evitar la línea plantilla que empieza con '<'
        if not name or name.startswith('<'):
            continue
        eps.add(name)

    return sorted(eps)

def item_exists(auth, hostid, key_):
    res = api("item.get", {"hostids": hostid, "filter":{"key_": key_}, "output":["itemid"]}, auth)
    return bool(res)

def create_item(auth, hostid, interfaceid, endpoint, key_):
    params = {
        "hostid": hostid,
        "interfaceid": interfaceid,
        "name": f"pjsip_status_{endpoint}",
        "key_": key_,                  # debe coincidir con tu UserParameter
        "type": ITEM_TYPE,             # Zabbix agent
        "value_type": ITEM_VALUE_TYPE, # Numeric (float) por RTT con decimales
        "units": ITEM_UNITS,           # ms
        "delay": ITEM_DELAY,
        "schedule": ITEM_SCHEDULE,
        "history": ITEM_HISTORY_S,
        "trends": ITEM_TRENDS_S,
        "status": 0                    # enabled
    }
    return api("item.create", params, auth)

def main():
    try:
        auth = login()
        hostid = get_hostid(auth)
        ifaceid = get_agent_interfaceid(auth, hostid)

        endpoints = get_endpoints_from_asterisk()
        if not endpoints:
            print("No se detectaron endpoints desde 'pjsip show endpoints'.")
            sys.exit(1)

        created = skipped = 0
        for ep in endpoints:
            key_ = f"asterisk.pjsip.{ep}"
            if item_exists(auth, hostid, key_):
                skipped += 1
                print(f"[SKIP] ya existe: {key_}")
                continue
            res = create_item(auth, hostid, ifaceid, ep, key_)
            print(f"[OK] creado: {key_} -> itemid={res['itemids'][0]}")
            created += 1

        print(f"\nResumen: creados={created}, existentes={skipped}")

    except subprocess.CalledProcessError as e:
        msg = e.output.decode("utf-8", errors="ignore") if isinstance(e.output, (bytes,bytearray)) else str(e.output)
        print(f"ERROR ejecutando asterisk: {msg}")
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(2)

if __name__ == "__main__":
    main()
