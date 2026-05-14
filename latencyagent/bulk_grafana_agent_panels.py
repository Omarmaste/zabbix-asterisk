#!/usr/bin/env python3
"""
v2 - Cambios respecto a v1:
  - LAT_H = 2 (antes 1) para que el numero se renderice
  - NR sin unidad "%" porque son contadores, no porcentajes
  - MARKER bumped a v2 para reemplazo limpio de los paneles v1
"""
import argparse
import json
import os
import re
import sys
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# ============================================================
# CONFIGURACIÓN — valores desde .env o variables de entorno
# ============================================================

# Zabbix
ZBX_URL   = os.environ.get("ZBX_URL",        "http://68.183.116.34/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",       "Admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",       "CHANGE_ME")
HOST_NAME = os.environ.get("LATENCY_ZBX_HOST", os.environ.get("ZBX_HOST", "ippbx-cloud-issa5-redplus"))

# Grafana
GRAFANA_URL    = os.environ.get("GRAFANA_URL",           "http://68.183.116.34:3000")
DASHBOARD_UID  = os.environ.get("GRAFANA_DASHBOARD_UID", "CHANGE_ME")
GRAFANA_DS_UID = os.environ.get("GRAFANA_DS_UID",        "CHANGE_ME")

# Autenticación Grafana — opción 1 (token) tiene prioridad sobre usuario+pass
GRAFANA_TOKEN  = os.environ.get("GRAFANA_TOKEN", "")
GRAFANA_USER   = os.environ.get("GRAFANA_USER",  "admin")
GRAFANA_PASS   = os.environ.get("GRAFANA_PASS",  "CHANGE_ME")

# Layout
PANELS_PER_ROW = 8
PANEL_W = 3
NR_H    = 3   # grande
LAT_H   = 2   # CAMBIADO: antes 1 (no renderizaba el numero)
START_Y = 10

# Marker - bump a v2 para que reemplace los v1 limpiamente
MARKER  = "auto:wvx_agent_v2"
OLD_MARKERS = ["auto:wvx_agent_v1", "auto:wvx_agent_v2"]  # ambos se limpian

# Umbrales NR (contador de rechazos, NO porcentaje)
NR_THRESHOLDS = [
    {"value": None, "color": "green"},
    {"value": 5,    "color": "yellow"},
    {"value": 15,   "color": "orange"},
    {"value": 30,   "color": "red"},
]

# Umbrales latencia (ms)
LAT_THRESHOLDS = [
    {"value": None, "color": "green"},
    {"value": 151,  "color": "super-light-green"},
    {"value": 401,  "color": "yellow"},
    {"value": 501,  "color": "red"},
]

# ============================================================
session = requests.Session()

def zbx_api(method, params, auth=None):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    if auth:
        payload["auth"] = auth
    r = session.post(ZBX_URL, json=payload, verify=False, timeout=30)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["result"]

def zbx_login():
    return zbx_api("user.login", {"user": ZBX_USER, "password": ZBX_PASS})

def zbx_get_hostid(auth):
    res = zbx_api("host.get", {"filter": {"host": [HOST_NAME]}, "output": ["hostid"]}, auth)
    if not res:
        raise SystemExit(f"Host no encontrado: {HOST_NAME}")
    return res[0]["hostid"]

def zbx_get_agent_items(auth, hostid):
    items = zbx_api("item.get", {"hostids": hostid, "output": ["itemid", "key_", "name"]}, auth)
    agents = {}
    for it in items:
        key = it["key_"]
        m_lat = re.match(r"^agent\.latency\[(\d+)\]$", key)
        m_nr  = re.match(r"^redplus\.agent\.nr\[(\d+)\]$", key)
        if m_lat:
            code = m_lat.group(1)
            agents.setdefault(code, {})["latency_itemid"] = it["itemid"]
            agents[code]["latency_name"] = it["name"]
        elif m_nr:
            code = m_nr.group(1)
            agents.setdefault(code, {})["nr_itemid"] = it["itemid"]
            agents[code]["nr_name"] = it["name"]
    for code, data in agents.items():
        nm = data.get("latency_name") or data.get("nr_name") or ""
        m = re.search(r"(?:Agent\s+\d+\s*-\s*|redplus\.Agent-\d+-)([^-]+?)\s*-", nm)
        data["display_name"] = m.group(1).strip() if m else code
    return agents

def grafana_request(method, path, **kwargs):
    url = f"{GRAFANA_URL.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json"}
    auth = None
    if GRAFANA_TOKEN:
        headers["Authorization"] = f"Bearer {GRAFANA_TOKEN}"
    else:
        auth = (GRAFANA_USER, GRAFANA_PASS)
    r = requests.request(method, url, headers=headers, auth=auth, timeout=30, verify=False, **kwargs)
    if r.status_code >= 400:
        print(f"[ERR] Grafana {method} {path} -> {r.status_code}: {r.text[:500]}")
        r.raise_for_status()
    return r.json()

def grafana_get_dashboard(uid):
    return grafana_request("GET", f"/api/dashboards/uid/{uid}")

def grafana_save_dashboard(dashboard, message):
    payload = {"dashboard": dashboard, "overwrite": True, "message": message}
    return grafana_request("POST", "/api/dashboards/db", json=payload)

