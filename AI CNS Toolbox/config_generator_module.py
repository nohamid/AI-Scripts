"""
Config Generator Module
Automatically generates Cisco device configurations
"""

import ipaddress
from icmplib import ping


class ConfigGeneratorError(Exception):
    """Custom exception for config generation errors"""
    pass


def check_ip_availability(ip_address):
    """
    Check if an IP address is already in use via ICMP ping
    
    Args:
        ip_address: IP address to check
    
    Returns:
        True if IP is free, False if already in use
    """
    try:
        host = ping(ip_address, count=2, interval=0.2)
        return not host.is_alive
    except Exception as e:
        raise ConfigGeneratorError(f"Unable to check IP availability: {str(e)}")


def parse_ip_and_mask(ip_with_prefix):
    """
    Parse IP address and prefix notation to separate IP and netmask
    
    Args:
        ip_with_prefix: IP address in CIDR notation (e.g., 192.168.1.1/24)
    
    Returns:
        Tuple of (ip_only, netmask)
    """
    try:
        iface = ipaddress.IPv4Interface(ip_with_prefix)
        ip_only = str(iface.ip)
        mask_only = str(iface.netmask)
        return ip_only, mask_only
    except ValueError as e:
        raise ConfigGeneratorError(f"Invalid IP format. Use X.X.X.X/24 notation: {str(e)}")


def get_default_gateway(ip_with_prefix):
    """
    Get the first usable IP in the subnet (typically the gateway)
    
    Args:
        ip_with_prefix: IP address in CIDR notation
    
    Returns:
        First usable IP address in the subnet
    """
    try:
        network = ipaddress.ip_network(ip_with_prefix, strict=False)
        return str(network[1])
    except Exception as e:
        raise ConfigGeneratorError(f"Unable to calculate gateway: {str(e)}")


def generate_config(interface, ip_with_prefix):
    """
    Generate Cisco device configuration
    
    Args:
        interface: Interface name (e.g., GigabitEthernet0/0/0)
        ip_with_prefix: IP address in CIDR notation (e.g., 192.168.1.1/24)
    
    Returns:
        Dictionary with config status and generated configuration
    """
    try:
        # Parse IP and netmask
        ip_only, mask_only = parse_ip_and_mask(ip_with_prefix)
        
        # Get default gateway
        gateway = get_default_gateway(ip_with_prefix)
        
        # Check if IP is available
        if not check_ip_availability(ip_only):
            return {
                "status": "error",
                "message": f"IP address {ip_only} is already in use!",
                "interface": interface,
                "ip": ip_only
            }
        
        # Generate configuration
        config = (
            f"enable\n"
            f"configure terminal\n"
            f"vrf definition Mgmt-vrf\n"
            f"address-family ipv4\n"
            f"exit\n"
            f"exit\n"
            f"interface {interface}\n"
            f"ip address {ip_only} {mask_only}\n"
            f"no shut\n"
            f"cdp enable\n"
            f"exit\n"
            f"ip domain name cisco.com\n"
            f"ip route vrf Mgmt-vrf 0.0.0.0 0.0.0.0 {gateway}\n"
            f"hostname auto-provisioned\n"
            f"crypto key generate rsa modulus 2048\n"
            f"username admin privilege 15 password cisco\n"
            f"line vty 0 4\n"
            f"transport input ssh\n"
            f"login local\n"
            f"exit\n"
        )
        
        return {
            "status": "success",
            "message": f"Configuration generated successfully for {interface}",
            "interface": interface,
            "ip": ip_only,
            "netmask": mask_only,
            "gateway": gateway,
            "config": config
        }
    
    except ConfigGeneratorError as e:
        return {
            "status": "error",
            "message": str(e),
            "interface": interface,
            "ip": ip_with_prefix
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Unexpected error: {str(e)}",
            "interface": interface,
            "ip": ip_with_prefix
        }
