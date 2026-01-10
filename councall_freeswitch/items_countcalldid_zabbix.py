#!/usr/bin/env python3
"""
Script para crear items HTTP agent en Zabbix 6.0.28
Monitorea llamadas entrantes por DID usando FreeSWITCH
Autor: Automatización Zabbix
Fecha: 2026-01-10
"""

import os
import sys
import requests
from typing import List, Dict

# ========= CONFIGURACIÓN =========
ZBX_URL = os.environ.get("ZBX_URL", "http://IP/zabbix/api_jsonrpc.php")
ZBX_USER = os.environ.get("ZBX_USER", "user")
ZBX_PASS = os.environ.get("ZBX_PASS", "pass")
HOST_NAME = os.environ.get("ZBX_HOST", "atenea.kiwano-plus.com")

# URL del API PHP que cuenta llamadas DID
API_URL = os.environ.get("API_URL", "https://IP/zabbix_atenea")

# Configuración de items
ITEM_DELAY = "1m"
ITEM_HISTORY_S = "90d"         # Zabbix 6.x acepta formato de tiempo
ITEM_TRENDS_S = "365d"
ITEM_VALUE_TYPE = 3            # Numeric (unsigned)
ITEM_TYPE = 19                 # HTTP agent
ITEM_UNITS = "calls"
ITEM_TIMEOUT = "3s"

# TLS/SSL
VERIFY_TLS = os.environ.get("ZBX_VERIFY_TLS", "false").lower() == "true"

# ========= CLASES Y FUNCIONES =========

