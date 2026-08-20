#!/usr/bin/env python3
"""
Dashboard de PRUEBA (no toca el dashboard de produccion adr4gjc).
Solo 4 secciones, un cubo (stat) por agente en cada una, SIN tablas ni
transformaciones -- mismo estilo/patron que los cubos de Latencia/NR que ya
existen y funcionan bien en el dashboard de produccion (itemid explicito,
"lastNotNull", refresco automatico):
  - Estado por Agente
  - Plataforma por Agente (APP/WEB)
  - Conexion por Agente (WiFi/Cable)
  - Version de Aplicativo por Agente

Se genera en la carpeta "wvx - npls" para revisar antes de decidir si
reemplaza al dashboard de produccion.
"""
import datetime
import os
import re
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

ZBX_URL   = os.environ.get("ZBX_URL",        "http://68.183.116.34/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",       "Admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",       "CHANGE_ME")
HOST_NAME = os.environ.get("LATENCY_ZBX_HOST", os.environ.get("ZBX_HOST", "ippbx-cloud-issa5-redplus"))
WOLKVOX_OPERATION = os.environ.get("WOLKVOX_OPERATION", "unknown_operation")

GRAFANA_URL    = os.environ.get("GRAFANA_URL",           "http://68.183.116.34:3000")
GRAFANA_DS_UID = os.environ.get("GRAFANA_DS_UID",        "CHANGE_ME")
GRAFANA_TOKEN  = os.environ.get("GRAFANA_TOKEN", "")
GRAFANA_USER   = os.environ.get("GRAFANA_USER",  "admin")
GRAFANA_PASS   = os.environ.get("GRAFANA_PASS",  "CHANGE_ME")
FOLDER_UID = os.environ.get("GRAFANA_FOLDER_UID", "ffo8egx0ssq9sa")  # wvx - npls
TEST_UID   = os.environ.get("GRAFANA_TEST_DASHBOARD_UID", "wvxnpls-tbltest")

PANELS_PER_ROW = 8
TILE_W = 3
TILE_H = 3
ROW_H  = 1

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

def zbx_get_status_items(auth, hostid):
    """Devuelve {code: {display_name, status_itemid, platform_itemid, connection_type_itemid, version_itemid}}"""
    items = zbx_api("item.get", {"hostids": hostid, "output": ["itemid", "key_", "name"]}, auth)
    agents = {}
    for it in items:
        key = it["key_"]
        m = re.match(rf"^{re.escape(WOLKVOX_OPERATION)}\.agent\.(status|platform|connection_type|version)\[(\d+)\]$", key)
        if not m:
            continue
        field, code = m.group(1), m.group(2)
        agents.setdefault(code, {})[f"{field}_itemid"] = it["itemid"]
        m2 = re.search(r"Agent\s+\d+\s*-\s*([^-]+?)\s*-", it["name"])
        agents[code]["display_name"] = m2.group(1).strip() if m2 else code
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
        print(f"[ERR] Grafana {method} {path} -> {r.status_code}: {r.text[:800]}")
        r.raise_for_status()
    return r.json()

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

def make_row(panel_id, title, y):
    return {"id": panel_id, "type": "row", "title": title, "gridPos": {"x": 0, "y": y, "w": 24, "h": ROW_H}, "collapsed": False, "panels": []}

def make_stat_tile(panel_id, code, name, itemid, x, y, mappings, unit="short", decimals=None, thresholds=None):
    fc = {
        "unit": unit,
        "mappings": mappings,
        "color": {"mode": "thresholds"},
        "thresholds": {"mode": "absolute", "steps": thresholds or [{"color": "gray", "value": 0}]}
    }
    if decimals is not None:
        fc["decimals"] = decimals
    return {
        "id": panel_id,
        "type": "stat",
        "title": f"{code} - {name}",
        "description": "auto:wvx_status_test_v1",
        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": GRAFANA_DS_UID},
        "gridPos": {"x": x, "y": y, "w": TILE_W, "h": TILE_H},
        "targets": [make_target(itemid, "A")],
        "options": {
            "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
            "orientation": "horizontal",
            "textMode":    "value",
            "wideLayout":  True,
            "colorMode":   "background",
            "graphMode":   "none",
            "justifyMode": "center",
            "showPercentChange": False,
            "percentChangeColorMode": "standard",
            "text": {"valueSize": 18}
        },
        "fieldConfig": {
            "defaults": fc,
            "overrides": []
        }
    }

