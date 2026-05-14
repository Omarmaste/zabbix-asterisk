#!/usr/bin/env bash
# =============================================================
# install_zabbix.sh — Instala ítems y triggers en Zabbix
# Módulos: fail2ban, sip, pjsip, countcalls, latencyagent, auditlog
#
# Uso:
#   bash install_zabbix.sh                              # instala todo
#   bash install_zabbix.sh --skip-agent                 # no toca zabbix_agentd.conf
#   bash install_zabbix.sh --skip-voip                  # omite fail2ban+SIP+PJSIP+countcalls
#   bash install_zabbix.sh --signal_voip=SIP            # usa SIP   → excluye PJSIP
#   bash install_zabbix.sh --signal_voip=PJSIP          # usa PJSIP → excluye SIP
#   bash install_zabbix.sh --signal_voip=SIP,PJSIP      # usa ambos → no excluye nada
#
# Combinaciones frecuentes:
#   --skip-agent --skip-voip          → solo latency + auditlog
#   --skip-agent --signal_voip=SIP    → SIP+countcalls+latency+auditlog sin tocar agente
# =============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_AGENT=0
SKIP_VOIP=0
EXCLUDE_SIP=0
EXCLUDE_PJSIP=0

# Parsear argumentos
for arg in "$@"; do
    case "$arg" in
        --skip-agent)
            SKIP_AGENT=1
            ;;
        --skip-voip)
            SKIP_VOIP=1
            ;;
        --signal_voip=*)
            val="${arg#--signal_voip=}"
            # El valor indica qué señalización SE USA → la otra se excluye.
            has_sip=0; has_pjsip=0
            IFS=',' read -ra voip_list <<< "$val"
            for v in "${voip_list[@]}"; do
                v_upper="${v^^}"
                v_upper="${v_upper// /}"
                [[ "$v_upper" == "SIP"   ]] && has_sip=1
                [[ "$v_upper" == "PJSIP" ]] && has_pjsip=1
            done
            [[ $has_sip   -eq 0 ]] && EXCLUDE_SIP=1
            [[ $has_pjsip -eq 0 ]] && EXCLUDE_PJSIP=1
            ;;
        *)
            echo "Argumento desconocido: $arg"
            echo "Uso: bash install_zabbix.sh [--skip-agent] [--skip-voip] [--signal_voip=SIP|PJSIP|SIP,PJSIP]"
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
B='\033[1;34m'; C='\033[0;36m'; W='\033[1m'; N='\033[0m'

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

# run <label> <cmd...>
# Ejecuta cmd, muestra OK/FAIL y las últimas líneas relevantes de output.
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

# Mostrar qué módulos se omiten
if [[ $SKIP_VOIP -eq 1 ]]; then
    echo -e "  ${Y}--skip-voip     : omite fail2ban, SIP, PJSIP, countcalls${N}"
