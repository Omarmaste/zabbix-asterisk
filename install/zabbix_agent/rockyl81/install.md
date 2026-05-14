sudo rpm -Uvh https://repo.zabbix.com/zabbix/6.0/rhel/8/x86_64/zabbix-release-latest-6.0.el8.noarch.rpm

** Instala el agente
dnf install -y zabbix-agent

vi /etc/zabbix/zabbix_agentd.conf
Server=192.168.1.10              # IP del Zabbix Server (chequeos pasivos)
ServerActive=192.168.1.10        # IP del Zabbix Server (chequeos activos)
Hostname=mi-servidor-rocky       # hostname del server a monitorear

sudo systemctl enable --now zabbix-agent
sudo systemctl status zabbix-agent

