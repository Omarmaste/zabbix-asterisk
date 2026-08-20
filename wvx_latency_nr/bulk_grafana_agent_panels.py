#!/usr/bin/env python3
"""
v5 - Cambios respecto a v4:
  - Si GRAFANA_DASHBOARD_UID esta vacio/CHANGE_ME (o el UID configurado ya no
    existe en Grafana), el script crea el tablero y su carpeta automaticamente
    (mismo formato que "wvx - npls - Latencia Agentes") y guarda el UID nuevo
    de vuelta en el .env, para que las corridas siguientes (cron nocturno)
    reutilicen el mismo tablero en vez de crear uno nuevo cada vez.
  - Nuevas variables opcionales: GRAFANA_FOLDER_TITLE / GRAFANA_DASHBOARD_TITLE
    (default: derivadas de WOLKVOX_OPERATION si no se definen).

v4 - Cambios respecto a v3:
  - Columna por agente ahora tiene 6 cubos apilados: NR, Latencia, Estado,
    Plataforma (APP/WEB), Conexion (WiFi/Cable) y Version de aplicativo.
  - Estado/Plataforma/Conexion vienen de items numericos + value map de Zabbix
    (ver create_status_items.py) coloreados via mappings a nivel de panel.
  - Version se colorea en rojo si tiene mas de 30 dias de antiguedad (umbral
    recalculado en cada corrida, ya que el cron nocturno regenera el tablero).
  - PANEL_W=4 / PANELS_PER_ROW=6 (antes 3/8) para calzar 6 agentes por fila.

v3 - Cambios respecto a v2:
  - Agrega paneles globales tipo timeseries: Latencia Global + Network Rejection
  - Fix regex key lookup usando WOLKVOX_OPERATION como prefijo
  - START_Y = 18 para dejar espacio a los dos paneles globales (9px c/u)
  - GLOBAL_MARKER para limpiar/regenerar paneles globales independientemente
"""
import argparse
import datetime
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
            _k, _v = _k.strip(), _v.strip()
            if _v[:1] in ('"', "'"):
                _q = _v[0]; _e = _v.find(_q, 1)
                _v = _v[1:_e] if _e != -1 else _v[1:]
            else:
                _v = _v.split('#', 1)[0].strip()
            if _k and _k not in _os.environ:
                _os.environ[_k] = _v
ENV_FILE_PATH = _ef
del _pl, _os, _ef

# ============================================================
# CONFIGURACIÓN — valores desde .env o variables de entorno
# ============================================================

# Zabbix
ZBX_URL   = os.environ.get("ZBX_URL",        "http://68.183.116.34/zabbix/api_jsonrpc.php")
ZBX_USER  = os.environ.get("ZBX_USER",       "Admin")
ZBX_PASS  = os.environ.get("ZBX_PASS",       "CHANGE_ME")
HOST_NAME = os.environ.get("LATENCY_ZBX_HOST", os.environ.get("ZBX_HOST", "ippbx-cloud-issa5-redplus"))
#
WOLKVOX_OPERATION = os.environ.get("WOLKVOX_OPERATION", "unknown_operation")

# Grafana
GRAFANA_URL    = os.environ.get("GRAFANA_URL",           "http://68.183.116.34:3000")
DASHBOARD_UID  = os.environ.get("GRAFANA_DASHBOARD_UID", "CHANGE_ME")
GRAFANA_DS_UID = os.environ.get("GRAFANA_DS_UID",        "CHANGE_ME")

# Si el tablero no existe todavia, se crea con estos titulos (carpeta + dashboard).
# Por defecto se derivan de WOLKVOX_OPERATION; se pueden sobrescribir en .env.
GRAFANA_FOLDER_TITLE    = os.environ.get("GRAFANA_FOLDER_TITLE",    f"wvx - {WOLKVOX_OPERATION}")
GRAFANA_DASHBOARD_TITLE = os.environ.get("GRAFANA_DASHBOARD_TITLE", f"wvx - {WOLKVOX_OPERATION} - Latencia Agentes")

# Autenticación Grafana — opción 1 (token) tiene prioridad sobre usuario+pass
GRAFANA_TOKEN  = os.environ.get("GRAFANA_TOKEN", "")
GRAFANA_USER   = os.environ.get("GRAFANA_USER",  "admin")
GRAFANA_PASS   = os.environ.get("GRAFANA_PASS",  "CHANGE_ME")

