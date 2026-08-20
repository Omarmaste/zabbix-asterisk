#!/usr/bin/env bash
# =============================================================
# install_zabbix.sh — Instala ítems y triggers en Zabbix
#
# Módulos (= directorios del proyecto):
#   ast_fail2ban | ast_sip | ast_pjsip | ast_countcalls_latency
#   wvx_latency_nr
#
# Uso:
#   bash install_zabbix.sh                          # instala todo
#   bash install_zabbix.sh --skip-<modulo>          # omite ese módulo
#
# Nota: si ast_sip, ast_pjsip o ast_countcalls_latency están activos,
#       sus scripts de agente se escriben en zabbix_agentd.conf automáticamente.
#
# Ejemplo:
#   # Solo wvx_latency_nr:
#   bash install_zabbix.sh --skip-ast_fail2ban --skip-ast_sip --skip-ast_pjsip \
#                          --skip-ast_countcalls_latency
#
# Primera vez / sin .env: se lanza un asistente interactivo que pregunta
# todo (módulos a activar + credenciales) y genera el .env solo. También
# se puede forzar con --wizard aunque ya exista .env, para rehacerlo.
# =============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Colores (definidos antes que nada: los usa tambien el wizard) ──
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'
B='\033[1;34m'; W='\033[1m'; N='\033[0m'

SKIP_AST_FAIL2BAN=0
SKIP_AST_SIP=0
SKIP_AST_PJSIP=0
SKIP_AST_COUNTCALLS_LATENCY=0
SKIP_WVX_LATENCY_NR=0
RUN_WIZARD=0

for arg in "$@"; do
    case "$arg" in
        --skip-ast_fail2ban)           SKIP_AST_FAIL2BAN=1 ;;
        --skip-ast_sip)                SKIP_AST_SIP=1 ;;
        --skip-ast_pjsip)              SKIP_AST_PJSIP=1 ;;
        --skip-ast_countcalls_latency) SKIP_AST_COUNTCALLS_LATENCY=1 ;;
        --skip-wvx_latency_nr)         SKIP_WVX_LATENCY_NR=1 ;;
        --wizard|--configure)          RUN_WIZARD=1 ;;
        *)
            echo "Argumento desconocido: $arg"
            echo ""
            echo "Uso: bash install_zabbix.sh [--skip-<modulo>] [--wizard]"
            echo "  Módulos: ast_fail2ban  ast_sip  ast_pjsip"
            echo "           ast_countcalls_latency  wvx_latency_nr"
            exit 1
            ;;
    esac
done

# ─── .env: asistente interactivo si no existe (o si se pide --wizard) ──
if [[ ! -f "${SCRIPT_DIR}/.env" ]] || [[ $RUN_WIZARD -eq 1 ]]; then
    source "${SCRIPT_DIR}/install/setup_wizard.sh"
    run_wizard
    # El wizard deja SKIP_* seteados según lo que el usuario respondió
    # ahí adentro — pisa cualquier --skip-* que se haya pasado por CLI.
fi

if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
    echo "ERROR: No existe ${SCRIPT_DIR}/.env"
    echo "Configura las credenciales antes de instalar."
    exit 1
fi
set -a; source "${SCRIPT_DIR}/.env"; set +a

# ─── Contadores ───────────────────────────────────────────────
PASS=0; FAIL_COUNT=0; SKIP_COUNT=0
declare -a FAIL_MSGS=()

# ─── Helpers ──────────────────────────────────────────────────
module_header() {
    echo ""
    echo -e "${B}${W}┌──────────────────────────────────────────────────────┐${N}"
    printf "${B}${W}│  MÓDULO: %-44s│${N}\n" "$1"
    echo -e "${B}${W}└──────────────────────────────────────────────────────┘${N}"
}

run() {
    local label="$1"; shift
    printf "  %-54s" "$label"
    local tmp; tmp=$(mktemp)
    if "$@" > "$tmp" 2>&1; then
        echo -e "[${G}OK${N}]"
        grep -E '(Total|Resumen|creados|OK|Nuevos|completado|Proceso|Tablero|Carpeta|guardado|URL:)' "$tmp" 2>/dev/null \
            | tail -4 | sed 's/^/      /'
        rm -f "$tmp"; ((PASS++))
    else
        local rc=$?
        echo -e "[${R}FAIL${N}] exit=$rc"
        tail -12 "$tmp" | sed 's/^/      [!] /'
        rm -f "$tmp"; ((FAIL_COUNT++))
        FAIL_MSGS+=("$label")
    fi
}

