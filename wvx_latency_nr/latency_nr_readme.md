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
- [Notas de campo — instalación real (NueveOnce)](#notas-de-campo--instalación-real-nueveonce)

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
│  │  POLLER (frecuencia configurable)        │   │
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

1. Cada ciclo del poller (ej. cada minuto o cada 30 minutos, según se configure en cron), los scripts `send_*.sh` consultan la API de Wolkvox y envían las métricas a Zabbix vía `zabbix_sender` (solo los valores que cambiaron, para optimizar)
2. Cada 24 horas (a la 01:00 AM), `sync_agents.sh`:
   - Crea items en Zabbix para los agentes nuevos que detecte
   - Regenera los paneles de Grafana para incluirlos
3. Operación nunca tiene que hacer nada manual cuando entra un agente nuevo

> **Nota sobre la frecuencia del poller:** el intervalo de envío de métricas (`send_latency_data.sh` / `send_nr_data.sh`) es ajustable libremente en el crontab según la necesidad del cliente. En la instalación de NueveOnce se configuró en **cada 30 minutos** (`*/30 * * * *`) en vez de cada minuto.

---

## Estructura del repositorio

| Archivo | Propósito |
|---|---|
| `create_latency_items.py` | Crea/actualiza items trapper en Zabbix para latencia (`{OPERACION}.agent.latency[CODIGO]`) |
| `create_nr_items.py` | Crea/actualiza items trapper en Zabbix para network rejection (`{OPERACION}.agent.nr[CODIGO]`) |
| `send_latency_data.sh` | Poller: consulta Wolkvox API, envía latencia a Zabbix |
| `send_nr_data.sh` | Poller: consulta Wolkvox API, envía NR a Zabbix |
| `sync_agents.sh` | Orquestador diario: encadena los 3 scripts de sincronización |
| `bulk_grafana_agent_panels.py` | Regenera paneles del dashboard de Grafana (idempotente) |
| `.env` (raíz del proyecto, un nivel arriba de `wvx_latency_nr/`) | Variables de entorno reales del cliente (host Zabbix, token Wolkvox, UIDs de Grafana, etc.) |

> Los scripts cargan automáticamente el `.env` ubicado en la **raíz del proyecto** (`/etc/zabbix/scripts/wvx_latency_agent/.env`), no dentro de la subcarpeta `wvx_latency_nr/`. Si no existe, caen a los valores por defecto/placeholder definidos en cada script.

---

## Requisitos previos

### Software en el servidor

- Linux (probado en CentOS/RHEL 7+, Ubuntu 20+ y **Debian 12 / Bookworm**)
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

> ⚠️ **Debian 12 / Bookworm — dos advertencias importantes antes de correr lo anterior:**
>
> 1. **`apt install zabbix-sender` puede desinstalar `zabbix-agent`.** Si `zabbix-sender` se resuelve desde `bookworm-backports` (versión 7.x) mientras `zabbix-agent` instalado es de `bookworm` estable (versión 6.x), apt detecta un conflicto entre versiones de Zabbix y **remueve el agente automáticamente** para poder instalar el sender — sin preguntar de forma explícita, solo aparece en el listado `The following packages will be REMOVED:` dentro del prompt `[Y/n]`. **Lee siempre esa sección antes de confirmar.** Si ya pasó, reinstala fijando la misma línea de versión para ambos paquetes:
>    ```bash
>    apt-cache policy zabbix-agent zabbix-sender
>    apt install zabbix-agent zabbix-sender=<version_de_bookworm_estable>
>    systemctl enable --now zabbix-agent
>    ```
> 2. **`pip3 install` falla con "externally-managed-environment".** Debian 12 bloquea `pip` por defecto (PEP 668). Opciones:
>    ```bash
>    # Opción recomendada para este script (paquetes del sistema)
>    apt install python3-requests python3-urllib3
>
>    # Alternativa: forzar pip
>    apt install python3-pip
>    pip3 install requests urllib3 --break-system-packages
>    ```

### Servicios externos

- **Zabbix Server** accesible vía puerto API (80/443) y trapper (10051)
- **Grafana** con el plugin `alexanderzobnin-zabbix-datasource` instalado y configurado
- **Wolkvox** con token API válido

### Credenciales necesarias

| Servicio | Qué necesitas |
|---|---|
| Zabbix | URL, usuario, password de un usuario con rol Admin |
| Wolkvox | `wolkvox_server` (ej. `0025`), `wolkvox-token`, `WOLKVOX_OPERATION` |
| Grafana | URL, UID del dashboard, UID del datasource, service account token (o user+pass) |

---

## Instalación

### 1. Clonar el repositorio en el servidor

```bash
sudo mkdir -p /etc/zabbix/scripts
cd /etc/zabbix/scripts
sudo git clone https://github.com/TU_USUARIO/TU_REPO.git wvx_latency_agent
cd wvx_latency_agent
```

> El repo clona **todo el proyecto** (incluye `ast_pjsip`, `ast_sip`, `ast_fail2ban`, `wvx_auditlog`, etc.). Los archivos de este módulo específico (latencia + NR de agentes) están dentro de la subcarpeta `wvx_latency_nr/`.

### 2. Permisos

```bash
sudo chmod +x wvx_latency_nr/*.sh
sudo chown -R zabbix:zabbix /etc/zabbix/scripts/wvx_latency_agent
sudo mkdir -p /var/log/zabbix
sudo chown zabbix:zabbix /var/log/zabbix
```

> Verifica que el usuario `zabbix` exista en el sistema antes del `chown` (`id zabbix`), especialmente si acabas de reinstalar el agente por el conflicto de paquetes mencionado arriba.

### 3. Verificar Zabbix host

El host objetivo debe existir en Zabbix antes de ejecutar nada. Por defecto el sistema espera el host:

```
ippbx-cloud-issa5-redplus
```

Si tu host se llama diferente, ajusta la variable `LATENCY_ZBX_HOST` (o `ZBX_HOST`) en el `.env`.

---

## Configuración

Toda la configuración real del cliente va en un archivo **`.env`** en la raíz del proyecto (`/etc/zabbix/scripts/wvx_latency_agent/.env`), **no** se edita directamente dentro de cada script:

```bash
# Zabbix
ZBX_URL=http://TU_ZABBIX/zabbix/api_jsonrpc.php
ZBX_USER=tu_user
ZBX_PASS=tu_password
LATENCY_ZBX_HOST=nombre_exacto_del_host_en_zabbix

# Wolkvox
WOLKVOX_SERVER=0025
WOLKVOX_TOKEN=tu_token_aqui
WOLKVOX_OPERATION=nombre_operacion

# Grafana
GRAFANA_URL=https://tu-grafana.com
GRAFANA_DASHBOARD_UID=abc123xyz
GRAFANA_DS_UID=........y5fkd
GRAFANA_TOKEN=glsa_...          # opción A (recomendado)
# GRAFANA_USER=user              # opción B
# GRAFANA_PASS=pass
```

#### Cómo obtener `GRAFANA_DASHBOARD_UID`

De la URL del dashboard en Grafana:
```
https://grafana.com/d/abc123xyz/mi-dashboard
                     ^^^^^^^^^ ESE es el UID
```
No confundir con la URL de la **carpeta** (`/dashboards/f/...`) — el UID del dashboard sale en la URL cuando entras al dashboard en sí (`/d/...`), no en la vista de carpeta.

#### Cómo obtener `GRAFANA_DS_UID`

En Grafana → **Connections** → **Data sources** → click en tu datasource Zabbix → en la URL aparece el UID:
```
/connections/datasources/edit/be...ykd
                              ^^^^^^^^^^^^^^
```

#### Cómo crear un service account token en Grafana

1. **Administration** → **Users and access** → **Service accounts**
2. **Add service account** → nombre + rol **Editor**
3. Dentro del service account → **Add service account token**
4. **Generate token** → copiar el token (empieza con `glsa_`, solo se muestra una vez)

---

## Uso manual

### Paso 1 — Sincronizar items en Zabbix

```bash
cd /etc/zabbix/scripts/wvx_latency_agent/wvx_latency_nr
python3 create_latency_items.py
python3 create_nr_items.py
```

> ⚠️ **Ejecuta SIEMPRE los dos scripts juntos**, nunca solo uno. `bulk_grafana_agent_panels.py` (Paso 3) solo genera paneles por agente cuando este tiene **ambos** items (`latency_itemid` **y** `nr_itemid`) — si solo corriste `create_latency_items.py`, el conteo de "Agentes completos" saldrá en `0` y no se creará ningún panel individual, aunque el panel global de latencia sí muestre datos (usa un filtro por nombre, no depende de "completitud").

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

### Pollers (envío de métricas)

Edita el crontab del usuario `zabbix` o `root`:

```bash
crontab -e
```

Ejemplo con envío **cada minuto**:

```cron
* * * * * /bin/bash /etc/zabbix/scripts/wvx_latency_agent/wvx_latency_nr/send_latency_data.sh >/dev/null 2>&1
* * * * * /bin/bash /etc/zabbix/scripts/wvx_latency_agent/wvx_latency_nr/send_nr_data.sh >/dev/null 2>&1
```

Ejemplo con envío **cada 30 minutos** (configuración usada en NueveOnce):

```cron
*/30 * * * * /etc/zabbix/scripts/wvx_latency_agent/wvx_latency_nr/send_latency_data.sh >> /var/log/zabbix/latency_agent.log 2>&1
*/30 * * * * /etc/zabbix/scripts/wvx_latency_agent/wvx_latency_nr/send_nr_data.sh >> /var/log/zabbix/nr_agent.log 2>&1
```

Sincronización diaria de agentes nuevos (items Zabbix + paneles Grafana):

```cron
0 1 * * * /bin/bash /etc/zabbix/scripts/wvx_latency_agent/sync_agents.sh
```

Verifica:

```bash
crontab -l
```

### Logs

```bash
# Log del sync diario
tail -100 /var/log/zabbix/sync_agents.log

# Logs de los pollers (si se redirigieron a archivo)
tail -100 /var/log/zabbix/latency_agent.log
tail -100 /var/log/zabbix/nr_agent.log

# Ejecutar el sync manualmente sin esperar a las 01:00
bash /etc/zabbix/scripts/wvx_latency_agent/sync_agents.sh
tail -50 /var/log/zabbix/sync_agents.log
```

---

## ¿Qué pasa cuando llega un agente nuevo?

Supongamos que la operación crea el agente `13050 - JUAN PEREZ` en Wolkvox.

### Día 1, 14:30 — Agente creado en Wolkvox
El agente empieza a hacer llamadas. La API de Wolkvox ahora reporta 52 agentes en vez de 51.

### Cada ciclo del poller desde ese momento
Los pollers (`send_*.sh`) detectan el nuevo `agent_id` pero al intentar enviarlo a Zabbix vía `zabbix_sender` recibe `failed: 1` porque el item aún no existe. Esto es esperado y temporal.

### Día 2, 01:00 AM — Sync automático
`sync_agents.sh` se ejecuta vía cron:

1. **`create_latency_items.py`** detecta los 52 agentes en Wolkvox, ve que el item `agent.latency[13050]` no existe en Zabbix, lo **crea**
2. **`create_nr_items.py`** crea `agent.nr[13050]`
3. **`bulk_grafana_agent_panels.py`** consulta Zabbix (ahora 52 agentes completos), regenera los paneles del dashboard incluyendo a JUAN PEREZ

### Día 2, 01:01 AM en adelante
Los pollers ya envían las métricas del agente nuevo sin fallos. El supervisor ve a JUAN PEREZ en el dashboard de Grafana al refrescar.

**Cero intervención manual.**

### Idempotencia

Los scripts son seguros para re-ejecutar:
- Items en Zabbix: si existen, los actualiza; si no, los crea
- Paneles en Grafana: el script marca cada panel con un tag interno (`auto:wvx_agent_v2` / `auto:wvx_global_v1` en el campo `description`). Al re-ejecutar, **borra solo los que tienen ese tag** y vuelve a crearlos — los paneles que hiciste a mano NO se tocan.

---

## Troubleshooting

### `zabbix_sender` falla con `failed: N`

Significa que `N` items no existen en Zabbix. Ejecuta:

```bash
python3 create_latency_items.py
python3 create_nr_items.py
```

### Un panel global de Grafana muestra "No data" aunque los paneles individuales sí tienen datos

**Caso real observado:** el panel "Latencia Global Agentes" (filtro `/Agent .* - .* - Latency/`) mostraba datos correctamente, pero "Network Rejection Global Agentes" (filtro `/Agent .* - .* - NR/`) mostraba **"No data"** — a pesar de que:
- Los items de NR existían en Zabbix (`create_nr_items.py` corrió sin error)
- `send_nr_data.sh` reportaba `processed: 48; failed: 0`
- Los paneles individuales por agente (itemid directo) sí mostraban el valor correcto (ej. `25%`, `0%`)
- El autocompletado del campo "Item" en el editor del panel sí encontraba los items por nombre
- El Query Inspector mostraba `status: 200` pero `frames: Array[0]` (respuesta vacía, sin error)

**Causa:** el datasource de Zabbix en Grafana (`alexanderzobnin-zabbix-datasource`) mantiene una caché interna de metadatos (**Cache TTL**, configurable en la página de configuración del datasource → sección "Zabbix API") para resolver búsquedas por nombre/regex. Los items de NR se crearon **después** de que Latencia ya estaba siendo consultada, así que la caché de items para ese tipo de búsqueda quedó desactualizada.

**Solución:**
1. Ve a **Connections → Data sources → (tu datasource Zabbix) → Settings**
2. En la sección **"Zabbix API"**, busca el campo **"Cache TTL"**
3. Bájalo temporalmente (ej. a `10s`) y dale **"Save & test"**
4. Vuelve al dashboard y refresca — el panel que mostraba "No data" ya debería traer los valores
5. (Opcional) Sube el Cache TTL de nuevo a un valor razonable una vez confirmado, para no sobrecargar la API de Zabbix con consultas repetidas

> Un `Ctrl+Shift+R` (hard refresh del navegador) **no resuelve esto** — la caché problemática vive del lado del datasource/backend de Grafana, no en el navegador.

### Grafana muestra "No data" en varios paneles (todos, no solo uno)

Esos agentes están en Zabbix pero todavía no han recibido el primer valor de `zabbix_sender`. Para forzarlo:

```bash
# Borra los state files (solo una vez)
rm -f /etc/zabbix/scripts/wvx_latency_agent/agent_*_state.json

# Re-ejecuta para enviar TODOS los valores actuales
bash send_latency_data.sh
bash send_nr_data.sh
```

### `bulk_grafana_agent_panels.py` reporta "Agentes completos: 0"

Significa que ningún agente tiene **ambos** items (latencia + NR) creados en Zabbix. Revisa que hayas corrido `create_latency_items.py` **y** `create_nr_items.py` — es común olvidar el segundo porque el primero ya "parece" haber funcionado (el panel global de latencia sí se ve).

### El conteo de agentes no cuadra con el portal de Wolkvox

La API `?api=latency` solo devuelve agentes con sesión activa. Agentes deshabilitados, en vacaciones, sin loguear, o nuevos sin estrenar **no aparecen**. Es normal que el portal muestre 53 y la API 51, por ejemplo.

### `bulk_grafana_agent_panels.py` falla con 401/403

Token expirado, mal copiado, o sin permisos. Verifica:
- `GRAFANA_TOKEN` empieza con `glsa_`
- El service account tiene rol **Editor** (no Viewer)
- O usa basic auth (`GRAFANA_USER`/`GRAFANA_PASS`) si no quieres lidiar con tokens

### Los paneles autogenerados desaparecen pero los manuales no

Eso es lo esperado: el script reconoce sus propios paneles por el `description: auto:wvx_agent_v2` / `auto:wvx_global_v1` y los reemplaza limpiamente. Los manuales no tienen ese marker, así que no los toca.

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
| `LATENCY_ZBX_HOST` / `ZBX_HOST` | `.env` | `ippbx-cloud-issa5-redplus` | Host de Zabbix donde se crean los items |
| `WOLKVOX_SERVER` | `.env` | `00XX` | ID del servidor Wolkvox |
| `WOLKVOX_OPERATION` | `.env` | `unknown_operation` | Prefijo usado en las keys de los items (`{OPERACION}.agent.latency[...]`) |
| `GRAFANA_DASHBOARD_UID` | `.env` | `CHANGE_ME` | UID del dashboard en Grafana |
| `GRAFANA_DS_UID` | `.env` | `CHANGE_ME` | UID del datasource Zabbix en Grafana |
| `PANELS_PER_ROW` | `bulk_grafana_agent_panels.py` | `8` | Cuántos agentes por fila en el dashboard |
| `PANEL_W` | `bulk_grafana_agent_panels.py` | `3` | Ancho de cada panel (grid units) |
| `NR_H` / `LAT_H` | `bulk_grafana_agent_panels.py` | `3` / `2` | Alto de cada sub-panel |
| `START_Y` | `bulk_grafana_agent_panels.py` | `18` | Y inicial (deja espacio para los 2 paneles globales) |
| `MARKER` / `GLOBAL_MARKER` | `bulk_grafana_agent_panels.py` | `auto:wvx_agent_v2` / `auto:wvx_global_v1` | Tags para identificar paneles autogenerados |
| Cache TTL | Configuración del datasource Zabbix en Grafana | Variable | Puede causar "No data" en paneles con filtro por nombre si se crean items nuevos después de la última consulta |

---

## Seguridad

- **No commitees credenciales reales al repo.** Usa el archivo `.env` en la raíz del proyecto e inclúyelo en `.gitignore`.
- El service account token de Grafana se puede **revocar** sin afectar otras cuentas si se compromete.
- El archivo `.env` debe tener permisos restrictivos:
  ```bash
  chmod 600 /etc/zabbix/scripts/wvx_latency_agent/.env
  ```

---

## Notas de campo — instalación real (NueveOnce)

Resumen de particularidades encontradas durante la puesta en producción en el servidor `nueveonce` (Debian 12), que no estaban cubiertas originalmente en este readme:

1. **`apt install python3 zabbix-sender jq curl` desinstaló `zabbix-agent`** por conflicto entre el paquete estable (6.0.x) y `zabbix-sender` de `bookworm-backports` (7.0.x). Se detectó porque `systemctl status zabbix-agent` devolvió `Unit not found`. Se resolvió reinstalando el agente y fijando versiones compatibles.
2. **`pip3` no estaba instalado** (`bash: pip3: command not found`) — hubo que instalar `python3-pip` primero. Luego, `pip3 install` falló por el bloqueo `externally-managed-environment` de Debian 12, resuelto con `--break-system-packages` (en este caso `requests` y `urllib3` ya venían satisfechos por paquetes del sistema).
3. El repo se clonó completo en `/etc/zabbix/scripts/wvx_latency_agent/`; los scripts de este módulo están en la subcarpeta `wvx_latency_nr/`, pero el `.env` vive un nivel arriba, en la raíz del proyecto.
4. La frecuencia del poller se configuró en **cada 30 minutos** (no cada minuto) según requerimiento del cliente.
5. Se presentó el caso de "No data" en el panel global de Network Rejection descrito en la sección de Troubleshooting — causado por el **Cache TTL** del datasource de Zabbix en Grafana, no por falta de datos ni error de configuración de los items.
6. UIDs reales usados en esta instalación (como referencia de formato, no reutilizar):
   - `GRAFANA_DASHBOARD_UID`: se obtiene de la URL `/d/{UID}/...` (no de `/dashboards/f/{folder_uid}/...`, que es la carpeta)
   - `GRAFANA_DS_UID`: se obtiene de `/connections/datasources/edit/{UID}`

---

## Mantenimiento

| Tarea | Frecuencia | Cómo |
|---|---|---|
| Verificar logs de sync | Semanal | `tail -200 /var/log/zabbix/sync_agents.log` |
| Verificar cron activo | Mensual | `crontab -l` |
| Actualizar token Grafana | Cuando expire | Service account → New token → actualizar `.env` |
| Rotar password Zabbix | Política interna | Editar `ZBX_PASS` en `.env` |
| Revisar Cache TTL del datasource Zabbix | Si aparece "No data" en paneles nuevos tras crear items | Connections → Data sources → Zabbix → Settings → Zabbix API |

---

**Autor:** Equipo de monitoreo
**Versión:** 2.1 (incluye notas de instalación real — NueveOnce)