GRAFANA_HOST_FILTER  = os.environ.get("GRAFANA_HOST_FILTER",  "Zabbix server")
GRAFANA_GROUP_FILTER = os.environ.get("GRAFANA_GROUP_FILTER", "Zabbix servers")

# El host "Zabbix server" es compartido por TODOS los clientes de este
# servidor (cada operacion Wolkvox distingue sus items solo por prefijo de
# key/nombre) — por eso los paneles globales anclan el filtro de nombre al
# prefijo "[TAG]" que create_latency_items.py/create_nr_items.py ya ponen
# en el "name" del item, para no mezclar agentes de otro cliente.
# DISPLAY_TAG = WOLKVOX_OPERATION sin el prefijo de marca compartida
# "ALOGLOBAL-" (todas las operaciones lo llevan) — debe calcularse IGUAL
# que en create_latency_items.py/create_nr_items.py/create_status_items.py,
# si no el filtro deja de matchear los items reales.
_op_upper = WOLKVOX_OPERATION.upper()
DISPLAY_TAG = _op_upper[len("ALOGLOBAL-"):] if _op_upper.startswith("ALOGLOBAL-") else _op_upper
_OP_TAG = re.escape(DISPLAY_TAG)
LATENCY_NAME_FILTER = rf"/^\[{_OP_TAG}\] Agent .* - .* - Latency$/"
NR_NAME_FILTER       = rf"/^\[{_OP_TAG}\] Agent .* - .* - NR$/"

# Timezone del tablero (paneles + navegacion "Today"): se toma de .env
# (TIMEZONE_DEFAULT), salvo que se quiera un GRAFANA_TIMEZONE especifico
# solo para el tablero.
GRAFANA_TIMEZONE = os.environ.get("GRAFANA_TIMEZONE", os.environ.get("TIMEZONE_DEFAULT", "America/Bogota"))


# Layout (columna por agente: NR, Latencia, Estado, Plataforma, Conexion, Version)
PANELS_PER_ROW = 6   # 24 / PANEL_W
PANEL_W = 4
NR_H    = 3   # alto panel NR individual
LAT_H   = 3   # alto panel latencia individual
TILE_H  = 3   # alto de cada cubo de estado/plataforma/conexion/version
AGENT_COL_H = NR_H + LAT_H + TILE_H * 4  # alto total de la columna de un agente
GLOBAL_H = 9  # alto paneles globales (timeseries)
START_Y = 18  # y inicial por-agente (2 x GLOBAL_H para los paneles globales)

# Markers — identifican paneles autogenerados para reemplazarlos limpiamente
MARKER        = "auto:wvx_agent_v2"
GLOBAL_MARKER = "auto:wvx_global_v1"
OLD_MARKERS   = ["auto:wvx_agent_v1", "auto:wvx_agent_v2"]  # per-agente
ALL_AUTO_MARKERS = OLD_MARKERS + [GLOBAL_MARKER]            # todos

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

# Mapeos de valor->texto+color para los campos codificados (ver create_status_items.py)
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

