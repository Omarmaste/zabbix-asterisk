#!/usr/bin/env bash
# =============================================================
# install_zabbix.sh — Instala ítems y triggers en Zabbix
#
# Módulos disponibles (directorios del proyecto):
#   fail2ban | sip | pjsip | sensor_countcalls | wvx_latency_nr | wvx_auditlog
#
# Uso:
#   bash install_zabbix.sh                          # instala todo
#   bash install_zabbix.sh --skip-agent             # no toca zabbix_agentd.conf
#   bash install_zabbix.sh --skip-<modulo>          # omite ese módulo
#
# Ejemplos:
#   bash install_zabbix.sh --skip-agent --skip-sip --skip-pjsip --skip-fail2ban \
#                          --skip-sensor_countcalls --skip-wvx_auditlog
#     → solo corre wvx_latency_nr
#
#   bash install_zabbix.sh --skip-agent --skip-pjsip --skip-sensor_countcalls
#     → corre fail2ban + sip + wvx_latency_nr + wvx_auditlog
# =============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SKIP_AGENT=0
SKIP_FAIL2BAN=0
SKIP_SIP=0
SKIP_PJSIP=0
SKIP_AST_COUNTCALLS_LATENCY=0
SKIP_WVX_LATENCY_NR=0
SKIP_WVX_AUDITLOG=0

for arg in "$@"; do
    case "$arg" in
        --skip-agent)              SKIP_AGENT=1 ;;
        --skip-fail2ban)           SKIP_FAIL2BAN=1 ;;
        --skip-sip)                SKIP_SIP=1 ;;
        --skip-pjsip)              SKIP_PJSIP=1 ;;
        --skip-ast_countcalls_latency) SKIP_AST_COUNTCALLS_LATENCY=1 ;;
        --skip-wvx_latency_nr)     SKIP_WVX_LATENCY_NR=1 ;;
        --skip-wvx_auditlog)       SKIP_WVX_AUDITLOG=1 ;;
        *)
            echo "Argumento desconocido: $arg"
            echo ""
            echo "Uso: bash install_zabbix.sh [--skip-agent]"
            echo "       [--skip-fail2ban] [--skip-sip] [--skip-pjsip]"
            echo "       [--skip-ast_countcalls_latency] [--skip-wvx_latency_nr] [--skip-wvx_auditlog]"
            exit 1
            ;;
    esac
done

# ─── Cargar .env ──────────────────────────────────────────────
if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
    echo "ERROR: No existe ${SCRIPT_DIR}/.env"
    echo "Configura las credenciales antes de instalar."
    exit 1
fi
set -a; source "${SCRIPT_DIR}/.env"; set +a

# ─── Colores ──────────────────────────────────────────────────
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'
B='\033[1;34m'; W='\033[1m'; N='\033[0m'

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
        grep -E '(Total|Resumen|creados|OK|Nuevos|completado|Proceso)' "$tmp" 2>/dev/null \
            | tail -2 | sed 's/^/      /'
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
    [[ "${ZBX_PASS:-CHANGE_ME}"      == "CHANGE_ME" ]] && { echo -e "  ${Y}⚠${N}  ZBX_PASS no configurado";      warn=1; }
    [[ "${WOLKVOX_TOKEN:-CHANGE_ME}" == "CHANGE_ME" ]] && { echo -e "  ${Y}⚠${N}  WOLKVOX_TOKEN no configurado"; warn=1; }
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

# Mostrar módulos activos/skipped
declare -A _MODS=(
    [fail2ban]=$SKIP_FAIL2BAN
    [sip]=$SKIP_SIP
    [pjsip]=$SKIP_PJSIP
    [ast_countcalls_latency]=$SKIP_AST_COUNTCALLS_LATENCY
    [wvx_latency_nr]=$SKIP_WVX_LATENCY_NR
    [wvx_auditlog]=$SKIP_WVX_AUDITLOG
)
for mod in fail2ban sip pjsip ast_countcalls_latency wvx_latency_nr wvx_auditlog; do
    if [[ ${_MODS[$mod]} -eq 1 ]]; then
        printf "  ${Y}%-24s${N} SKIP\n" "$mod"
    else
        printf "  ${G}%-24s${N} RUN\n"  "$mod"
    fi
done
echo ""
check_creds

# ═══════════════════════════════════════════════════════════════
# MÓDULO 1 — FAIL2BAN
# ═══════════════════════════════════════════════════════════════
module_header "FAIL2BAN"

if [[ $SKIP_FAIL2BAN -eq 1 ]]; then
    skip_step "fail2ban (--skip-fail2ban)"
else
    run "Items fail2ban" \
        env ZBX_HOST="${ZBX_HOST_FAIL2BAN:-${ZBX_HOST:-Zabbix server}}" \
        python3 "${SCRIPT_DIR}/fail2ban/asterisk.fail2ban.bulk.py"
