#!/usr/bin/env python3
# Crea/actualiza items TRAPPER para latencia de agentes
import json, os, subprocess, requests, time

# Carga .env desde la raíz del proyecto (sin dependencias externas)
import pathlib as _pl, os as _os
_ef = next((p / ".env" for p in _pl.Path(__file__).resolve().parents if (p / ".env").is_file()), None)
if _ef:
    for _l in open(_ef):
        _l = _l.strip()
        if _l and not _l.startswith('#') and '=' in _l:
            _k, _, _v = _l.partition('=')
            _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
            if _k and _k not in _os.environ:
                _os.environ[_k] = _v
del _pl, _os, _ef

ZBX_URL   = os.environ.get("ZBX_URL",        "http://IP/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",       "Admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",       "CHANGE_ME")
HOST_NAME = os.environ.get("LATENCY_ZBX_HOST", os.environ.get("ZBX_HOST", "ippbx-cloud-issa5-redplus"))

WOLKVOX_URL    = os.environ.get("WOLKVOX_URL",    "https://wv0025.wolkvox.com/api/v2/real_time.php")
WOLKVOX_SERVER = os.environ.get("WOLKVOX_SERVER", "00XX")
WOLKVOX_TOKEN  = os.environ.get("WOLKVOX_TOKEN",  "CHANGE_ME")

DELAY_BETWEEN_REQUESTS = 0.3
MAX_RETRIES = 2
session = requests.Session()

def api(method, params, auth=None):
    payload = {"jsonrpc":"2.0","method":method,"params":params,"id":1}
    if auth: payload["auth"] = auth
    r = session.post(ZBX_URL, json=payload, verify=False, timeout=30)
    r.raise_for_status()
    j = r.json()
    if "error" in j: raise RuntimeError(j["error"])
    return j["result"]

def login(): return api("user.login", {"user":ZBX_USER,"password":ZBX_PASS})

def get_hostid(auth):
    res = api("host.get", {"filter":{"host":[HOST_NAME]}, "output":["hostid"]}, auth)
    if not res: raise SystemExit(f"Host no encontrado: {HOST_NAME}")
    return res[0]["hostid"]

def item_by_key(auth, hostid, key_):
    res = api("item.get", {"hostids":hostid, "filter":{"key_":key_}, "output":["itemid"]}, auth)
    return res[0] if res else None

def fetch_agents():
    url = f"{WOLKVOX_URL}?api=latency"
    for attempt in range(MAX_RETRIES):
        try:
            cmd = ["curl","-sS","-H",f"wolkvox_server: {WOLKVOX_SERVER}",
                   "-H",f"wolkvox-token: {WOLKVOX_TOKEN}",url]
            raw = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=30)
            j = json.loads(raw.decode("utf-8",errors="ignore"))
            agents = {}
            for item in j.get("data",[]):
                for agent in item.get("by_agent",[]):
                    agent_id = agent.get("agent_id","")
                    if agent_id and "-" in agent_id:
                        code = agent_id.split("-")[0]
                        name = agent_id.split("-")[1] if len(agent_id.split("-"))>1 else code
                        agents[code] = name
            if agents: return agents
            if attempt < MAX_RETRIES-1: time.sleep(3)
        except Exception:
            if attempt < MAX_RETRIES-1: time.sleep(3)
            else: raise
    raise RuntimeError("No se pudieron obtener agentes")

def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] === LATENCY ITEMS SYNC ===")
    print("[1/3] Autenticando en Zabbix...")
    auth = login()
    hostid = get_hostid(auth)
    print(f"  OK - Host ID: {hostid}")
    print("[2/3] Obteniendo agentes de Wolkvox...")
    agents = fetch_agents()
    print(f"  OK - {len(agents)} agentes encontrados")
    print(f"[3/3] Creando/actualizando items...")
    created = updated = 0
    new_agents = []
    for idx, (code, name) in enumerate(sorted(agents.items()), 1):
        key_ = f"agent.latency[{code}]"
        item_name = f"Agent {code} - {name} - Latency"
        it = item_by_key(auth, hostid, key_)
        try:
            if it:
                api("item.update", {
                    "itemid": it["itemid"], "name": item_name,
                    "type": 2, "value_type": 0, "units": "ms",
                    "history": "90d", "trends": "365d"
                }, auth)
                updated += 1
            else:
                api("item.create", {
                    "hostid": hostid, "name": item_name, "key_": key_,
                    "type": 2, "value_type": 0, "units": "ms",
                    "history": "90d", "trends": "365d",
                    "description": f"Latencia del agente {code}"
                }, auth)
                created += 1
                new_agents.append(f"{code}-{name}")
                print(f"  ✓ NEW {code} - {name}")
            time.sleep(DELAY_BETWEEN_REQUESTS)
        except Exception as e:
            print(f"  ✗ ERR {code}: {e}")
    print(f"Total: {len(agents)} | Nuevos: {created} | Actualizados: {updated}")
    if new_agents:
        print(f"Nuevos agentes: {', '.join(new_agents)}")

if __name__ == "__main__":
    main()