STATUS_MAPPINGS = [{"type": "value", "options": {
    "0": {"text": "Otro/Desconocido", "color": "orange"},
    "1": {"text": "Conectado", "color": "green"},
    "2": {"text": "Desconectado", "color": "red"},
}}]
PLATFORM_MAPPINGS = [{"type": "value", "options": {
    "0": {"text": "Otro/Desconocido", "color": "orange"},
    "1": {"text": "APP", "color": "orange"},
    "2": {"text": "WEB", "color": "green"},
}}]
CONNECTION_MAPPINGS = [{"type": "value", "options": {
    "0": {"text": "Otro/Desconocido", "color": "orange"},
    "1": {"text": "WiFi", "color": "blue"},
    "2": {"text": "Cable/Ethernet", "color": "green"},
}}]
VERSION_MAPPINGS = []  # numero plano (YYYYMMDD), sin mapping de texto

# Version = fecha YYYYMMDD como entero. Si la version es de hace mas de 30 dias -> rojo.
# Como YYYYMMDD crece igual que la fecha, el umbral numerico funciona con thresholds normales.
_VERSION_CUTOFF = int((datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y%m%d"))
VERSION_THRESHOLDS = [
    {"color": "red", "value": None},
    {"color": "green", "value": _VERSION_CUTOFF},
]

SECTIONS = [
    ("Estado por Agente",                "status_itemid",          STATUS_MAPPINGS,    "short", None, None),
    ("Plataforma por Agente (APP/WEB)",  "platform_itemid",        PLATFORM_MAPPINGS,  "short", None, None),
    ("Conexion por Agente (WiFi/Cable)", "connection_type_itemid", CONNECTION_MAPPINGS,"short", None, None),
    ("Version de Aplicativo por Agente", "version_itemid",         VERSION_MAPPINGS,   "none", 0, VERSION_THRESHOLDS),
]

def main():
    print("[1/3] Zabbix login...")
    auth = zbx_login()
    hostid = zbx_get_hostid(auth)

    print("[2/3] Recolectando items de estado/conexion...")
    agents = zbx_get_status_items(auth, hostid)
    complete = {c: d for c, d in agents.items() if all(k in d for k in
                ("status_itemid", "platform_itemid", "connection_type_itemid", "version_itemid"))}
    print(f"      Agentes completos: {len(complete)}")
    codes_sorted = sorted(complete.keys(), key=int)

    print("[3/3] Construyendo dashboard...")
    panels = []
    next_id = 3000
    y = 0
    for title, field_key, mappings, unit, decimals, thresholds in SECTIONS:
        panels.append(make_row(next_id, title, y))
        next_id += 1
        y += ROW_H
        for idx, code in enumerate(codes_sorted):
            data = complete[code]
            col = idx % PANELS_PER_ROW
            row = idx // PANELS_PER_ROW
            x = col * TILE_W
            tile_y = y + row * TILE_H
            panels.append(make_stat_tile(next_id, code, data["display_name"], data[field_key], x, tile_y,
                                          mappings, unit=unit, decimals=decimals, thresholds=thresholds))
            next_id += 1
        rows_used = (len(codes_sorted) - 1) // PANELS_PER_ROW + 1 if codes_sorted else 0
        y += rows_used * TILE_H

    dashboard = {
        "uid": TEST_UID,
        "title": "TEST - wvx - npls - Estado y Conexion Agentes",
        "tags": ["wvx", "npls", "test"],
        "timezone": "America/Bogota",
        "editable": True,
        "panels": panels,
        "time": {"from": "now-1h", "to": "now"},
        "refresh": "30s",
        "schemaVersion": 39
    }

    payload = {"dashboard": dashboard, "overwrite": True, "message": "test cubos v1 (sin tablas)", "folderUid": FOLDER_UID}
    res = grafana_request("POST", "/api/dashboards/db", json=payload)
    print(f"      OK -> uid={res.get('uid')} url={GRAFANA_URL}{res.get('url')}")

if __name__ == "__main__":
    main()
