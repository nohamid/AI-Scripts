# Import Modules: 
import ipaddress
from icmplib import ping
import pyperclip

# Input to gather information: 

interface  =     input("What Interface do you want to configure? ")
IP         =     input("Which IP should be confgured: (X.X.X.X/24) ")

# Get the first IP address of the subnet for default route:
network = ipaddress.ip_network(IP, strict=False)
first_usable_ip = network[1]

# Substract IP Address and Subnet Mask: 

try:
    # Erstellt ein Interface-Objekt
    iface = ipaddress.IPv4Interface(IP)
    
    # Extrahiert die IP und die Netzmaske separat
    ip_only = str(iface.ip)
    mask_only = iface.netmask

except ValueError:
    print("Invalid Format: IP/Prefix (z.B. 10.49.208.1/25).")

# Check if IP Address is free or already in Use: 

def check_device(ip_only):
    # Sends 2 packets
    host = ping(ip_only, count=2, interval=0.2)
    
    if host.is_alive:
        print(f"The IP address is already in use! {ip_only}")
        exit()
    else:
        print(f"The IP Addess: {ip_only} is free.")
        

check_device(ip_only)

# Output of the configuration:

output = ( 
    f"enable\n"
    f"configure terminal\n"
    f"vrf definition Mgmt-vrf\n"
    f"address-family ipv4\n"
    f"exit\n"
    f"exit\n"
    f"interface {interface}\n"
    f"IP address {ip_only} {mask_only}\n"
    f"no shut\n"
    f"cdp enable\n"
    f"exit\n"
    f"ip domain name cisco.com\n"
    f"IP route vrf Mgmt-vrf 0.0.0.0 0.0.0.0 {first_usable_ip}\n"
    f"hostname auto-provisioned\n"
    f"crypto key generate rsa modulus 2048\n"
    f"username admin privilege 15 password cisco\n"
    f"line vty 0 4\n"
    f"transport input ssh\n"
    f"login local\n"
    f"exit\n"
)
print(output)

# Copy Configuration automatically to clipboard

print("✅ Configuration copied to clipboard!")
pyperclip.copy(output)