def make_target(itemid, ref_id="A"):
    return {
        "refId": ref_id,
        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": GRAFANA_DS_UID},
        "queryType": "3",
        "itemids": str(itemid),
        "resultFormat": "time_series",
        "application": {"filter": ""},
        "group":       {"filter": ""},
        "host":        {"filter": ""},
        "item":        {"filter": ""},
        "itemTag":     {"filter": ""},
        "macro":       {"filter": ""},
        "proxy":       {"filter": ""},
        "tags":        {"filter": ""},
        "trigger":     {"filter": ""},
        "textFilter":  "",
        "countTriggersBy": "",
        "evaltype": "0",
        "functions": [],
        "options": {
            "count": False,
            "disableDataAlignment": False,
            "showDisabledItems": False,
            "skipEmptyValues": False,
            "useTrends": "default",
            "useZabbixValueMapping": False
        },
        "table": {"skipEmptyValues": False},
        "schema": 12
    }

def make_nr_panel(panel_id, code, name, itemid, x, y):
    return {
        "id": panel_id,
        "type": "stat",
        "title": f"{code} - {name} - NR",
        "description": MARKER,
        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": GRAFANA_DS_UID},
        "gridPos": {"x": x, "y": y, "w": PANEL_W, "h": NR_H},
        "targets": [make_target(itemid, "A")],
        "options": {
            "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
            "orientation": "horizontal",
            "textMode":    "value",
            "wideLayout":  True,
            "colorMode":   "value",
            "graphMode":   "none",
            "justifyMode": "auto",
            "showPercentChange": True,
            "percentChangeColorMode": "standard",
            "text": {                    # <-- AQUI
                "valueSize": 25,         # tamaño del numero (px)
            }
        },
        "fieldConfig": {
            "defaults": {
                "unit": "short",  # CAMBIADO: antes "percent" — NR es contador
                "mappings": [],
                "thresholds": {"mode": "absolute", "steps": NR_THRESHOLDS},
                "color": {"mode": "thresholds"}
            },
            "overrides": []
        }
    }

def make_lat_panel(panel_id, code, itemid, x, y):
    return {
        "id": panel_id,
        "type": "stat",
        "title": f"Latency {code}",
        "description": MARKER,
        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": GRAFANA_DS_UID},
        "gridPos": {"x": x, "y": y, "w": PANEL_W, "h": LAT_H},
        "targets": [make_target(itemid, "A")],
        "options": {
            "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
            "orientation": "horizontal",
            "textMode":    "value",
            "wideLayout":  True,
            "colorMode":   "value",
            "graphMode":   "none",
            "justifyMode": "auto",
            "showPercentChange": False,
            "percentChangeColorMode": "standard",
            "text": {                    # <-- AQUI
                "valueSize": 15,         # tamaño del numero (px)
            }
        },
        "fieldConfig": {
            "defaults": {
                "unit": "ms",
                "mappings": [],
                "thresholds": {"mode": "absolute", "steps": LAT_THRESHOLDS},
                "color": {"mode": "thresholds"}
            },
            "overrides": []
        }
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("[1/5] Zabbix login...")
    auth = zbx_login()
    hostid = zbx_get_hostid(auth)
    print(f"      Host ID: {hostid}")

    print("[2/5] Recolectando items...")
    agents = zbx_get_agent_items(auth, hostid)
    complete = {c: d for c, d in agents.items()
                if "latency_itemid" in d and "nr_itemid" in d}
    print(f"      Agentes completos: {len(complete)}")

    print(f"[3/5] Generando {len(complete) * 2} paneles...")
    new_panels = []
    next_id = 1000
    for idx, code in enumerate(sorted(complete.keys(), key=int)):
        data = complete[code]
        name = data["display_name"]
        col = idx % PANELS_PER_ROW
        row = idx // PANELS_PER_ROW
        x = col * PANEL_W
        y = START_Y + row * (NR_H + LAT_H)
        new_panels.append(make_nr_panel(next_id,     code, name, data["nr_itemid"],      x, y))
        new_panels.append(make_lat_panel(next_id + 1, code,       data["latency_itemid"], x, y + NR_H))
        next_id += 2

    if args.dry_run:
        print(f"\n[DRY-RUN] Total paneles: {len(new_panels)}")
        return

    print("[4/5] Cargando dashboard...")
    dash_resp = grafana_get_dashboard(DASHBOARD_UID)
    dashboard = dash_resp["dashboard"]
    existing = dashboard.get("panels", [])
    # Limpia v1 Y v2 (por si re-corres)
    kept = [p for p in existing if p.get("description") not in OLD_MARKERS]
    removed = len(existing) - len(kept)
    print(f"      Existentes: {len(existing)} | Conservados: {len(kept)} | Removidos auto: {removed}")

    dashboard["panels"] = kept + new_panels
    print(f"[5/5] Guardando ({len(dashboard['panels'])} paneles totales)...")
    res = grafana_save_dashboard(dashboard, message=f"v2: {len(complete)} agent panels")
    print(f"      OK ✓ version={res.get('version')}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[FATAL] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
