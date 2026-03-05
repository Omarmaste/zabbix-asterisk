Script para monitorear los estados de servicio y seguridad

una vez cargado la informacion para ser enviado via zabbix sender

Detectar Fail2ban caido
========================
se coloca en el cron
cat  /etc/crontab
*/5 * * * * /bin/bash /etc/zabbix/scripts/asterisk.fail2ban >/dev/null 2>&1


Trigger en Zabbix
Nombre
Asterisk GW Fail2ban Fuera de servicio

Problem expression
last(/ippbx-cloud-issa5-redplus/fail2ban.status)=0

Recovery expression
last(/Zabbix server/fail2ban.status)=1
