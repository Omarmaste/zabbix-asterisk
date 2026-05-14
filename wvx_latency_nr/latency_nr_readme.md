# Wolkvox Agent Monitoring — Zabbix + Grafana

Sistema de monitoreo automatizado para agentes Wolkvox. Recolecta métricas de latencia y network rejection desde la API de Wolkvox, las envía a Zabbix mediante items trapper, y genera paneles visuales en Grafana — todo de forma automática y autoescalable cuando se agregan nuevos agentes a la operación.

---

## Tabla de contenidos

- [Arquitectura](#arquitectura)
- [Estructura del repositorio](#estructura-del-repositorio)
- [Requisitos previos](#requisitos-previos)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Uso manual](#uso-manual)
- [Automatización (cron)](#automatización-cron)
- [¿Qué pasa cuando llega un agente nuevo?](#qué-pasa-cuando-llega-un-agente-nuevo)
- [Troubleshooting](#troubleshooting)

---

## Arquitectura

```
┌─────────────────┐
│  Wolkvox API    │  (real_time.php?api=latency)
└────────┬────────┘
         │ HTTPS + token
         ▼
┌─────────────────────────────────────────────────┐
│  Servidor IPPBX (cron + scripts)                │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  CADA MINUTO (poller)                    │   │
│  │  send_latency_data.sh → zabbix_sender    │   │
│  │  send_nr_data.sh      → zabbix_sender    │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  CADA 24 HORAS (01:00 AM)                │   │
│  │  sync_agents.sh                          │   │
│  │   ├─ create_latency_items.py             │   │
│  │   ├─ create_nr_items.py                  │   │
│  │   └─ bulk_grafana_agent_panels.py        │   │
│  └──────────────────────────────────────────┘   │
└──────────┬──────────────────┬───────────────────┘
           │                  │
           ▼                  ▼
   ┌──────────────┐    ┌──────────────┐
   │   Zabbix     │    │   Grafana    │
   │  (trapper    │◄───┤  (dashboards │
   │   items)     │    │   + paneles) │
   └──────────────┘    └──────────────┘
```

**Flujo en palabras:**

1. Cada minuto, los scripts `send_*.sh` consultan la API de Wolkvox y envían las métricas a Zabbix vía `zabbix_sender` (solo los valores que cambiaron, para optimizar)
2. Cada 24 horas (a la 01:00 AM), `sync_agents.sh`:
   - Crea items en Zabbix para los agentes nuevos que detecte
   - Regenera los paneles de Grafana para incluirlos
3. Operación nunca tiene que hacer nada manual cuando entra un agente nuevo

---

## Estructura del repositorio

| Archivo | Propósito |
|---|---|
| `create_latency_items.py` | Crea/actualiza items trapper en Zabbix para latencia (`agent.latency[CODIGO]`) |
| `create_nr_items.py` | Crea/actualiza items trapper en Zabbix para network rejection (`redplus.agent.nr[CODIGO]`) |
| `send_latency_data.sh` | Poller: consulta Wolkvox API, envía latencia a Zabbix |
| `send_nr_data.sh` | Poller: consulta Wolkvox API, envía NR a Zabbix |
| `sync_agents.sh` | Orquestador diario: encadena los 3 scripts de sincronización |
| `bulk_grafana_agent_panels.py` | Regenera paneles del dashboard de Grafana (idempotente) |

---

## Requisitos previos

### Software en el servidor

- Linux (probado en CentOS/RHEL 7+ y Ubuntu 20+)
- Python 3.6+
- `zabbix_sender` (paquete `zabbix-sender` o `zabbix-agent`)
- `jq` (para parsear JSON en bash)
- `curl`

```bash
# RHEL/CentOS
sudo yum install python3 zabbix-sender jq curl

# Ubuntu/Debian
sudo apt install python3 zabbix-sender jq curl

# Python deps
pip3 install requests urllib3
```

### Servicios externos

- **Zabbix Server** accesible vía puerto API (80/443) y trapper (10051)
- **Grafana** con el plugin `alexanderzobnin-zabbix-datasource` instalado y configurado
- **Wolkvox** con token API válido

### Credenciales necesarias

| Servicio | Qué necesitas |
|---|---|
| Zabbix | URL, usuario, password de un usuario con rol Admin |
| Wolkvox | `wolkvox_server` (ej. `0025`), `wolkvox-token` |
| Grafana | URL, UID del dashboard, service account token (o user+pass) |

---

## Instalación

### 1. Clonar el repositorio en el servidor

```bash
sudo mkdir -p /etc/zabbix/scripts
cd /etc/zabbix/scripts
sudo git clone https://github.com/TU_USUARIO/TU_REPO.git wvx_latency_agent
cd wvx_latency_agent
```

### 2. Permisos

```bash
sudo chmod +x *.sh *.py
sudo chown -R zabbix:zabbix /etc/zabbix/scripts/wvx_latency_agent
sudo mkdir -p /var/log/zabbix
sudo chown zabbix:zabbix /var/log/zabbix
```

### 3. Verificar Zabbix host

El host objetivo debe existir en Zabbix antes de ejecutar nada. Por defecto el sistema espera el host:

```
ippbx-cloud-issa5-redplus
```

Si tu host se llama diferente, ajusta la variable `HOST_NAME` en los scripts `.py` y `ZBX_HOST` en los `.sh`.

---

## Configuración

Cada script tiene un bloque de configuración al inicio. Antes de ejecutar, **edita las siguientes variables** según tu entorno:

### Variables comunes (en TODOS los scripts)

```python
# Zabbix
ZBX_URL   = "http://TU_ZABBIX/zabbix/api_jsonrpc.php"
ZBX_USER  = "tu_user"
ZBX_PASS  = "tu_password"
HOST_NAME = "ippbx-cloud-issa5-redplus"

# Wolkvox
WOLKVOX_SERVER = "0025"
WOLKVOX_TOKEN  = "tu_token_aqui"
```

### Variables específicas de Grafana (en `bulk_grafana_agent_panels.py`)

```python
GRAFANA_URL    = "https://tu-grafana.com"
DASHBOARD_UID  = "abc123xyz"          # se obtiene de la URL del dashboard
GRAFANA_DS_UID = ".........y5fkd"     # UID del datasource Zabbix en Grafana

# Autenticación: usa UNA opción
GRAFANA_TOKEN  = "glsa_..."   # opción A (recomendado): service account token
GRAFANA_USER   = "user"      # opción B: basic auth
GRAFANA_PASS   = "pass"
```

#### Cómo obtener `DASHBOARD_UID`

De la URL del dashboard en Grafana:
```
https://grafana.com/d/abc123xyz/mi-dashboard
                     ^^^^^^^^^ ESE es el UID
```

#### Cómo crear un service account token en Grafana

1. **Administration** → **Users and access** → **Service accounts**
2. **Add service account** → nombre + rol **Editor**
3. Dentro del service account → **Add service account token**
4. **Generate token** → copiar el token (empieza con `glsa_`, solo se muestra una vez)

#### Cómo obtener `GRAFANA_DS_UID`

En Grafana → Connections → Data sources → click en tu datasource Zabbix → en la URL aparece el UID:
```
/connections/datasources/edit/be...ykd
                              ^^^^^^^^^^^^^^
```

---

## Uso manual

### Paso 1 — Sincronizar items en Zabbix

```bash
cd /etc/zabbix/scripts/wvx_latency_agent
python3 create_latency_items.py
python3 create_nr_items.py
```

Salida esperada:
```
[1/3] Autenticando en Zabbix...
  OK - Host ID: 10618
[2/3] Obteniendo agentes de Wolkvox...
  OK - 51 agentes encontrados
[3/3] Creando/actualizando items...
Total: 51 | Nuevos: 0 | Actualizados: 51
```

### Paso 2 — Probar envío de datos

```bash
bash send_latency_data.sh
bash send_nr_data.sh
```

Salida esperada:
```
[OK] Agentes obtenidos
  ✓ Agent 12004: 215 -> 230 ms
  ...
[INFO] Enviando 5 items...
Response from "X.X.X.X:10051": "processed: 5; failed: 0; total: 5"
[RESULT] processed=5 failed=0
```

> **Si todos los agentes muestran `N/A -> X`**: es la primera ejecución, el state file está vacío. Es normal.
> **Si ves `failed > 0`**: ejecuta primero `create_*items.py` para crear los items faltantes.

### Paso 3 — Generar paneles en Grafana

```bash
# Prueba primero en dry-run (no toca Grafana)
python3 bulk_grafana_agent_panels.py --dry-run

# Si todo se ve bien, ejecuta de verdad
python3 bulk_grafana_agent_panels.py
```

Refresca el dashboard en Grafana — verás todos los paneles de agentes creados.

---

## Automatización (cron)

### Pollers cada minuto (envío de métricas)

Edita el crontab del usuario `zabbix` o `root`:

```bash
crontab -e
```

Agrega:

```cron
# Wolkvox -> Zabbix: envío de métricas cada minuto
* * * * * /bin/bash /etc/zabbix/scripts/wvx_latency_agent/send_latency_data.sh >/dev/null 2>&1
* * * * * /bin/bash /etc/zabbix/scripts/wvx_latency_agent/send_nr_data.sh >/dev/null 2>&1

# Sincronización diaria de agentes nuevos (items Zabbix + paneles Grafana)
0 1 * * * /bin/bash /etc/zabbix/scripts/wvx_latency_agent/sync_agents.sh
```

Verifica:

```bash
crontab -l | grep -E "(send_|sync_agents)"
```

### Logs

```bash
# Log del sync diario
tail -100 /var/log/zabbix/sync_agents.log

# Ejecutar el sync manualmente sin esperar a las 01:00
bash /etc/zabbix/scripts/wvx_latency_agent/sync_agents.sh
tail -50 /var/log/zabbix/sync_agents.log
```

---

## ¿Qué pasa cuando llega un agente nuevo?

Supongamos que la operación crea el agente `13050 - JUAN PEREZ` en Wolkvox.

### Día 1, 14:30 — Agente creado en Wolkvox
El agente empieza a hacer llamadas. La API de Wolkvox ahora reporta 52 agentes en vez de 51.

### Cada minuto desde ese momento
Los pollers (`send_*.sh`) detectan el nuevo `agent_id` pero al intentar enviarlo a Zabbix vía `zabbix_sender` recibe `failed: 1` porque el item aún no existe. Esto es esperado y temporal.

### Día 2, 01:00 AM — Sync automático
`sync_agents.sh` se ejecuta vía cron:

1. **`create_latency_items.py`** detecta los 52 agentes en Wolkvox, ve que el item `agent.latency[13050]` no existe en Zabbix, lo **crea**
2. **`create_nr_items.py`** crea `redplus.agent.nr[13050]`
3. **`bulk_grafana_agent_panels.py`** consulta Zabbix (ahora 52 agentes), regenera los paneles del dashboard incluyendo a JUAN PEREZ

### Día 2, 01:01 AM en adelante
Los pollers ya envían las métricas del agente nuevo sin fallos. El supervisor ve a JUAN PEREZ en el dashboard de Grafana al refrescar.

**Cero intervención manual.**

### Idempotencia

Los scripts son seguros para re-ejecutar:
- Items en Zabbix: si existen, los actualiza; si no, los crea
- Paneles en Grafana: el script marca cada panel con un tag interno (`auto:wvx_agent_v2` en el campo `description`). Al re-ejecutar, **borra solo los que tienen ese tag** y vuelve a crearlos — los paneles que hiciste a mano (Latencia Global, gráficas custom, etc.) NO se tocan.

---

## Troubleshooting

### `zabbix_sender` falla con `failed: N`

Significa que `N` items no existen en Zabbix. Ejecuta:

```bash
python3 create_latency_items.py
python3 create_nr_items.py
```

### Grafana muestra "No data" en varios paneles

Esos agentes están en Zabbix pero todavía no han recibido el primer valor de `zabbix_sender`. Para forzarlo:

```bash
# Borra los state files (solo una vez)
rm -f /etc/zabbix/scripts/wvx_latency_agent/agent_*_state.json

# Re-ejecuta para enviar TODOS los valores actuales
bash send_latency_data.sh
bash send_nr_data.sh
```

### El conteo de agentes no cuadra con el portal de Wolkvox

La API `?api=latency` solo devuelve agentes con sesión activa. Agentes deshabilitados, en vacaciones, sin loguear, o nuevos sin estrenar **no aparecen**. Es normal que el portal muestre 53 y la API 51, por ejemplo.

### `bulk_grafana_agent_panels.py` falla con 401/403

Token expirado, mal copiado, o sin permisos. Verifica:
- `GRAFANA_TOKEN` empieza con `glsa_`
- El service account tiene rol **Editor** (no Viewer)
- O usa basic auth (`GRAFANA_USER`/`GRAFANA_PASS`) si no quieres lidiar con tokens

### Los paneles autogenerados desaparecen pero los manuales no

Eso es lo esperado: el script reconoce sus propios paneles por el `description: auto:wvx_agent_v2` y los reemplaza limpiamente. Los manuales no tienen ese marker, así que no los toca.

### Cambié el script y los paneles viejos siguen ahí

Si subiste de versión el `MARKER` (ej. de `v1` a `v2`), agrega el viejo a `OLD_MARKERS`:

```python
OLD_MARKERS = ["auto:wvx_agent_v1", "auto:wvx_agent_v2", "auto:wvx_agent_v3"]
```

Así una sola ejecución limpia todos los autogenerados de cualquier versión previa.

### Ver agentes actuales en el state

```bash
cat /etc/zabbix/scripts/wvx_latency_agent/agent_nr_state.json | jq 'length'
cat /etc/zabbix/scripts/wvx_latency_agent/agent_latency_state.json | jq 'length'
```

---

## Variables de configuración importantes

| Variable | Archivo | Valor por defecto | Descripción |
|---|---|---|---|
| `HOST_NAME` | `.py` | `ippbx-cloud-issa5-redplus` | Host de Zabbix donde se crean los items |
| `WOLKVOX_SERVER` | todos | `0025` | ID del servidor Wolkvox |
| `PANELS_PER_ROW` | `bulk_grafana_*.py` | `8` | Cuántos agentes por fila en el dashboard |
| `PANEL_W` | `bulk_grafana_*.py` | `3` | Ancho de cada panel (grid units) |
| `NR_H` / `LAT_H` | `bulk_grafana_*.py` | `3` / `2` | Alto de cada sub-panel |
| `START_Y` | `bulk_grafana_*.py` | `10` | Y inicial (deja espacio para paneles manuales arriba) |
| `MARKER` | `bulk_grafana_*.py` | `auto:wvx_agent_v2` | Tag para identificar paneles autogenerados |

---

## Seguridad

- **No commitees credenciales reales al repo.** Considera usar variables de entorno o un archivo `.env` ignorado en `.gitignore`.
- El service account token de Grafana se puede **revocar** sin afectar otras cuentas si se compromete.
- Los archivos de configuración con tokens deben tener permisos restrictivos:
  ```bash
  chmod 600 *.py *.sh
  ```

---

## Licencia

Uso interno. Modificar según necesidades de la operación.

---

## Mantenimiento

| Tarea | Frecuencia | Cómo |
|---|---|---|
| Verificar logs de sync | Semanal | `tail -200 /var/log/zabbix/sync_agents.log` |
| Verificar cron activo | Mensual | `crontab -l` |
| Actualizar token Grafana | Cuando expire | Service account → New token → editar `bulk_grafana_agent_panels.py` |
| Rotar password Zabbix | Política interna | Editar `ZBX_PASS` en todos los `.py` |

---

**Autor:** Equipo de monitoreo  
**Versión:** 2.0
