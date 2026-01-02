from netmiko import ConnectHandler


def get_config(device):
        
    with ConnectHandler(**device) as net_connect:
        return net_connect.send_command("show running-config")