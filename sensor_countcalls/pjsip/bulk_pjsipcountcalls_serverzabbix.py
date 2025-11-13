#!/usr/bin/env python3
import os, sys, re, subprocess, requests

# ========= CONFIG =========
ZBX_URL   = os.environ.get("ZBX_URL",   "http://68.183.116.34/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",  "Admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",  "vonaGe3102iP")
HOST_NAME = os.environ.get("ZBX_HOST",  "nueveonce")  # nombre EXACTO del host en Zabbix

ZABBIX_CONF = os.environ.get("ZABBIX_CONF", "/etc/zabbix/zabbix_agentd.conf")
VERIFY_TLS = os.environ.get("ZBX_VERIFY_TLS", "false").lower() == "true"

# Configuración del ítem
ITEM_DELAY      = "1m"
ITEM_HISTORY_S  = 7776000       # 90 días
ITEM_TRENDS_S   = 31536000      # 365 días
ITEM_VALUE_TYPE = 3             # Numérico (sin signo)
ITEM_TYPE       = 0             # Zabbix agent
ITEM_UNITS      = "calls"

# Prefijo de clave en UserParameter
KEY_PREFIX = "asterisk.calls.pjsip"

session = requests.Session()

# ========== FUNCIONES API ZABBIX ==========
def api(method, params, auth=None):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    if auth:
        payload["auth"] = auth
    r = session.post(ZBX_URL, json=payload, verify=VERIFY_TLS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"API error: {data['error']}")
    return data["result"]

def login():
    return api("user.login", {"user": ZBX_USER, "password": ZBX_PASS})

def get_hostid(auth):
    res = api("host.get", {"filter": {"host": [HOST_NAME]}, "output": ["hostid"]}, auth)
    if not res:
        raise RuntimeError(f"No se encontró el host '{HOST_NAME}' en Zabbix")
    return res[0]["hostid"]

def get_agent_interfaceid(auth, hostid):
    ifs = api("hostinterface.get", {"hostids": hostid, "output": ["interfaceid", "type"]}, auth)
    for i in ifs:
        if str(i.get("type")) == "1":  # Zabbix agent
            return i["interfaceid"]
    if ifs:
        return ifs[0]["interfaceid"]
    raise RuntimeError("El host no tiene interfaz de agente Zabbix.")

# ========== EXTRAER ENDPOINTS PJSIP DESDE ZABBIX_CONF ==========
def get_pjsip_endpoints_from_conf():
    if not os.path.isfile(ZABBIX_CONF):
        raise RuntimeError(f"No existe archivo Zabbix Agent: {ZABBIX_CONF}")
    endpoints = []
    # Buscar líneas: UserParameter=asterisk.calls.pjsip.<endpoint>, ...
    pat = re.compile(r'^\s*UserParameter\s*=\s*asterisk\.calls\.pjsip\.([A-Za-z0-9_.\-]+)\s*,', re.ASCII)
    with open(ZABBIX_CONF, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = pat.search(line)
            if m:
                endpoints.append(m.group(1))
    endpoints = sorted(set(endpoints))
    if not endpoints:
        raise RuntimeError("No se encontraron endpoints PJSIP en zabbix_agentd.conf.")
    return endpoints

def item_exists(auth, hostid, key_):
    res = api("item.get", {"hostids": hostid, "filter": {"key_": key_}, "output": ["itemid"]}, auth)
    return bool(res)

def create_item(auth, hostid, interfaceid, endpoint, key_):
    params = {
        "hostid": hostid,
        "interfaceid": interfaceid,
        "name": f"Llamadas activas PJSIP: {endpoint}",
        "key_": key_,
        "type": ITEM_TYPE,
        "value_type": ITEM_VALUE_TYPE,
        "units": ITEM_UNITS,
        "delay": ITEM_DELAY,
        "history": ITEM_HISTORY_S,
        "trends": ITEM_TRENDS_S,
        "status": 0
    }
    return api("item.create", params, auth)

# ========== MAIN ==========
def main():
    try:
        auth = login()
        hostid = get_hostid(auth)
        ifaceid = get_agent_interfaceid(auth, hostid)

        endpoints = get_pjsip_endpoints_from_conf()

        created = skipped = 0
        for ep in endpoints:
            key_ = f"{KEY_PREFIX}.{ep}"
            if item_exists(auth, hostid, key_):
                skipped += 1
                print(f"[SKIP] ya existe: {key_}")
                continue
            res = create_item(auth, hostid, ifaceid, ep, key_)
            print(f"[OK] creado: {key_} -> itemid={res['itemids'][0]}")
            created += 1

        print(f"\nResumen: creados={created}, existentes={skipped}")

    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(2)

if __name__ == "__main__":
    main()

