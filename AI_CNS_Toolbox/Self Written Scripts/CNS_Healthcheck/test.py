from netmiko import ConnectHandler

device = {
    "device_type": "cisco_ios",
    "host": "10.49.214.209",
    "username": "admin",
    "password": "m1amivice19!"
}


net_connect = ConnectHandler(**device)
parsed_output_sip = net_connect.send_command("show ip interface brief", use_genie=True)
sip = parsed_output_sip.get('interface', {})


for interface, details in sip.items():
    ip = details.get("ip_address")
    

    if ip and ip.startswith("10.49."):
        mgmt = {
            "interface": interface,
            "ip_address": details.get("ip_address"),
            "status": details.get("status"),
            "protocol": details.get("protocol"),
        }
        break
    
        
# Get the Management Interface data
print(mgmt["interface"])
print(mgmt["ip_address"])

