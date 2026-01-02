"""
FTP Backup Module
Handles device configuration backups via FTP
"""

from netmiko import ConnectHandler
from icmplib import ping
from ftplib import FTP
import os
from datetime import datetime


class ConfigBackupError(Exception):
    """Custom exception for backup errors"""
    pass


def get_device_config(device_ip, username="admin", password="m1amivice19!"):
    """
    Connect to a device and retrieve running configuration
    
    Args:
        device_ip: IP address of the device
        username: SSH username
        password: SSH password
    
    Returns:
        Configuration output as string
    """
    device = {
        "device_type": "cisco_ios",
        "host": device_ip,
        "username": username,
        "password": password
    }
    
    try:
        with ConnectHandler(**device) as net_connect:
            return net_connect.send_command("show running-config")
    except Exception as e:
        raise ConfigBackupError(f"Failed to retrieve config from {device_ip}: {str(e)}")


def ping_device(device_ip):
    """
    Check if device is reachable via ICMP ping
    
    Args:
        device_ip: IP address to ping
    
    Returns:
        True if reachable, False otherwise
    """
    try:
        ping_result = ping(device_ip, count=2, interval=0.2)
        return ping_result.is_alive
    except Exception as e:
        raise ConfigBackupError(f"Ping check failed: {str(e)}")


def extract_hostname(config_output):
    """
    Extract hostname from running configuration
    
    Args:
        config_output: Running configuration text
    
    Returns:
        Hostname string or 'unknown' if not found
    """
    try:
        for line in config_output.splitlines():
            if line.startswith("hostname "):
                return line.split()[1]
        return "unknown"
    except Exception:
        return "unknown"


def upload_to_ftp(filename, file_content, ftp_host="10.49.208.27", 
                  ftp_user="cisco", ftp_password="cisco", 
                  ftp_directory="/Automated_Skynet_Config_Backups/"):
    """
    Upload backup file to FTP server
    
    Args:
        filename: Name of the file to upload
        file_content: Content to write to file
        ftp_host: FTP server IP/hostname
        ftp_user: FTP username
        ftp_password: FTP password
        ftp_directory: Target directory on FTP server
    
    Returns:
        True if successful
    """
    try:
        # Write file locally first
        with open(filename, "w") as f:
            f.write(file_content)
        
        # Connect to FTP server
        ftp = FTP(ftp_host)
        ftp.login(user=ftp_user, passwd=ftp_password)
        ftp.cwd(ftp_directory)
        
        # Upload file
        with open(filename, "rb") as file:
            ftp.storbinary(f"STOR {filename}", file)
        
        ftp.quit()
        
        # Clean up local file
        os.remove(filename)
        
        return True
    except Exception as e:
        raise ConfigBackupError(f"FTP upload failed: {str(e)}")


def backup_device_config(device_ip, username="admin", password="m1amivice19!",
                        ftp_host="10.49.208.27", ftp_user="cisco", ftp_password="cisco"):
    """
    Complete backup workflow: ping -> get config -> upload to FTP
    
    Args:
        device_ip: IP address of device to backup
        username: SSH username
        password: SSH password
        ftp_host: FTP server IP/hostname
        ftp_user: FTP username
        ftp_password: FTP password
    
    Returns:
        Dictionary with backup status and details
    """
    try:
        # Step 1: Ping device
        if not ping_device(device_ip):
            return {
                "status": "error",
                "message": f"Device {device_ip} is unreachable",
                "device_ip": device_ip
            }
        
        # Step 2: Get running config
        config = get_device_config(device_ip, username, password)
        
        # Step 3: Extract hostname and create filename
        hostname = extract_hostname(config)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{hostname}_{timestamp}_running_config.txt"
        
        # Step 4: Upload to FTP
        upload_to_ftp(filename, config, ftp_host, ftp_user, ftp_password)
        
        return {
            "status": "success",
            "message": f"Configuration backed up successfully for {hostname}",
            "device_ip": device_ip,
            "hostname": hostname,
            "filename": filename,
            "timestamp": timestamp
        }
    
    except ConfigBackupError as e:
        return {
            "status": "error",
            "message": str(e),
            "device_ip": device_ip
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Unexpected error during backup: {str(e)}",
            "device_ip": device_ip
        }
