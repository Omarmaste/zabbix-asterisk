#!/usr/bin/env python3
import os, sys, requests

# ================== CONFIG ==================
ZBX_URL   = os.environ.get("ZBX_URL",   "http://<IP>/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",  "admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",  "admin")
# Host EXACTO en Zabbix (puede ser nombre visible o técnico; usaremos el visible para la nueva sintaxis)
ZBX_HOST  = os.environ.get("ZBX_HOST",  "gatewayp")
VERIFY_TLS = os.environ.get("ZBX_VERIFY_TLS", "false").lower() == "true"

TRIGGER_NAME_PREFIX = os.environ.get("TRIGGER_NAME_PREFIX", "status_tsip_asterisk.")

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

def get_host(auth):
    # Trae host por nombre técnico y si no, por nombre visible
    res = api("host.get", {"filter":{"host":[ZBX_HOST]}, "output":["hostid","host","name"]}, auth)
    if not res:
        res = api("host.get", {"filter":{"name":[ZBX_HOST]}, "output":["hostid","host","name"]}, auth)
    if not res:
        raise RuntimeError(f"No se encontró el host '{ZBX_HOST}'")
    return res[0]  # {hostid, host (técnico), name (visible)}

def items_sip_status(auth, hostid):
    # Intento directo: nombre que empieza por "sip_status_"
    res = api("item.get", {
        "hostids": hostid,
        "search": {"name": "sip_status_"},
        "startSearch": 1,  # prefijo
        "output": ["itemid", "key_", "name", "value_type"]
    }, auth)

    # Fallback robusto: traer todo y filtrar en cliente
    if not res:
        res = api("item.get", {
            "hostids": hostid,
            "output": ["itemid", "key_", "name", "value_type"],
            "limit": 100000
        }, auth)

    final = []
    for it in res:
        name = it.get("name","")
        key_ = it.get("key_","")
        vtype = str(it.get("value_type",""))
        # Solo ítems de peers:
        # - name inicia con sip_status_
        # - key inicia con asterisk. y SIN '['
        # - Numeric (unsigned) => 3
        if not name.startswith("sip_status_"):
            continue
        if not key_.startswith("asterisk.") or "[" in key_:
            continue
        if vtype != "3":
            continue
        final.append(it)
    return final

def trigger_exists(auth, hostid, name):
    res = api("trigger.get", {
        "hostids": hostid,
        "filter": {"description": name},
        "output": ["triggerid"]
    }, auth)
    return bool(res)

def create_trigger_status_zero(auth, host_visible_name, item_key, trigger_name, peer):
    # >>> NUEVA SINTAXIS: last(/<host_visible>/<item_key>)=0
    expr = f"last(/{host_visible_name}/{item_key})=0"
    params = {
        "description": trigger_name,
        "expression": expr,
        "priority": 5,          # Disaster
        "manual_close": 0,
        "status": 0,            # enabled
        "tags": [
            {"tag": "service", "value": "asterisk"},
            {"tag": "peer", "value": peer}
        ],
    }
    return api("trigger.create", params, auth)

def main():
    try:
        auth = login()
        host = get_host(auth)
        hostid, host_visible = host["hostid"], host["name"]  # usamos NOMBRE VISIBLE en la nueva sintaxis

        items = items_sip_status(auth, hostid)
        if not items:
            print("No se encontraron ítems 'sip_status_*' (key 'asterisk.*' sin corchetes) en el host.")
            sys.exit(1)

        created = skipped = 0
        for it in items:
            key_ = it["key_"]                        # p.ej. asterisk.525589577915
            name = it.get("name", "")                # p.ej. sip_status_525589577915
            peer = name.split("sip_status_", 1)[1] if "sip_status_" in name else key_.split("asterisk.",1)[1]
            trig_name = f"{TRIGGER_NAME_PREFIX}{peer}"

            if trigger_exists(auth, hostid, trig_name):
                print(f"[SKIP] ya existe trigger: {trig_name}")
                skipped += 1
                continue

            res = create_trigger_status_zero(auth, host_visible, key_, trig_name, peer)
            print(f"[OK] creado trigger: {trig_name} -> id={res['triggerids'][0]}")
            created += 1

        print(f"\nResumen: creados={created}, existentes={skipped}")

    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(2)

if __name__ == "__main__":
    main()
