import ipaddress
from netmiko import ConnectHandler
import pandas as pd

# Check the Syntax of the input:
def is_valid_ip_range(IP_Range):
    parts = IP_Range.split('-')
    if len(parts) == 2:
            start_ip_str = parts[0].strip()
            end_ip_str = parts[1].strip()
    elif len(parts) == 1:
         return True
    else:
        return False



    try:
        # 2. Validate syntax and values using the ipaddress module
        start_ip = ipaddress.IPv4Address(start_ip_str)
        end_ip = ipaddress.IPv4Address(end_ip_str)
        #return start_ip <= end_ip
        return True 
    
    except ValueError:
            # This triggers if an IP is malformed (e.g., 10.10.10.300)
            return False


def get_ip_list(IP_Range):
    parts = IP_Range.split('-')

    if len(parts) == 1:
        return [parts[0].strip()]

    if len(parts) != 2:
        raise ValueError("Invalid IP range format")

    start_ip = ipaddress.IPv4Address(parts[0].strip())
    end_ip = ipaddress.IPv4Address(parts[1].strip())

    return [
        str(ipaddress.IPv4Address(ip))
        for ip in range(int(start_ip), int(end_ip) + 1)
    ]

while True:
    IP_Range = input("Insert the IP Address Range (10.10.10.1 - 10.10.10.254): ")

    try:
        my_devices = get_ip_list(IP_Range)

        if len(my_devices) == 1:
            print(f"Only one IP Address provided: {my_devices[0]}")
        else:
            print(
                f"Thanks, I'm proceeding with this IP-Range: {IP_Range}, "
                f"I found {len(my_devices)} devices in range."
            )

        break

    except ValueError as e:
        print(e)

#Netmiko Part: 

device = {
    "device_type": "cisco_ios",
    "username": "admin",
    "password": "m1amivice19!"
}
csvdata = []
for ip in my_devices:
 
    device["host"] = ip
    net_connect = None

    try:
          print(f"\n--- Connecting to {ip} ---")
          net_connect = ConnectHandler(**device)
          #output = net_connect.send_command("show version")
          parsed_output_sv = net_connect.send_command("show version", use_genie=True)
          parsed_output_sip = net_connect.send_command("show ip interface brief", use_genie=True)
          
          v = parsed_output_sv.get('version', {})
          host   = v.get('hostname', 'N/A')
          ver    = v.get('version', 'N/A')
          up     = v.get('uptime', 'N/A')
          dev    = v.get('chassis', 'Unknown')
          serial = v.get('chassis_sn', 'Unknown')
          


          sip = parsed_output_sip.get('interface', {})
            
          mgmt = []
        
          for interface, details in sip.items():
                ip_addr = details.get("ip_address")

                if ip_addr and ip_addr.startswith("10.49."):
                    mgmt = {
                        "interface": interface,
                        "ip_address": ip_addr,
                        "status": details.get("status"),
                        "protocol": details.get("protocol"),
                    }
                    break

    except Exception as e:
          print(f"Could not connect to {ip}: {e}")
    finally:
            if net_connect:
                net_connect.disconnect()
                print(f"Closed connection to {ip}")
    if mgmt:
        print(
            f"Device {ip} {host} is running version: {ver}, "
            f"uptime is: {up}, platform: {dev}, {serial}, "
            f"{mgmt['interface']} , {mgmt['ip_address']}"
        )
    else:
        print(
            f"Device {ip} {host} is running version: {ver}, "
            f"uptime is: {up}, platform: {dev}, {serial}, "
            f"no management interface found"
        )

    ### CSV File Part: 

    csvdata.append({
        "hostname": host,
        "platform": dev,
        "version": ver,
        "serial": serial,
        "mgmt_interface": mgmt["interface"] if mgmt else "N/A",
        "mgmt_ip": mgmt["ip_address"] if mgmt else "N/A",
        "uptime": up
        })

df = pd.DataFrame(csvdata)
df.to_csv('output.csv', index=False)
        