# Version = fecha YYYYMMDD como entero (crece igual que la fecha, sirve para threshold numerico).
# Se recalcula en cada corrida (cron nocturno) para que el corte de "30 dias" no quede desactualizado.
_VERSION_CUTOFF = int((datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y%m%d"))
VERSION_THRESHOLDS = [
    {"value": None, "color": "red"},
    {"value": _VERSION_CUTOFF, "color": "green"},
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

STATUS_FIELDS = ["latency", "nr", "status", "platform", "connection_type", "version"]

def zbx_get_agent_items(auth, hostid):
    items = zbx_api("item.get", {"hostids": hostid, "output": ["itemid", "key_", "name"]}, auth)
    agents = {}
    fields_re = "|".join(re.escape(f) for f in STATUS_FIELDS)
    pattern = re.compile(rf"^{re.escape(WOLKVOX_OPERATION)}\.agent\.({fields_re})\[(\d+)\]$")
    for it in items:
        m = pattern.match(it["key_"])
        if not m:
            continue
        field, code = m.group(1), m.group(2)
        agents.setdefault(code, {})[f"{field}_itemid"] = it["itemid"]
        agents[code][f"{field}_name"] = it["name"]
    for code, data in agents.items():
        nm = data.get("latency_name") or data.get("nr_name") or ""
        m = re.search(r"(?:Agent\s+\d+\s*-\s*|redplus\.Agent-\d+-)([^-]+?)\s*-", nm)
        data["display_name"] = m.group(1).strip() if m else code
    return agents

def grafana_request(method, path, allow_404=False, **kwargs):
    url = f"{GRAFANA_URL.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json"}
    auth = None
    if GRAFANA_TOKEN:
        headers["Authorization"] = f"Bearer {GRAFANA_TOKEN}"
    else:
        auth = (GRAFANA_USER, GRAFANA_PASS)
    r = requests.request(method, url, headers=headers, auth=auth, timeout=30, verify=False, **kwargs)
    if r.status_code == 404 and allow_404:
        return None
    if r.status_code >= 400:
        print(f"[ERR] Grafana {method} {path} -> {r.status_code}: {r.text[:500]}")
        r.raise_for_status()
    return r.json()

def grafana_get_dashboard(uid, allow_404=False):
    return grafana_request("GET", f"/api/dashboards/uid/{uid}", allow_404=allow_404)

def grafana_save_dashboard(dashboard, message, folder_uid=None):
    payload = {"dashboard": dashboard, "overwrite": True, "message": message}
    if folder_uid:
        payload["folderUid"] = folder_uid
    return grafana_request("POST", "/api/dashboards/db", json=payload)

def _is_placeholder_uid(uid):
    return not uid or uid.strip() in ("", "CHANGE_ME")

def grafana_get_folder_uid_by_title(title):
    folders = grafana_request("GET", "/api/folders")
    for f in folders:
        if f.get("title") == title:
            return f["uid"]
    return None

def grafana_create_folder(title):
    res = grafana_request("POST", "/api/folders", json={"title": title})
    return res["uid"]

def grafana_ensure_folder(title):
    uid = grafana_get_folder_uid_by_title(title)
    if uid:
        print(f"      Carpeta existente: '{title}' ({uid})")
        return uid
    uid = grafana_create_folder(title)
    print(f"      Carpeta creada: '{title}' ({uid})")
    return uid

def grafana_create_dashboard(title, folder_uid):
    dashboard = {
        "id": None,
        "uid": None,
        "title": title,
        "tags": [],
        "timezone": GRAFANA_TIMEZONE,
        "schemaVersion": 42,
        "version": 0,
        "refresh": "",
        "time": {"from": "now/d", "to": "now/d"},
        "panels": [],
    }
    res = grafana_save_dashboard(dashboard, message="Creacion inicial automatica (install_zabbix.sh)", folder_uid=folder_uid)
    return res["uid"]

def persist_dashboard_uid(uid):
    if not ENV_FILE_PATH:
        print(f"      [!] No se encontro .env para guardar GRAFANA_DASHBOARD_UID={uid}; agregalo manualmente.")
        return
    lines = ENV_FILE_PATH.read_text().splitlines(keepends=True)
    pattern = re.compile(r'^\s*GRAFANA_DASHBOARD_UID\s*=')
    new_line = f'GRAFANA_DASHBOARD_UID="{uid}"\n'
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    ENV_FILE_PATH.write_text("".join(lines))
    print(f"      GRAFANA_DASHBOARD_UID={uid} guardado en {ENV_FILE_PATH}")

def grafana_resolve_dashboard_uid():
    """Devuelve un UID de tablero valido, creando tablero+carpeta si hace falta."""
    global DASHBOARD_UID
    if not _is_placeholder_uid(DASHBOARD_UID):
        if grafana_get_dashboard(DASHBOARD_UID, allow_404=True) is not None:
            return DASHBOARD_UID
        print(f"      [!] GRAFANA_DASHBOARD_UID={DASHBOARD_UID} no existe en Grafana; se creara un tablero nuevo.")
    print(f"      Tablero no configurado; creando '{GRAFANA_DASHBOARD_TITLE}' en carpeta '{GRAFANA_FOLDER_TITLE}'...")
    folder_uid = grafana_ensure_folder(GRAFANA_FOLDER_TITLE)
    uid = grafana_create_dashboard(GRAFANA_DASHBOARD_TITLE, folder_uid)
    persist_dashboard_uid(uid)
    DASHBOARD_UID = uid
    return uid

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

def make_global_target(ref_id, item_filter):
    return {
        "refId": ref_id,
        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": GRAFANA_DS_UID},
        "queryType": "0",
        "group":       {"filter": GRAFANA_GROUP_FILTER},
        "host":        {"filter": GRAFANA_HOST_FILTER},
        "application": {"filter": ""},
        "item":        {"filter": item_filter},
        "itemTag":     {"filter": ""},
        "itemids":     "",
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
        "schema": 12,
        "resultFormat": "time_series"
    }

def make_latency_global_panel(panel_id):
    desc = (
        f"{GLOBAL_MARKER}\n"
        "Latencia de agentes (estándar ITU-T G.114):\n"
        "- 0-150ms: Excelente\n"
        "- 150-400ms: Aceptable\n"
        "- 400-500ms: Límite tolerable\n"
        "- Mayor a 500ms: Inaceptable - afecta calidad de llamadas"
    )
    return {
        "id": panel_id,
        "type": "timeseries",
        "title": "Latencia Global Agentes",
        "description": desc,
        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": GRAFANA_DS_UID},
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": GLOBAL_H},
        "targets": [make_global_target("A", LATENCY_NAME_FILTER)],
        "options": {
            "tooltip": {"mode": "single", "sort": "none", "hideZeros": False},
            "legend": {
                "showLegend": True,
                "displayMode": "table",
                "placement": "right",
                "calcs": ["max"]
            }
        },
        "fieldConfig": {
            "defaults": {
                "unit": "ms",
                "decimals": 0,
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "line",
                    "lineInterpolation": "linear",
                    "lineWidth": 1,
                    "fillOpacity": 0,
                    "gradientMode": "none",
                    "spanNulls": False,
                    "showPoints": "auto",
                    "pointSize": 5,
                    "stacking": {"mode": "none", "group": "A"},
                    "axisPlacement": "auto",
                    "axisLabel": "",
                    "axisColorMode": "text",
                    "axisBorderShow": True,
                    "scaleDistribution": {"type": "linear"},
                    "axisCenteredZero": False,
                    "hideFrom": {"tooltip": False, "viz": False, "legend": False},
                    "thresholdsStyle": {"mode": "line+area"}
                },
                "thresholds": {
                    "mode": "absolute",
                    "steps": LAT_THRESHOLDS
                },
                "mappings": []
            },
            "overrides": []
        }
    }

def make_nr_global_panel(panel_id):
    return {
        "id": panel_id,
        "type": "timeseries",
        "title": "Network Rejection Global Agentes",
        "description": GLOBAL_MARKER,
        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": GRAFANA_DS_UID},
        "gridPos": {"x": 0, "y": GLOBAL_H, "w": 24, "h": GLOBAL_H},
        "targets": [make_global_target("A", NR_NAME_FILTER)],
        "options": {
            "tooltip": {"mode": "single", "sort": "none", "hideZeros": False},
            "legend": {
                "showLegend": True,
                "displayMode": "list",
                "placement": "right",
                "calcs": []
            }
        },
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "decimals": 0,
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "line",
                    "lineInterpolation": "linear",
                    "lineWidth": 1,
                    "fillOpacity": 0,
                    "gradientMode": "none",
                    "spanNulls": False,
                    "showPoints": "auto",
                    "pointSize": 5,
                    "stacking": {"mode": "none", "group": "A"},
                    "axisPlacement": "auto",
                    "axisLabel": "",
                    "axisColorMode": "text",
                    "axisBorderShow": False,
                    "scaleDistribution": {"type": "linear"},
                    "axisCenteredZero": False,
                    "hideFrom": {"tooltip": False, "viz": False, "legend": False},
                    "thresholdsStyle": {"mode": "line+area"}
                },
                "thresholds": {
                    "mode": "absolute",
                    "steps": NR_THRESHOLDS
                },
                "mappings": []
            },
            "overrides": []
        }
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
                "valueSize": 35,         # tamaño del numero (px)
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

