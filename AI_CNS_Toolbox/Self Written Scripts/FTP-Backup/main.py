# Inititalize 

import ipaddress
import pyperclip
import netmiko 
from icmplib import ping
from netmiko_part import get_config
from ftplib import FTP


# First input: 

device = input("Enter IP Address of the device, where you want to backup the configuration: ")
print ("I will check if the device is reachable")

try: 
    ping_check = ping(device, count =2, interval=0.2)
    if not ping_check.is_alive:
        print(f"❌ Network Timeout: {device} is unreachable.")
        exit()
except Exception as e:
    print(f"❌ Technical Error: {e}")
    exit()
   
print(f"✅ {device} reached successfully!")

#### Netmiko Part: 

# Device:

device = {
    "device_type": "cisco_ios",
    "host": device,
    "username": "admin",
    "password": "m1amivice19!"
}

output = get_config(device)
print(output)


#### Extract the Hostname of the Device: 

def output_extract_hostname(output):
    for line in output.splitlines():
        if line.startswith("hostname "):
            return line.split()[1]
    return None


#### FTP Part: 
hostname = output_extract_hostname(output)
filename = f"{hostname}_running_config.txt"

with open(filename, "w") as f:
    f.write(output)

# FTP upload
from ftplib import FTP

ftp = FTP("10.49.208.27")
ftp.login(user="cisco", passwd="cisco")

ftp.cwd("/Automated_Skynet_Config_Backups/")   # ← target directory on FTP serv

with open(filename, "rb") as file:
    ftp.storbinary(f"STOR {filename}", file)

ftp.quit()

print("✅ Configuration copied to clipboard!")
pyperclip.copy(output)