else
    excluded_voip=()
    [[ $EXCLUDE_SIP   -eq 1 ]] && excluded_voip+=("SIP")
    [[ $EXCLUDE_PJSIP -eq 1 ]] && excluded_voip+=("PJSIP")
    if [[ ${#excluded_voip[@]} -gt 0 ]]; then
        echo -e "  ${Y}Excluidos       : ${excluded_voip[*]} (no son la señalización activa)${N}"
    fi
fi
echo ""
check_creds

# ═══════════════════════════════════════════════════════════════
# MÓDULOS 1–5 — ASTERISK/VOIP (omitidos con --skip-voip)
# ═══════════════════════════════════════════════════════════════
if [[ $SKIP_VOIP -eq 1 ]]; then
    module_header "FAIL2BAN"
    skip_step "fail2ban (--skip-voip)"
    module_header "SIP — chan_sip"
    skip_step "SIP (--skip-voip)"
    module_header "PJSIP"
    skip_step "PJSIP (--skip-voip)"
    module_header "COUNTCALLS — SIP"
    skip_step "countcalls SIP (--skip-voip)"
    module_header "COUNTCALLS — PJSIP"
    skip_step "countcalls PJSIP (--skip-voip)"
else

# MÓDULO 1 — FAIL2BAN
module_header "FAIL2BAN"

run "Items fail2ban" \
    env ZBX_HOST="${ZBX_HOST_FAIL2BAN:-${ZBX_HOST:-Zabbix server}}" \
    python3 "${SCRIPT_DIR}/fail2ban/asterisk.fail2ban.bulk.py"

# MÓDULO 2 — SIP (chan_sip)
module_header "SIP — chan_sip  [host: ${ZBX_HOST_SIP:-${ZBX_HOST:-gatewayp}}]"

if [[ $EXCLUDE_SIP -eq 1 ]]; then
    skip_step "Módulo SIP excluido (señalización activa: PJSIP)"
    skip_step "Items SIP en Zabbix"
    skip_step "Triggers SIP en Zabbix"
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

# MÓDULO 3 — PJSIP
module_header "PJSIP  [host: ${ZBX_HOST_PJSIP:-${ZBX_HOST:-gatewayd}}]"

if [[ $EXCLUDE_PJSIP -eq 1 ]]; then
    skip_step "Módulo PJSIP excluido (señalización activa: SIP)"
    skip_step "Items PJSIP en Zabbix"
    skip_step "Triggers PJSIP en Zabbix"
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

# MÓDULO 4 — COUNTCALLS SIP
module_header "COUNTCALLS — SIP  [host: ${ZBX_HOST_COUNTCALLS:-${ZBX_HOST:-startgroup}}]"

if [[ $EXCLUDE_SIP -eq 1 ]]; then
    skip_step "Módulo countcalls SIP excluido (señalización activa: PJSIP)"
    skip_step "Items countcalls SIP en Zabbix"
else
    if [[ $SKIP_AGENT -eq 0 ]]; then
        run "Scripts conteo + UserParameters SIP" \
            bash "${SCRIPT_DIR}/sensor_countcalls/bulk_sipcountcalls_scripts.sh"
    else
        skip_step "Scripts conteo SIP (--skip-agent)"
    fi

    run "Items countcalls SIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_COUNTCALLS:-${ZBX_HOST:-startgroup}}" \
        python3 "${SCRIPT_DIR}/sensor_countcalls/bulk_sipcountcalls_serverzabbix.py"
fi

# MÓDULO 5 — COUNTCALLS PJSIP
module_header "COUNTCALLS — PJSIP  [host: ${ZBX_HOST_COUNTCALLS_PJSIP:-${ZBX_HOST:-nueveonce}}]"

if [[ $EXCLUDE_PJSIP -eq 1 ]]; then
    skip_step "Módulo countcalls PJSIP excluido (señalización activa: SIP)"
    skip_step "Items countcalls PJSIP en Zabbix"
else
    if [[ $SKIP_AGENT -eq 0 ]]; then
        run "Scripts conteo + UserParameters PJSIP" \
            bash "${SCRIPT_DIR}/sensor_countcalls/pjsip/bulk_pjsipcountcalls_scripts.sh"
    else
        skip_step "Scripts conteo PJSIP (--skip-agent)"
    fi

    run "Items countcalls PJSIP en Zabbix" \
        env ZBX_HOST="${ZBX_HOST_COUNTCALLS_PJSIP:-${ZBX_HOST:-nueveonce}}" \
        python3 "${SCRIPT_DIR}/sensor_countcalls/pjsip/bulk_pjsipcountcalls_serverzabbix.py"
fi

fi  # end SKIP_VOIP

# ═══════════════════════════════════════════════════════════════
# MÓDULO 6 — LATENCY AGENT
# ═══════════════════════════════════════════════════════════════
module_header "LATENCY AGENT  [host: ${LATENCY_ZBX_HOST:-ippbx-cloud-issa5-redplus}]"

run "Items latencia en Zabbix" \
    python3 "${SCRIPT_DIR}/latencyagent/create_latency_items.py"

run "Items network rejection en Zabbix" \
    python3 "${SCRIPT_DIR}/latencyagent/create_nr_items.py"

# ═══════════════════════════════════════════════════════════════
# MÓDULO 7 — WVX AUDIT LOG
# ═══════════════════════════════════════════════════════════════
module_header "WVX AUDIT LOG  [host: ${ZBX_HOST_AUDITLOG:-${ZBX_HOST:-monitoralo}}]"

if [[ -z "${WVX_OPERATIONS:-}" ]]; then
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