def make_info_tile(panel_id, title, itemid, x, y, mappings, unit="short", decimals=None, thresholds=None):
    fc = {
        "unit": unit,
        "mappings": mappings,
        "thresholds": {"mode": "absolute", "steps": thresholds or [{"value": 0, "color": "gray"}]},
        "color": {"mode": "thresholds"}
    }
    if decimals is not None:
        fc["decimals"] = decimals
    return {
        "id": panel_id,
        "type": "stat",
        "title": title,
        "description": MARKER,
        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": GRAFANA_DS_UID},
        "gridPos": {"x": x, "y": y, "w": PANEL_W, "h": TILE_H},
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
        "fieldConfig": {"defaults": fc, "overrides": []}
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("[1/6] Zabbix login...")
    auth = zbx_login()
    hostid = zbx_get_hostid(auth)
    print(f"      Host ID: {hostid}")

    print("[2/6] Recolectando items...")
    agents = zbx_get_agent_items(auth, hostid)
    # Solo latencia+NR son obligatorios (compat con agentes sin sync de estado
    # todavia); los 4 cubos de estado se agregan solo si el item existe.
    complete = {c: d for c, d in agents.items()
                if "latency_itemid" in d and "nr_itemid" in d}
    incomplete_status = [c for c, d in complete.items() if "status_itemid" not in d]
    print(f"      Agentes completos: {len(complete)}"
          + (f" ({len(incomplete_status)} sin items de estado: {', '.join(incomplete_status)})" if incomplete_status else ""))

    agent_count = len(complete)
    print(f"[3/6] Generando paneles (2 globales + {agent_count} agentes x 6 cubos)...")
    # Paneles globales (timeseries)
    global_panels = [
        make_latency_global_panel(900),
        make_nr_global_panel(901),
    ]
    # Paneles por agente (stat): NR, Latencia, Estado, Plataforma, Conexion, Version
    agent_panels = []
    next_id = 1000
    for idx, code in enumerate(sorted(complete.keys(), key=int)):
        data = complete[code]
        name = data["display_name"]
        col = idx % PANELS_PER_ROW
        row = idx // PANELS_PER_ROW
        x = col * PANEL_W
        y = START_Y + row * AGENT_COL_H
        agent_panels.append(make_nr_panel(next_id,     code, name, data["nr_itemid"],      x, y))
        agent_panels.append(make_lat_panel(next_id + 1, code,       data["latency_itemid"], x, y + NR_H))
        next_id += 2
        status_tiles = [
            ("Estado",     "status_itemid",          STATUS_MAPPINGS,     "short", None, None),
            ("Plataforma", "platform_itemid",        PLATFORM_MAPPINGS,   "short", None, None),
            ("Conexion",   "connection_type_itemid", CONNECTION_MAPPINGS, "short", None, None),
            ("Version",    "version_itemid",         [],                  "none",  0,    VERSION_THRESHOLDS),
        ]
        for tile_idx, (label, key, mappings, unit, decimals, thresholds) in enumerate(status_tiles):
            if key not in data:
                continue
            tile_y = y + NR_H + LAT_H + TILE_H * tile_idx
            agent_panels.append(make_info_tile(next_id, f"{code} - {name} - {label}", data[key], x, tile_y,
                                                mappings, unit=unit, decimals=decimals, thresholds=thresholds))
            next_id += 1

    new_panels = global_panels + agent_panels

    if args.dry_run:
        print(f"\n[DRY-RUN] Total paneles: {len(new_panels)} (2 globales + {len(agent_panels)} por agente)")
        if _is_placeholder_uid(DASHBOARD_UID):
            print(f"[DRY-RUN] GRAFANA_DASHBOARD_UID no configurado — se crearia "
                  f"'{GRAFANA_DASHBOARD_TITLE}' en carpeta '{GRAFANA_FOLDER_TITLE}'")
        return

    print("[4/6] Resolviendo tablero de Grafana...")
    dashboard_uid = grafana_resolve_dashboard_uid()

    print("[5/6] Cargando dashboard...")
    dash_resp = grafana_get_dashboard(dashboard_uid)
    dashboard = dash_resp["dashboard"]
    folder_uid = dash_resp.get("meta", {}).get("folderUid")
    print(f"      Carpeta actual: {dash_resp.get('meta', {}).get('folderTitle')} ({folder_uid})")
    existing = dashboard.get("panels", [])
    # Limpia todos los paneles autogenerados (globales y por-agente)
    kept = [p for p in existing
            if not any(m in (p.get("description") or "") for m in ALL_AUTO_MARKERS)]
    removed = len(existing) - len(kept)
    print(f"      Existentes: {len(existing)} | Conservados: {len(kept)} | Removidos auto: {removed}")

    if dashboard.get("timezone") != GRAFANA_TIMEZONE:
        print(f"      Timezone: {dashboard.get('timezone') or '(vacio)'} -> {GRAFANA_TIMEZONE}")
        dashboard["timezone"] = GRAFANA_TIMEZONE

    dashboard["panels"] = kept + new_panels
    print(f"[6/6] Guardando ({len(dashboard['panels'])} paneles totales)...")
    res = grafana_save_dashboard(dashboard, message=f"v3: 2 globales + {agent_count} agentes", folder_uid=folder_uid)
    print(f"      OK ✓ version={res.get('version')}")
    print(f"      URL: {GRAFANA_URL.rstrip('/')}/d/{dashboard_uid}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[FATAL] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
