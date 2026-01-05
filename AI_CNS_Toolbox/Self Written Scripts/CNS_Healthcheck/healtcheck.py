import ipaddress
from netmiko import ConnectHandler
import pandas as pd
import os
#cec
# Check the Syntax of the input:
def is_valid_ip_range(IP_Range):
    parts = IP_Range.split('-')
    if len(parts) != 2:
            return False
    
    start_ip_str = parts[0].strip()
    end_ip_str = parts[1].strip()

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
    """Converts '10.10.10.1 - 10.10.10.3' into ['10.10.10.1', '10.10.10.2', '10.10.10.3']"""
    parts = IP_Range.split('-')
    start_ip = ipaddress.IPv4Address(parts[0].strip())
    end_ip = ipaddress.IPv4Address(parts[1].strip())
    
    # Generate every IP from start to end (inclusive)
    # We cast to int to create the range, then back to string for Netmiko
    return [str(ipaddress.IPv4Address(ip)) for ip in range(int(start_ip), int(end_ip) + 1)]

def run_cns_healthcheck(ip_range, username="admin", password="m1amivice19!"):
    """
    Run CNS Health Check on a range of IPs
    
    Args:
        ip_range (str): IP range in format "10.10.10.1 - 10.10.10.254"
        username (str): Device username for SSH connection
        password (str): Device password for SSH connection
    
    Returns:
        dict: Contains 'status', 'message', 'data' (list of devices), 'csv_path'
    """
    
    # Validate IP range
    if not is_valid_ip_range(ip_range):
        return {
            'status': 'error',
            'message': 'Invalid IP range format. Use format: 10.10.10.1 - 10.10.10.254'
        }
    
    my_devices = get_ip_list(ip_range)
    
    # Device configuration for Netmiko
    device = {
        "device_type": "cisco_ios",
        "username": username,
        "password": password
    }
    
    csvdata = []
    device_results = []
    error_count = 0
    success_count = 0
    
    for ip in my_devices:
        device["host"] = ip
        net_connect = None

        try:
            net_connect = ConnectHandler(**device)
            parsed_output_sv = net_connect.send_command("show version", use_genie=True)
            parsed_output_sip = net_connect.send_command("show ip interface brief", use_genie=True)
            
            v = parsed_output_sv.get('version', {})
            host   = v.get('hostname', 'N/A')
            ver    = v.get('version', 'N/A')
            up     = v.get('uptime', 'N/A')
            dev    = v.get('chassis', 'Unknown')
            serial = v.get('chassis_sn', 'Unknown')
            
            sip = parsed_output_sip.get('interface', {})
            mgmt = {}
        
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

            # Build device result
            device_result = {
                "ip": ip,
                "hostname": host,
                "platform": dev,
                "version": ver,
                "serial": serial,
                "uptime": up,
                "mgmt_interface": mgmt.get("interface", "N/A") if mgmt else "N/A",
                "mgmt_ip": mgmt.get("ip_address", "N/A") if mgmt else "N/A",
                "mgmt_status": mgmt.get("status", "N/A") if mgmt else "N/A",
                "status": "success"
            }
            
            device_results.append(device_result)
            success_count += 1
            
            csvdata.append({
                "ip_address": ip,
                "hostname": host,
                "platform": dev,
                "version": ver,
                "serial": serial,
                "mgmt_interface": mgmt.get("interface") if mgmt else "N/A",
                "mgmt_ip": mgmt.get("ip_address") if mgmt else "N/A",
                "uptime": up
            })

        except Exception as e:
            error_count += 1
            error_msg = str(e)
            print(f"ERROR for {ip}: {error_msg}")
            device_result = {
                "ip": ip,
                "hostname": "N/A",
                "platform": "N/A",
                "version": "N/A",
                "serial": "N/A",
                "uptime": "N/A",
                "mgmt_interface": "N/A",
                "mgmt_ip": "N/A",
                "mgmt_status": "N/A",
                "status": "error",
                "error": error_msg
            }
            device_results.append(device_result)
        
        finally:
            if net_connect:
                net_connect.disconnect()
    
    # Save to CSV
    csv_path = os.path.join(os.path.dirname(__file__), 'output.csv')
    if csvdata:
        df = pd.DataFrame(csvdata)
        df.to_csv(csv_path, index=False)
    
    result = {
        'status': 'success',
        'message': f'Health check completed. {success_count} devices reachable, {error_count} devices failed.',
        'data': device_results,
        'csv_path': csv_path,
        'total_devices': len(my_devices),
        'success_count': success_count,
        'error_count': error_count
    }
    
    # Debug: Print results
    print(f"\nDEBUG: Returning {len(device_results)} devices")
    for device in device_results:
        print(f"Device {device['ip']}: status={device['status']}, error={device.get('error', 'None')}")
    
    return result

# Interactive mode (for standalone script execution)
if __name__ == '__main__':
    while True:
        IP_Range = input("Insert the IP Address Range (10.10.10.1 - 10.10.10.254): ")

        if is_valid_ip_range(IP_Range):
            print(f"Thanks, I'm proceeding with this IP-Range: {IP_Range}")
            break
        else:
            print("Invalid syntax or IP address. Please try again.")
    
    # Run the health check
    result = run_cns_healthcheck(IP_Range)
    
    print(f"\n{result['message']}")
    print("\nDevice Details:")
    print("=" * 100)
    
    for device in result['data']:
        status_icon = "✓" if device['status'] == 'success' else "✗"
        print(f"{status_icon} {device['ip']} | {device['hostname']} | {device['platform']} | {device['version']} | {device['mgmt_ip']}")
    
    print(f"\nResults saved to: {result['csv_path']}")
        