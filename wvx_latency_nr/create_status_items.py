#!/usr/bin/env python3
# Crea/actualiza items TRAPPER numericos para estado de agentes: status, platform,
# connection_type (con value maps de Zabbix para mostrar texto) y version (numerico).
# NOTA: el datasource Zabbix de Grafana no soporta graficar/tabular items de texto
# (probado: "non-metrics queries are not supported"), por eso estos campos van
# codificados como numero + value map en vez de texto plano. El campo "ip" se omite
# a proposito (alta cardinalidad, no mapeable).
import json, os, subprocess, requests, time

import pathlib as _pl, os as _os
_ef = next((p / ".env" for p in _pl.Path(__file__).resolve().parents if (p / ".env").is_file()), None)
if _ef:
    for _l in open(_ef):
        _l = _l.strip()
        if _l and not _l.startswith('#') and '=' in _l:
            _k, _, _v = _l.partition('=')
            _k, _v = _k.strip(), _v.strip()
            if _v[:1] in ('"', "'"):
                _q = _v[0]; _e = _v.find(_q, 1)
                _v = _v[1:_e] if _e != -1 else _v[1:]
            else:
                _v = _v.split('#', 1)[0].strip()
            if _k and _k not in _os.environ:
                _os.environ[_k] = _v
del _pl, _os, _ef

ZBX_URL   = os.environ.get("ZBX_URL",        "http://IP/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",       "Admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",       "CHANGE_ME")
HOST_NAME = os.environ.get("LATENCY_ZBX_HOST", os.environ.get("ZBX_HOST", "ippbx-cloud-issa5-redplus"))

WOLKVOX_URL       = os.environ.get("WOLKVOX_URL",       "https://wv0036.wolkvox.com/api/v2/real_time.php")
WOLKVOX_SERVER    = os.environ.get("WOLKVOX_SERVER",    "00XX")
WOLKVOX_TOKEN     = os.environ.get("WOLKVOX_TOKEN",     "CHANGE_ME")
WOLKVOX_OPERATION = os.environ.get("WOLKVOX_OPERATION", "unknown_operation")
# Tag para el "name" visible del item en Zabbix/Grafana: sin el prefijo de
# marca compartida "ALOGLOBAL-" (todas las operaciones lo llevan en
# WOLKVOX_OPERATION) para ganar ancho en las leyendas de los paneles globales.
_op_upper = WOLKVOX_OPERATION.upper()
DISPLAY_TAG = _op_upper[len("ALOGLOBAL-"):] if _op_upper.startswith("ALOGLOBAL-") else _op_upper

DELAY_BETWEEN_REQUESTS = 0.3
MAX_RETRIES = 2
session = requests.Session()

# Value maps: nombre -> mappings [(value, newvalue), ...]
# value "0" siempre = Otro/Desconocido (fallback para strings no reconocidos)
VALUEMAPS = {
    "WVX Agent Status": [
        ("0", "Otro/Desconocido"),
        ("1", "Conectado"),
        ("2", "Desconectado"),
    ],
    "WVX Agent Platform": [
        ("0", "Otro/Desconocido"),
        ("1", "APP"),
        ("2", "WEB"),
    ],
    "WVX Agent Connection": [
        ("0", "Otro/Desconocido"),
        ("1", "WiFi"),
        ("2", "Cable/Ethernet"),
    ],
}

# key_suffix -> (name_suffix, descripcion, nombre_del_valuemap o None si es numerico plano)
FIELDS = [
    ("status",          "Status",     "Estado del agente (codificado)",       "WVX Agent Status"),
    ("platform",        "Platform",   "Plataforma del agente (codificado)",   "WVX Agent Platform"),
    ("connection_type", "Connection", "Tipo de conexion (codificado)",        "WVX Agent Connection"),
    ("version",         "Version",    "Version del aplicativo (numerico)",    None),
]

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

def ensure_valuemaps(auth, hostid):
    """Crea (si no existen) los value maps a nivel de host y devuelve nombre->valuemapid."""
    existing = api("valuemap.get", {"hostids": hostid, "output": ["valuemapid", "name"]}, auth)
    by_name = {v["name"]: v["valuemapid"] for v in existing}
    for vm_name, pairs in VALUEMAPS.items():
        if vm_name in by_name:
            continue
        mappings = [{"type": "0", "value": v, "newvalue": nv} for v, nv in pairs]
        res = api("valuemap.create", {"hostid": hostid, "name": vm_name, "mappings": mappings}, auth)
        by_name[vm_name] = res["valuemapids"][0]
        print(f"  + Value map creado: {vm_name}")
    return by_name

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
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] === STATUS/CONNECTION ITEMS SYNC (numerico + valuemap) ===")
    print("[1/4] Autenticando en Zabbix...")
    auth = login()
    hostid = get_hostid(auth)
    print(f"  OK - Host ID: {hostid}")
    print("[2/4] Asegurando value maps...")
    valuemap_ids = ensure_valuemaps(auth, hostid)
    print("[3/4] Obteniendo agentes de Wolkvox...")
    agents = fetch_agents()
    print(f"  OK - {len(agents)} agentes encontrados")
    print(f"[4/4] Creando/actualizando items ({len(FIELDS)} por agente)...")
    created = updated = 0
    new_agents = set()
    for idx, (code, name) in enumerate(sorted(agents.items()), 1):
        for key_suffix, name_suffix, desc, vm_name in FIELDS:
            key_ = f"{WOLKVOX_OPERATION}.agent.{key_suffix}[{code}]"
            item_name = f"[{DISPLAY_TAG}] Agent {code} - {name} - {name_suffix}"
            params_common = {
                "name": item_name,
                "type": 2, "value_type": 3,  # trapper, unsigned int
                "history": "90d", "trends": "365d",
            }
            if vm_name:
                params_common["valuemapid"] = valuemap_ids[vm_name]
            it = item_by_key(auth, hostid, key_)
            try:
                if it:
                    api("item.update", {"itemid": it["itemid"], **params_common}, auth)
                    updated += 1
                else:
                    api("item.create", {
                        "hostid": hostid, "key_": key_,
                        "description": f"[{WOLKVOX_OPERATION}] {desc} del agente {code}",
                        **params_common
                    }, auth)
                    created += 1
                    new_agents.add(f"{code}-{name}")
                time.sleep(DELAY_BETWEEN_REQUESTS)
            except Exception as e:
                print(f"  ✗ ERR {code}/{key_suffix}: {e}")
    print(f"Total: {len(agents)} agentes x {len(FIELDS)} campos | Nuevos items: {created} | Actualizados: {updated}")
    if new_agents:
        print(f"Agentes nuevos: {', '.join(sorted(new_agents))}")

if __name__ == "__main__":
    main()
