---

## 🙋 Author

Made with ☕, 🐧 and Python by **Omar Maste**
- GitHub: [@Omarmaste](https://github.com/Omarmaste)
- LinkedIn: [tu-linkedin-aquí](https://www.linkedin.com/in/omar-jose-galaviz-prado-2a865413)
- Email: [omarg55@gmail.com](mailto:omarg55@gmail.com)
- Telegram: [@Ogalaviz](https://t.me/Ogalaviz)
> 💬 Feel free to connect or contribute. Open to collaboration and DevOps fun!

---

# 📡 Zabbix-Asterisk Integration

Monitor your **Asterisk-based VoIP infrastructure** with Zabbix using pre-built scripts for call count tracking and SIP device status.

![GitHub repo size](https://img.shields.io/github/repo-size/Omarmaste/zabbix-asterisk)
![GitHub stars](https://img.shields.io/github/stars/Omarmaste/zabbix-asterisk?style=social)
![GitHub forks](https://img.shields.io/github/forks/Omarmaste/zabbix-asterisk?style=social)
![License](https://img.shields.io/github/license/Omarmaste/zabbix-asterisk)

---

## 🚀 Features

- 📞 Count concurrent calls from Asterisk  
- 🔍 Monitor SIP and PJSIP devices jitter  
- ⚙️ Compatible with Rocky Linux 8 and CentOS   
- 📘 Pre-integrated with Zabbix 6.x agent system  

---

## 📁 Project Structure

```bash
├── bulk_pjsipdevice_scripts.sh              # Generate 1 script per PJSIP trunk to be used by Python for Zabbix item creation
├── bulk_sipdevice_scripts.sh                # Generate 1 script per JSIP trunk to be used by Python for Zabbix item creation
├── bulk_pjsipdevice_serverzabbix.py         # Python script that processes create PJSIP items in Zabbix
├── bulk_sipcountcalls_serverzabbix.py       # Python script that processes create SIP items in Zabbix
├── bulk_pjsipdevice_trigger_serverzabbix.py # Python script that processes PJSIP triggers in Zabbix
├── bulk_sipdevice_trigger_serverzabbix.py   # Python script that processes SIP triggers in Zabbix
├── sensor_countcalls/bulk_sipcountcalls_scripts.sh   # Generate 1 script per SIPCountCalls to be used by Python for Zabbix item creation
├── sensor_countcalls/bulk_sipcountcalls_serverzabbix.py   # Python script that processes SIPCountCalls triggers in Zabbix