skip_step() {
    printf "  %-54s[${Y}SKIP${N}]\n" "$1"
    ((SKIP_COUNT++))
}

check_creds() {
    local warn=0
    [[ "${ZBX_PASS:-CHANGE_ME}"             == "CHANGE_ME" ]] && { echo -e "  ${Y}⚠${N}  ZBX_PASS no configurado";             warn=1; }
    [[ "${WOLKVOX_TOKEN:-CHANGE_ME}"        == "CHANGE_ME" ]] && { echo -e "  ${Y}⚠${N}  WOLKVOX_TOKEN no configurado";        warn=1; }
    [[ "${GRAFANA_DASHBOARD_UID:-CHANGE_ME}" == "CHANGE_ME" ]] && { echo -e "  ${Y}i${N}  GRAFANA_DASHBOARD_UID vacio — se creara el tablero automaticamente"; }
    [[ "${GRAFANA_DS_UID:-CHANGE_ME}"        == "CHANGE_ME" ]] && { echo -e "  ${Y}⚠${N}  GRAFANA_DS_UID no configurado";        warn=1; }
    [[ $warn -eq 1 ]] && echo ""
}

# ═══════════════════════════════════════════════════════════════
echo -e "${B}${W}"
echo "╔══════════════════════════════════════════════════════╗"
echo "║   ZABBIX-ASTERISK — INSTALACIÓN DE MÓDULOS          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo -e "${N}"
echo "  Servidor Zabbix : ${ZBX_URL:-<no configurado>}"
echo "  Usuario         : ${ZBX_USER:-<no configurado>}"
echo "  Fecha           : $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

declare -A _MODS=(
    [ast_fail2ban]=$SKIP_AST_FAIL2BAN
    [ast_sip]=$SKIP_AST_SIP
    [ast_pjsip]=$SKIP_AST_PJSIP
    [ast_countcalls_latency]=$SKIP_AST_COUNTCALLS_LATENCY
    [wvx_latency_nr]=$SKIP_WVX_LATENCY_NR
)
for mod in ast_fail2ban ast_sip ast_pjsip ast_countcalls_latency wvx_latency_nr; do
    if [[ ${_MODS[$mod]} -eq 1 ]]; then
        printf "  ${Y}%-28s${N} SKIP\n" "$mod"
    else
        printf "  ${G}%-28s${N} RUN\n"  "$mod"
    fi
done
echo ""
check_creds

# ═══════════════════════════════════════════════════════════════
# MÓDULO 1 — AST FAIL2BAN
# ═══════════════════════════════════════════════════════════════
module_header "AST FAIL2BAN"

if [[ $SKIP_AST_FAIL2BAN -eq 1 ]]; then
    skip_step "ast_fail2ban (--skip-ast_fail2ban)"
else
    run "Items fail2ban" \
        env ZBX_HOST="${ZBX_HOST_FAIL2BAN:-${ZBX_HOST:-Zabbix server}}" \
        python3 "${SCRIPT_DIR}/ast_fail2ban/asterisk.fail2ban.bulk.py"

    # ─── Cron /etc/crontab ──────────────────────────────────────
    # Detecta estado Fail2ban c/5 min, las 24 h.
    # Elimina ruta vieja (/etc/zabbix/scripts/asterisk.fail2ban)
    # si aún existe, para que no queden dos entradas activas.
    # El marcador incluye SCRIPT_DIR: si no fuera unico por instalacion, un
    # segundo cliente en este mismo servidor vería el marcador del primero
    # ya presente en /etc/crontab y se saltaria su propio cron por completo.
    _CRON_MARKER="AUTO:ast_fail2ban:${SCRIPT_DIR}"
    _F2B_SCRIPT="${SCRIPT_DIR}/ast_fail2ban/asterisk.fail2ban"
    printf "  %-54s" "Cron fail2ban en /etc/crontab"
    # Limpia entrada vieja con ruta anterior (si existiera)
    sed -i '\|/etc/zabbix/scripts/asterisk\.fail2ban|d' /etc/crontab 2>/dev/null || true
    if grep -q "${_CRON_MARKER}" /etc/crontab 2>/dev/null; then
        echo -e "[${Y}SKIP${N}] ya configurado"
        ((SKIP_COUNT++))
    else
        cat >> /etc/crontab <<CRONEOF