fi

# ═══════════════════════════════════════════════════════════════
# MÓDULO 2 — SIP
# ═══════════════════════════════════════════════════════════════
module_header "SIP — chan_sip  [host: ${ZBX_HOST_SIP:-${ZBX_HOST:-gatewayp}}]"

if [[ $SKIP_SIP -eq 1 ]]; then
    skip_step "sip (--skip-sip)"
else
    if [[ $SKIP_AGENT -eq 0 ]]; then
        run "Scripts agente + UserParameters SIP" \
            bash "${SCRIPT_DIR}/sip/bulk_sipdevice_scripts.sh"
    else
        skip_step "Scripts agente SIP (--skip-agent)"
    fi
    run "Items SIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_SIP:-${ZBX_HOST:-gatewayp}}" \
        python3 "${SCRIPT_DIR}/sip/bulk_sipdevice_serverzabbix.py"
    run "Triggers SIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_SIP:-${ZBX_HOST:-gatewayp}}" \
        python3 "${SCRIPT_DIR}/sip/bulk_sipdevice_trigger_serverzabbix.py"
fi

# ═══════════════════════════════════════════════════════════════
# MÓDULO 3 — PJSIP
# ═══════════════════════════════════════════════════════════════
module_header "PJSIP  [host: ${ZBX_HOST_PJSIP:-${ZBX_HOST:-gatewayd}}]"

if [[ $SKIP_PJSIP -eq 1 ]]; then
    skip_step "pjsip (--skip-pjsip)"
else
    if [[ $SKIP_AGENT -eq 0 ]]; then
        run "Scripts agente + UserParameters PJSIP" \
            bash "${SCRIPT_DIR}/pjsip/bulk_pjsipdevice_scripts.sh"
    else
        skip_step "Scripts agente PJSIP (--skip-agent)"
    fi
    run "Items PJSIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_PJSIP:-${ZBX_HOST:-gatewayd}}" \
        python3 "${SCRIPT_DIR}/pjsip/bulk_pjsipdevice_serverzabbix.py"
    run "Triggers PJSIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_PJSIP:-${ZBX_HOST:-gatewayd}}" \
        python3 "${SCRIPT_DIR}/pjsip/bulk_pjsipdevice_trigger_serverzabbix.py"
fi

# ═══════════════════════════════════════════════════════════════
# MÓDULO 4 — SENSOR COUNTCALLS
# ═══════════════════════════════════════════════════════════════
module_header "AST COUNTCALLS LATENCY  [host: ${ZBX_HOST_COUNTCALLS:-${ZBX_HOST:-startgroup}}]"

if [[ $SKIP_AST_COUNTCALLS_LATENCY -eq 1 ]]; then
    skip_step "ast_countcalls_latency (--skip-ast_countcalls_latency)"
else
    if [[ $SKIP_AGENT -eq 0 ]]; then
        run "Scripts conteo + UserParameters SIP" \
            bash "${SCRIPT_DIR}/ast_countcalls_latency/bulk_sipcountcalls_scripts.sh"
        run "Scripts conteo + UserParameters PJSIP" \
            bash "${SCRIPT_DIR}/ast_countcalls_latency/pjsip/bulk_pjsipcountcalls_scripts.sh"
    else
        skip_step "Scripts conteo SIP (--skip-agent)"
        skip_step "Scripts conteo PJSIP (--skip-agent)"
    fi
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
fi

# ═══════════════════════════════════════════════════════════════
# MÓDULO 6 — WVX AUDIT LOG
# ═══════════════════════════════════════════════════════════════
module_header "WVX AUDIT LOG  [host: ${ZBX_HOST_AUDITLOG:-${ZBX_HOST:-monitoralo}}]"

if [[ $SKIP_WVX_AUDITLOG -eq 1 ]]; then
    skip_step "wvx_auditlog (--skip-wvx_auditlog)"
elif [[ -z "${WVX_OPERATIONS:-}" ]]; then
    skip_step "WVX_OPERATIONS vacío en .env — omitido"
else
    for op in $WVX_OPERATIONS; do
        run "Items auditlog [$op]" \
            env ZBX_HOST="${ZBX_HOST_AUDITLOG:-${ZBX_HOST:-monitoralo}}" \
            python3 "${SCRIPT_DIR}/wvx_auditlog/create_items_auditlog.py" "$op"
        run "Triggers auditlog [$op]" \
            env ZBX_HOST="${ZBX_HOST_AUDITLOG:-${ZBX_HOST:-monitoralo}}" \
            python3 "${SCRIPT_DIR}/wvx_auditlog/create_trigger.py" "$op"
    done
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
