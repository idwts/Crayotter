"""供生命周期 E2E 调用：通过 SSH 重启服务器上的 crayotter 后端。"""
import paramiko
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("8.161.229.68", username="root", password="yan20041111.", look_for_keys=False, allow_agent=False, timeout=30)
stdin, stdout, stderr = client.exec_command("systemctl restart crayotter.service && sleep 3 && systemctl is-active crayotter.service", timeout=60)
status = stdout.read().decode().strip()
print("service:", status)
client.close()
assert status == "active", "backend restart failed"
time.sleep(2)