#--- ${_CRON_MARKER} ----------------------------------------
# Detecta estado de Fail2ban de Asterisk y envia alerta a Zabbix.
# Intervalo: cada 5 min, las 24 horas.
#------------------------------------------------------------
*/5 * * * * root /bin/bash ${_F2B_SCRIPT} >/dev/null 2>&1
#--- END ${_CRON_MARKER} ------------------------------------
CRONEOF
        if [[ $? -eq 0 ]]; then
            echo -e "[${G}OK${N}]"
            echo "      Cada 5 min, 24 h | ${_F2B_SCRIPT}"
            ((PASS++))
        else
            echo -e "[${R}FAIL${N}]"
            ((FAIL_COUNT++))
            FAIL_MSGS+=("Cron fail2ban en /etc/crontab")
        fi
    fi
fi

# ═══════════════════════════════════════════════════════════════
# MÓDULO 2 — AST SIP
# ═══════════════════════════════════════════════════════════════
module_header "AST SIP  [host: ${ZBX_HOST_SIP:-${ZBX_HOST:-gatewayp}}]"

if [[ $SKIP_AST_SIP -eq 1 ]]; then
    skip_step "ast_sip (--skip-ast_sip)"
else
    run "Scripts agente + UserParameters SIP" \
        bash "${SCRIPT_DIR}/ast_sip/bulk_sipdevice_scripts.sh"
    run "Items SIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_SIP:-${ZBX_HOST:-gatewayp}}" \
        python3 "${SCRIPT_DIR}/ast_sip/bulk_sipdevice_serverzabbix.py"
    run "Triggers SIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_SIP:-${ZBX_HOST:-gatewayp}}" \
        python3 "${SCRIPT_DIR}/ast_sip/bulk_sipdevice_trigger_serverzabbix.py"
fi

# ═══════════════════════════════════════════════════════════════
# MÓDULO 3 — AST PJSIP
# ═══════════════════════════════════════════════════════════════
module_header "AST PJSIP  [host: ${ZBX_HOST_PJSIP:-${ZBX_HOST:-gatewayd}}]"

if [[ $SKIP_AST_PJSIP -eq 1 ]]; then
    skip_step "ast_pjsip (--skip-ast_pjsip)"
else
    run "Scripts agente + UserParameters PJSIP" \
        bash "${SCRIPT_DIR}/ast_pjsip/bulk_pjsipdevice_scripts.sh"
    run "Items PJSIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_PJSIP:-${ZBX_HOST:-gatewayd}}" \
        python3 "${SCRIPT_DIR}/ast_pjsip/bulk_pjsipdevice_serverzabbix.py"
    run "Triggers PJSIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_PJSIP:-${ZBX_HOST:-gatewayd}}" \
        python3 "${SCRIPT_DIR}/ast_pjsip/bulk_pjsipdevice_trigger_serverzabbix.py"
fi

# ═══════════════════════════════════════════════════════════════
# MÓDULO 4 — AST COUNTCALLS LATENCY
# ═══════════════════════════════════════════════════════════════
module_header "AST COUNTCALLS LATENCY  [host: ${ZBX_HOST_COUNTCALLS:-${ZBX_HOST:-startgroup}}]"

if [[ $SKIP_AST_COUNTCALLS_LATENCY -eq 1 ]]; then
    skip_step "ast_countcalls_latency (--skip-ast_countcalls_latency)"
else
    run "Scripts conteo + UserParameters SIP" \
        bash "${SCRIPT_DIR}/ast_countcalls_latency/bulk_sipcountcalls_scripts.sh"
    run "Scripts conteo + UserParameters PJSIP" \
        bash "${SCRIPT_DIR}/ast_countcalls_latency/pjsip/bulk_pjsipcountcalls_scripts.sh"
    run "Items countcalls SIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_COUNTCALLS:-${ZBX_HOST:-startgroup}}" \
        python3 "${SCRIPT_DIR}/ast_countcalls_latency/bulk_sipcountcalls_serverzabbix.py"
    run "Items countcalls PJSIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_COUNTCALLS_PJSIP:-${ZBX_HOST:-nueveonce}}" \
        python3 "${SCRIPT_DIR}/ast_countcalls_latency/pjsip/bulk_pjsipcountcalls_serverzabbix.py"
fi

# ═══════════════════════════════════════════════════════════════
# MÓDULO 5 — WVX LATENCY NR
# ═══════════════════════════════════════════════════════════════
module_header "WVX LATENCY NR  [host: ${LATENCY_ZBX_HOST:-${ZBX_HOST:-ippbx-cloud-issa5-redplus}}]"

