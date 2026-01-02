# Import Modules: 
import ipaddress
from icmplib import ping
import pyperclip

# Input to gather information: 

interface = input("What Interface do you want to configure? ").replace(" ", "")
IP         =     input("Which IP should be confgured: (X.X.X.X/24) ")

# Parse IP input (CIDR or IP + subnet mask)
try:
    if "/" in IP:
        # CIDR format (10.10.10.1/24)
        IP = IP.replace(" " , "")
        iface = ipaddress.IPv4Interface(IP)

    else:
        # IP + subnet mask (10.10.10.1 255.255.255.0)
        ip, mask = IP.split()
        network = ipaddress.IPv4Network((ip, mask), strict=False)
        iface = ipaddress.IPv4Interface(f"{ip}/{network.prefixlen}")

except ValueError:
    print("Invalid IP format. Use 10.10.10.1/24 or 10.10.10.1 255.255.255.0")
    exit(1)

# Extract values
ip_only = str(iface.ip)
mask_only = iface.netmask
network = iface.network
first_usable_ip = network[1]

 
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
    f"vrf forwarding Mgmt-vrf\n"
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