class ZabbixAPI:
    """Cliente para interactuar con Zabbix API"""
    
    def __init__(self, url: str, user: str, password: str, verify_ssl: bool = False):
        self.url = url
        self.user = user
        self.password = password
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.auth_token = None
    
    def call(self, method: str, params: dict) -> dict:
        """Ejecuta una llamada al API de Zabbix"""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1
        }
        
        if self.auth_token:
            payload["auth"] = self.auth_token
        
        try:
            response = self.session.post(
                self.url,
                json=payload,
                verify=self.verify_ssl,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            
            if "error" in data:
                raise RuntimeError(f"Zabbix API Error: {data['error']}")
            
            return data.get("result")
        
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Error de conexión: {e}")
    
    def login(self) -> str:
        """Autentica en Zabbix y obtiene token"""
        result = self.call("user.login", {
            "user": self.user,
            "password": self.password
        })
        self.auth_token = result
        return result
    
    def get_host_id(self, hostname: str) -> str:
        """Obtiene el ID del host por nombre"""
        result = self.call("host.get", {
            "filter": {"host": [hostname]},
            "output": ["hostid", "host", "name"]
        })
        
        if not result:
            result = self.call("host.get", {
                "filter": {"name": [hostname]},
                "output": ["hostid", "host", "name"]
            })
        
        if not result:
            raise RuntimeError(f"Host '{hostname}' no encontrado en Zabbix")
        
        return result[0]["hostid"]
    
    def item_exists(self, hostid: str, key: str) -> bool:
        """Verifica si un item ya existe"""
        result = self.call("item.get", {
            "hostids": hostid,
            "filter": {"key_": key},
            "output": ["itemid"]
        })
        return bool(result)
    
    def create_http_item(self, hostid: str, did: Dict[str, str]) -> dict:
        """Crea un item HTTP agent para monitorear un DID"""
        
        # Construir nombre del item: DID_CUENTA
        item_name = f"{did['did']}_{did['cuenta']}"
        
        # Construir descripción
        description = (
            f"Monitoreo de llamadas entrantes DID\n"
            f"País: {did['pais']}\n"
            f"Cuenta: {did['cuenta']}\n"
            f"IP de Desborde: {did['ip']}"
        )
        
        # Key único del item
        item_key = f"freeswitch.did.calls[{did['did']}]"
        
        # Request body JSON
        request_body = '{\n    "did":"' + did['did'] + '"\n}'
        
        # Parámetros del item
        params = {
            "hostid": hostid,
            "name": item_name,
            "key_": item_key,
            "type": ITEM_TYPE,
            "value_type": ITEM_VALUE_TYPE,
            "units": ITEM_UNITS,
            "delay": ITEM_DELAY,
            "history": ITEM_HISTORY_S,
            "trends": ITEM_TRENDS_S,
            "status": 0,
            "description": description,
            "timeout": ITEM_TIMEOUT,
            "url": API_URL,
            "request_method": 1,              # POST
            "post_type": 0,                   # Raw data
            "posts": request_body,
            # ⚠️ CORRECCIÓN: headers como diccionario en Zabbix 6.x
            "headers": {
                "Content-Type": "application/json"
            },
            "status_codes": "200",
            "follow_redirects": 1,
            "retrieve_mode": 0,
            "output_format": 0,
            "verify_peer": 0,
            "verify_host": 0,
            "ssl_cert_file": "",
            "ssl_key_file": "",
            "ssl_key_password": ""
        }
        
        return self.call("item.create", params)


def read_dids_from_text(text: str) -> List[Dict[str, str]]:
    """Lee DIDs desde texto"""
    dids = []
    lines = text.strip().split('\n')
    
    for line in lines:
        parts = [p.strip() for p in line.split('\t')]
        if len(parts) >= 4:
            dids.append({
                'did': parts[0],
                'pais': parts[1],
                'cuenta': parts[2],
                'ip': parts[3]
            })
    
    return dids


def main():
    """Función principal"""
    
    print("=" * 70)
    print("Script de Creación Automática de Items DID en Zabbix 6.0.28")
    print("=" * 70)
    print()
    
    # Datos de DIDs
    dids_text = """56809001248	CHILE	74143952	142.93.80.145
61291465001	AUSTRALIA	74143952	142.93.80.145
15615769814	United States	45652357	40.117.177.78
17543320713	United States	45652357	40.117.177.78
16894079046	United States	45652357	40.117.177.78
18042981233	United States	45652357	40.117.177.78
13239176127	United States	45652357	40.117.177.78
17136738387	United States	45652357	40.117.177.78
14692972946	United States	45652357	40.117.177.78
17866730623	United States	45652357	40.117.177.78
17724441098	United States	45652357	40.117.177.78
19842467175	United States	45652357	40.117.177.78
12817630495	United States	45652357	40.117.177.78
19179638284	United States	45652357	40.117.177.78
19295905906	United States	45652357	40.117.177.78
17867961462	COLOMBIA	45652357	40.117.177.78
14692423076	United States	45652357	40.117.177.78
5745906300	COLOMBIA	3091833147	40.117.177.78"""
    
    try:
        # 1. Conectar a Zabbix
        print(f"[1/4] Conectando a Zabbix: {ZBX_URL}")
        zapi = ZabbixAPI(ZBX_URL, ZBX_USER, ZBX_PASS, VERIFY_TLS)
        zapi.login()
        print("      ✓ Autenticación exitosa\n")
        
        # 2. Obtener ID del host
        print(f"[2/4] Buscando host: {HOST_NAME}")
        hostid = zapi.get_host_id(HOST_NAME)
        print(f"      ✓ Host encontrado (ID: {hostid})\n")
        
        # 3. Cargar DIDs
        print("[3/4] Cargando lista de DIDs")
        dids = read_dids_from_text(dids_text)
        print(f"      ✓ {len(dids)} DIDs cargados\n")
        
        # 4. Crear items
        print("[4/4] Creando items en Zabbix")
        print("-" * 70)
        
        created = 0
        skipped = 0
        errors = 0
        
        for idx, did in enumerate(dids, 1):
            item_key = f"freeswitch.did.calls[{did['did']}]"
            
            try:
                if zapi.item_exists(hostid, item_key):
                    print(f"[{idx:3d}/{len(dids)}] SKIP: {did['did']}_{did['cuenta']} (ya existe)")
                    skipped += 1
                    continue
                
                result = zapi.create_http_item(hostid, did)
                item_id = result['itemids'][0]
                print(f"[{idx:3d}/{len(dids)}] OK:   {did['did']}_{did['cuenta']} → Item ID: {item_id}")
                created += 1
                
            except Exception as e:
                print(f"[{idx:3d}/{len(dids)}] ERROR: {did['did']}_{did['cuenta']} → {e}")
                errors += 1
        
        # 5. Resumen
        print("-" * 70)
        print("\n" + "=" * 70)
        print("RESUMEN DE EJECUCIÓN")
        print("=" * 70)
        print(f"Items creados:     {created}")
        print(f"Items existentes:  {skipped}")
        print(f"Errores:           {errors}")
        print(f"Total procesados:  {len(dids)}")
        print("=" * 70)
        
        if errors > 0:
            sys.exit(1)
        
    except Exception as e:
        print(f"\n❌ ERROR CRÍTICO: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