if [[ $SKIP_WVX_LATENCY_NR -eq 1 ]]; then
    skip_step "wvx_latency_nr (--skip-wvx_latency_nr)"
else
    run "Items latencia en Zabbix" \
        python3 "${SCRIPT_DIR}/wvx_latency_nr/create_latency_items.py"
    run "Items network rejection en Zabbix" \
        python3 "${SCRIPT_DIR}/wvx_latency_nr/create_nr_items.py"
    run "Items estado/plataforma/conexion/version en Zabbix" \
        python3 "${SCRIPT_DIR}/wvx_latency_nr/create_status_items.py"
    run "Primer envio de estado/plataforma/conexion/version" \
        bash "${SCRIPT_DIR}/wvx_latency_nr/send_status_data.sh"
    # Si GRAFANA_DASHBOARD_UID esta vacio/CHANGE_ME (cliente nuevo), este paso
    # crea el tablero + carpeta en Grafana automaticamente (mismo formato que
    # "wvx - npls - Latencia Agentes") y guarda el UID nuevo en el .env.
    run "Paneles de agentes en Grafana (NR+Latencia+Estado+Plataforma+Conexion+Version)" \
        python3 "${SCRIPT_DIR}/wvx_latency_nr/bulk_grafana_agent_panels.py"

    # ─── Cron /etc/crontab ──────────────────────────────────────
    # Ventana 07:00-21:00: horario operativo del contact center.
    # send_latency c/10 min | send_nr c/11 min | send_status c/12 min —
    #   desfases distintos para que los pollers no colisionen en la API.
    # sync_agents 01:00 AM — registra agentes nuevos (latencia/NR/estado)
    #   en Zabbix y regenera todos los paneles de Grafana automáticamente,
    #   incluyendo el umbral de "version desactualizada" (30 dias).
    # El marcador incluye SCRIPT_DIR (ver nota igual en el modulo AST FAIL2BAN):
    # unico por instalacion para que un segundo cliente en este servidor no
    # se salte su propio cron creyendo que "ya esta configurado".
    _CRON_MARKER="AUTO:wvx_latency_nr:${SCRIPT_DIR}"
    _WVX_SCRIPTS="${SCRIPT_DIR}/wvx_latency_nr"
    printf "  %-54s" "Cron entries en /etc/crontab"
    if grep -q "${_CRON_MARKER}" /etc/crontab 2>/dev/null; then
        echo -e "[${Y}SKIP${N}] ya configurado"
        ((SKIP_COUNT++))
    else
        cat >> /etc/crontab <<CRONEOF

#--- ${_CRON_MARKER}
*/10 7-21 * * * root /bin/bash ${_WVX_SCRIPTS}/send_latency_data.sh >/dev/null 2>&1
*/11 7-21 * * * root /bin/bash ${_WVX_SCRIPTS}/send_nr_data.sh >/dev/null 2>&1
*/12 7-21 * * * root /bin/bash ${_WVX_SCRIPTS}/send_status_data.sh >/dev/null 2>&1
0 1 * * * root /bin/bash ${_WVX_SCRIPTS}/sync_agents.sh >/dev/null 2>&1
#--- END ${_CRON_MARKER}
CRONEOF
        if [[ $? -eq 0 ]]; then
            echo -e "[${G}OK${N}]"
            echo "      Ventana 07:00-21:00 | latencia c/10 min | NR c/11 min | estado c/12 min | sync 01:00 AM"
            ((PASS++))
        else
            echo -e "[${R}FAIL${N}]"
            ((FAIL_COUNT++))
            FAIL_MSGS+=("Cron entries en /etc/crontab")
        fi
    fi
fi

# ═══════════════════════════════════════════════════════════════
# RESUMEN
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${B}${W}╔══════════════════════════════════════════════════════╗${N}"
echo -e "${B}${W}║  RESUMEN                                             ║${N}"
echo -e "${B}${W}╚══════════════════════════════════════════════════════╝${N}"
echo ""
echo -e "  ${G}✓ Exitosos :${N} ${PASS}"
echo -e "  ${Y}⏭ Omitidos :${N} ${SKIP_COUNT}"
echo -e "  ${R}✗ Fallidos :${N} ${FAIL_COUNT}"

if [[ $FAIL_COUNT -gt 0 ]]; then
    echo ""
    echo -e "  ${R}Pasos con error:${N}"
    for msg in "${FAIL_MSGS[@]}"; do
        echo "    - $msg"
    done
    echo ""
    exit 1
fi

echo ""
echo -e "  ${G}${W}Instalación completada.${N}"
echo ""
