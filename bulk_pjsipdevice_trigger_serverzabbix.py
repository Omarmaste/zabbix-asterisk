#!/usr/bin/env python3
import os, sys, requests

# ================== CONFIG ==================
ZBX_URL   = os.environ.get("ZBX_URL",   "http://<IP>/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",  "admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",  "admin")
ZBX_HOST  = os.environ.get("ZBX_HOST",  "gatewayd")  # nombre EXACTO del host (técnico o visible)
VERIFY_TLS = os.environ.get("ZBX_VERIFY_TLS", "false").lower() == "true"

TRIGGER_NAME_PREFIX = os.environ.get("TRIGGER_NAME_PREFIX", "status_tpjsip_asterisk.")
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

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
    res = api("host.get", {"filter":{"host":[ZBX_HOST]}, "output":["hostid","host","name"]}, auth)
    if not res:
        res = api("host.get", {"filter":{"name":[ZBX_HOST]}, "output":["hostid","host","name"]}, auth)
    if not res:
        raise RuntimeError(f"No se encontró el host '{ZBX_HOST}'")
    return res[0]  # {hostid, host, name}

def items_pjsip_status(auth, hostid):
    # Trae por key para no depender del nombre, y luego filtramos por nombre también.
    res = api("item.get", {
        "hostids": hostid,
        "search": {"key_": "asterisk.pjsip."},
        "output": ["itemid", "key_", "name", "value_type"]
    }, auth)

    final = []
    for it in res:
        name = it.get("name","") or ""
        key_ = it.get("key_","") or ""
        vtype = str(it.get("value_type",""))
        if not name.startswith("pjsip_status_"):
            continue
        if not key_.startswith("asterisk.pjsip."):
            continue
        if vtype not in ("0", "3"):  # 0=float, 3=unsigned
            continue
        final.append(it)

    if DEBUG:
        print(f"[DEBUG] Encontrados {len(final)} items PJSIP válidos")
        for it in final:
            print(f"  - {it['name']} :: {it['key_']} :: vtype={it['value_type']}")
    return final

def trigger_exists(auth, hostid, name):
    res = api("trigger.get", {
        "hostids": hostid,
        "filter": {"description": name},
        "output": ["triggerid"]
    }, auth)
    return bool(res)

def create_trigger_status_zero(auth, host_visible_name, item_key, trigger_name, peer):
    # Sencillo: alerta si el último valor = 0
    expr = f"last(/{host_visible_name}/{item_key})=0"

    # (Opcional) alternativa más estable: 3 últimos valores a 0 (descomenta esta línea y comenta la anterior)
    # expr = f"count(/{host_visible_name}/{item_key},#3,0,\"eq\")=3"

    params = {
        "description": trigger_name,
        "expression": expr,
        "priority": 5,          # Disaster (ajusta a 4=High si prefieres)
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
        hostid, host_visible = host["hostid"], host["name"]

        items = items_pjsip_status(auth, hostid)
        if not items:
            # Ayuda de depuración: muestra cuántos hay con ese prefijo aunque no pasen el filtro
            maybe = api("item.get", {
                "hostids": hostid,
                "search": {"key_": "asterisk.pjsip."},
                "output": ["itemid", "key_", "name", "value_type"],
            }, auth)
            print("No se encontraron ítems 'pjsip_status_*' con key 'asterisk.pjsip.'")
            print(f"Sugerencia: revisa value_type. Ejemplo de keys encontradas ({min(5,len(maybe))}):")
            for it in maybe[:5]:
                print(f"  - {it.get('name')} :: {it.get('key_')} :: vtype={it.get('value_type')}")
            sys.exit(1)

        created = skipped = 0
        for it in items:
            key_ = it["key_"]
            name = it.get("name", "")
            peer = name.split("pjsip_status_", 1)[1] if "pjsip_status_" in name else key_.split("asterisk.pjsip.",1)[1]
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
