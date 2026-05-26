import psutil
import socket
import subprocess
import requests

def get_hostname_and_ip_addresses():
    hostname = socket.gethostname()
    ip_addresses = {interface: [addr.address for addr in addrs if addr.family == socket.AF_INET and '127.0.0.1' not in addr.address]
                    for interface, addrs in psutil.net_if_addrs().items()}
    return hostname, ip_addresses

def get_default_gateway():
    try:
        route_output = subprocess.run(['route', 'print'], capture_output=True, text=True)
        lines = route_output.stdout.split('\n')
        for line in lines:
            if '0.0.0.0' in line and '*' not in line:
                return line.split()[3]
    except Exception as e:
        print(f"Error getting default gateway: {e}")
        return None

def get_dns_servers():
    try:
        resolv_output = subprocess.run(['ipconfig', '/all'], capture_output=True, text=True)
        lines = resolv_output.stdout.split('\n')
        dns_servers = [line.split(': ')[1].strip() for line in lines if 'DNS Servers' in line]
        return dns_servers
    except Exception as e:
        print(f"Error getting DNS servers: {e}")
        return None

def get_public_ip():
    try:
        return requests.get('https://api.ipify.org').text
    except Exception as e:
        print(f"Error getting public IP: {e}")
        return None

def get_mac_addresses():
    mac_addresses = {interface: addr.address for interface, addrs in psutil.net_if_addrs().items() if 'link/ether' in [a.family for a in addrs]}
    return mac_addresses

def main():
    hostname, ip_addresses = get_hostname_and_ip_addresses()
    default_gateway = get_default_gateway()
    dns_servers = get_dns_servers()
    public_ip = get_public_ip()
    mac_addresses = get_mac_addresses()

    report = f"""
Hostname: {hostname}
IP Addresses:
{ip_addresses}

Default Gateway: {default_gateway}
DNS Servers:
{dns_servers}

Public IP: {public_ip}
MAC Addresses:
{mac_addresses}
"""

    print(report)

if __name__ == "__main__":
    main()