#!/usr/bin/env python3
import os, sys, re, subprocess, requests

# ========= CONFIG =========
ZBX_URL   = os.environ.get("ZBX_URL",   "http://172.27.127.89/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",  "Admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",  "vonaGe3102iP")
HOST_NAME = os.environ.get("ZBX_HOST",  "startgroup")  # nombre EXACTO del host en Zabbix

# Fuente de peers: 'agent_conf' (por defecto) o 'sip_show_peers'
PEER_SOURCE = os.environ.get("PEER_SOURCE", "agent_conf").lower()

# Ruta del agente para Opción A
ZABBIX_CONF = os.environ.get("ZABBIX_CONF", "/etc/zabbix/zabbix_agentd.conf")

# Binario Asterisk para Opción B
ASTERISK_BIN = os.environ.get("ASTERISK_BIN", "/usr/sbin/asterisk")

# Puedes forzar peers extra (separados por coma) si algún peer no aparece en la fuente elegida
EXTRA_PEERS = [p.strip() for p in os.environ.get("EXTRA_PEERS", "").split(",") if p.strip()]

# TLS verify
VERIFY_TLS = os.environ.get("ZBX_VERIFY_TLS", "false").lower() == "true"

# Ítem (Zabbix 6.x: history/trends en segundos)
ITEM_DELAY      = "1m"
ITEM_HISTORY_S  = 7776000       # 90 días
ITEM_TRENDS_S   = 31536000      # 365 días
ITEM_VALUE_TYPE = 3             # Numeric (unsigned)
ITEM_TYPE       = 0             # Zabbix agent
ITEM_UNITS      = "calls"

# Key prefix (debe existir como UserParameter=asterisk.calls.<peer>)
KEY_PREFIX = "asterisk.calls"

session = requests.Session()

def api(method, params, auth=None):
    payload = {"jsonrpc":"2.0","method":method,"params":params,"id":1}
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
    res = api("host.get", {"filter":{"host":[HOST_NAME]}, "output":["hostid","host","name"]}, auth)
    if not res:
        res = api("host.get", {"filter":{"name":[HOST_NAME]}, "output":["hostid","host","name"]}, auth)
    if not res:
        raise RuntimeError(f"No se encontró el host '{HOST_NAME}' en Zabbix")
    return res[0]["hostid"]

def get_agent_interfaceid(auth, hostid):
    ifs = api("hostinterface.get", {"hostids": hostid, "output":["interfaceid","type"]}, auth)
    for i in ifs:
        if str(i.get("type")) == "1":  # Zabbix agent
            return i["interfaceid"]
    if ifs:
        return ifs[0]["interfaceid"]
    raise RuntimeError("El host no tiene interfaz de agente.")

# ---------- Opción A: leer peers desde zabbix_agentd.conf ----------
def get_peers_from_agent_conf():
    if not os.path.isfile(ZABBIX_CONF):
        raise RuntimeError(f"No existe ZABBIX_CONF: {ZABBIX_CONF}")
    peers = []
    # Busca líneas tipo: UserParameter=asterisk.calls.Telmex_New, /etc/zabbix/scripts/countcalls_tsip_Telmex_New
    pat = re.compile(r'^\s*UserParameter\s*=\s*asterisk\.calls\.([A-Za-z0-9_.\-]+)\s*,', re.ASCII)
    with open(ZABBIX_CONF, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = pat.search(line)
            if m:
                peers.append(m.group(1))
    peers = sorted(set(peers + EXTRA_PEERS))
    if not peers:
        raise RuntimeError("No se encontraron peers en zabbix_agentd.conf (UserParameter=asterisk.calls.<peer>, ...)")
    return peers

# ---------- Opción B: leer peers desde 'sip show peers' (parsing robusto) ----------
def strip_ansi(s):
    # quita escape codes ANSI si los hubiera
    return re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', s)

def get_peers_from_sip_show_peers():
    out = subprocess.check_output([ASTERISK_BIN, "-rx", "sip show peers"], stderr=subprocess.STDOUT)
    if not isinstance(out, str):
        out = out.decode("utf-8", errors="ignore")
    out = strip_ansi(out)
    peers = set()
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("name/username"):  # cabecera
            continue
        if ("monitored:" in s) or ("objects found" in low) or ("sip peers" in low) or ("sip devices" in low):
            continue
        # Extrae el primer token (columna 1) y toma lo que esté antes de la primera "/"
        # Permitimos letras, números, _, -, . en el nombre del peer.
        m = re.match(r'^\s*([A-Za-z0-9_.\-]+)\/', s)
        if m:
            peers.add(m.group(1))
    peers = sorted(peers.union(EXTRA_PEERS))
    if not peers:
        raise RuntimeError("No se detectaron peers desde 'sip show peers'.")
    return peers

def item_exists(auth, hostid, key_):
    res = api("item.get", {"hostids": hostid, "filter":{"key_": key_}, "output":["itemid"]}, auth)
    return bool(res)

def create_item(auth, hostid, interfaceid, peer_name, key_):
    params = {
        "hostid": hostid,
        "interfaceid": interfaceid,
        "name": f"countcalls_tsip_{peer_name}",
        "key_": key_,                  # asterisk.calls.<peer>
        "type": ITEM_TYPE,
        "value_type": ITEM_VALUE_TYPE,
        "units": ITEM_UNITS,           # calls
        "delay": ITEM_DELAY,           # 1m
        "history": ITEM_HISTORY_S,     # 90d
        "trends": ITEM_TRENDS_S,       # 365d
        "status": 0
    }
    return api("item.create", params, auth)

def main():
    try:
        auth = login()
        hostid = get_hostid(auth)
        ifaceid = get_agent_interfaceid(auth, hostid)

        if PEER_SOURCE == "agent_conf":
            peers = get_peers_from_agent_conf()
        elif PEER_SOURCE == "sip_show_peers":
            peers = get_peers_from_sip_show_peers()
        else:
            raise RuntimeError("PEER_SOURCE debe ser 'agent_conf' o 'sip_show_peers'.")

        created = skipped = 0
        for p in peers:
            key_ = f"{KEY_PREFIX}.{p}"
            if item_exists(auth, hostid, key_):
                skipped += 1
                print(f"[SKIP] ya existe: {key_}")
                continue
            res = create_item(auth, hostid, ifaceid, p, key_)
            print(f"[OK] creado: {key_} -> itemid={res['itemids'][0]}")
            created += 1

        print(f"\nResumen: creados={created}, existentes={skipped}")

    except subprocess.CalledProcessError as e:
        msg = e.output.decode("utf-8", errors="ignore") if isinstance(e.output, (bytes, bytearray)) else str(e.output)
        print(f"ERROR ejecutando asterisk: {msg}")
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(2)

if __name__ == "__main__":
    main()
