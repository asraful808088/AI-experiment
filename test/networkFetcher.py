#!/usr/bin/env python3
"""
Network Fetcher - Fetch all network-related information using only built-in libraries
No external dependencies (only socket, subprocess, platform, etc.)
"""

import socket
import subprocess
import json
import platform
import re
from datetime import datetime
import sys

class NetworkFetcher:
    """Fetch all network-related information using built-in libraries only"""
    
    def __init__(self):
        self.system = platform.system()
        self.info = {}
    
    def get_all_network_info(self):
        """Get all network information as dictionary"""
        self.info = {
            "timestamp": datetime.now().isoformat(),
            "system": self.system,
            "hostname": self._get_hostname(),
            "ip_addresses": self._get_ip_addresses(),
            "network_interfaces": self._get_network_interfaces(),
            "active_connections": self._get_active_connections(),
            "dns_info": self._get_dns_info(),
            "gateway_info": self._get_gateway_info(),
            "public_ip": self._get_public_ip(),
            "wifi_info": self._get_wifi_info(),
            "open_ports": self._get_open_ports(),
            "arp_table": self._get_arp_table(),
            "network_stats": self._get_network_stats()
        }
        return self.info
    
    def _get_hostname(self):
        """Get hostname"""
        return {
            "hostname": socket.gethostname(),
            "fqdn": socket.getfqdn()
        }
    
    def _get_ip_addresses(self):
        """Get all IP addresses"""
        ips = {"ipv4": [], "ipv6": []}
        
        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                if '.' in ip:
                    ips["ipv4"].append(ip)
                else:
                    ips["ipv6"].append(ip)
        except:
            pass
        
        # Get IP from network interfaces
        try:
            if self.system == "Windows":
                result = subprocess.run(['ipconfig'], capture_output=True, text=True, shell=True)
                for line in result.stdout.split('\n'):
                    if 'IPv4' in line or 'IPv6' in line:
                        ip_match = re.search(r'\d+\.\d+\.\d+\.\d+', line)
                        if ip_match and ip_match.group() not in ips["ipv4"]:
                            ips["ipv4"].append(ip_match.group())
                    elif 'IPv6' in line:
                        ip_match = re.search(r'[a-f0-9:]+', line)
                        if ip_match and ip_match.group() not in ips["ipv6"]:
                            ips["ipv6"].append(ip_match.group())
            else:
                result = subprocess.run(['ifconfig'], capture_output=True, text=True, shell=True)
                for line in result.stdout.split('\n'):
                    if 'inet ' in line and not '127.0.0.1' in line:
                        ip_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', line)
                        if ip_match and ip_match.group(1) not in ips["ipv4"]:
                            ips["ipv4"].append(ip_match.group(1))
                    elif 'inet6 ' in line:
                        ip_match = re.search(r'inet6 ([a-f0-9:]+)', line)
                        if ip_match and ip_match.group(1) not in ips["ipv6"]:
                            ips["ipv6"].append(ip_match.group(1))
        except:
            pass
        
        return ips
    
    def _get_network_interfaces(self):
        """Get network interfaces"""
        interfaces = []
        
        try:
            if self.system == "Windows":
                result = subprocess.run(['ipconfig', '/all'], capture_output=True, text=True, shell=True)
                current_interface = {}
                
                for line in result.stdout.split('\n'):
                    if 'adapter' in line.lower():
                        if current_interface:
                            interfaces.append(current_interface)
                        current_interface = {"name": line.strip(), "details": []}
                    elif current_interface and line.strip():
                        current_interface["details"].append(line.strip())
                
                if current_interface:
                    interfaces.append(current_interface)
            else:
                result = subprocess.run(['ifconfig'], capture_output=True, text=True, shell=True)
                current_interface = {}
                
                for line in result.stdout.split('\n'):
                    if line and not line.startswith(' '):
                        if current_interface:
                            interfaces.append(current_interface)
                        current_interface = {"name": line.split(':')[0], "details": []}
                    elif current_interface and line.strip():
                        current_interface["details"].append(line.strip())
                
                if current_interface:
                    interfaces.append(current_interface)
        except:
            pass
        
        return interfaces
    
    def _get_active_connections(self):
        """Get active network connections"""
        connections = {"tcp": [], "udp": []}
        
        try:
            if self.system == "Windows":
                result = subprocess.run(['netstat', '-an'], capture_output=True, text=True, shell=True)
            else:
                result = subprocess.run(['netstat', '-an'], capture_output=True, text=True, shell=True)
            
            for line in result.stdout.split('\n'):
                if 'TCP' in line or 'tcp' in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        connections["tcp"].append({
                            "local": parts[1] if len(parts) > 1 else "N/A",
                            "remote": parts[2] if len(parts) > 2 else "N/A",
                            "state": parts[3] if len(parts) > 3 else "N/A"
                        })
                elif 'UDP' in line or 'udp' in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        connections["udp"].append({
                            "local": parts[1] if len(parts) > 1 else "N/A",
                            "remote": "*:*" if len(parts) > 2 else "N/A"
                        })
        except:
            pass
        
        return connections
    
    def _get_dns_info(self):
        """Get DNS information"""
        dns_info = {"nameservers": [], "search_domains": []}
        
        try:
            if self.system == "Windows":
                result = subprocess.run(['ipconfig', '/all'], capture_output=True, text=True, shell=True)
                for line in result.stdout.split('\n'):
                    if 'DNS Servers' in line:
                        dns_match = re.search(r'\d+\.\d+\.\d+\.\d+', line)
                        if dns_match:
                            dns_info["nameservers"].append(dns_match.group())
            else:
                try:
                    with open('/etc/resolv.conf', 'r') as f:
                        for line in f:
                            if line.startswith('nameserver'):
                                dns_info["nameservers"].append(line.split()[1])
                            elif line.startswith('search'):
                                dns_info["search_domains"].extend(line.split()[1:])
                except:
                    pass
        except:
            pass
        
        return dns_info
    
    def _get_gateway_info(self):
        """Get default gateway"""
        gateways = {"ipv4": [], "ipv6": []}
        
        try:
            if self.system == "Windows":
                result = subprocess.run(['route', 'print', '0.0.0.0'], capture_output=True, text=True, shell=True)
                for line in result.stdout.split('\n'):
                    if '0.0.0.0' in line and '0.0.0.0' in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            gateways["ipv4"].append({
                                "gateway": parts[2] if len(parts) > 2 else "N/A",
                                "interface": parts[3] if len(parts) > 3 else "N/A"
                            })
                            break
            else:
                result = subprocess.run(['ip', 'route', 'show', 'default'], capture_output=True, text=True, shell=True)
                for line in result.stdout.split('\n'):
                    if 'default' in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            gateways["ipv4"].append({
                                "gateway": parts[2] if len(parts) > 2 else "N/A",
                                "interface": parts[4] if len(parts) > 4 else "N/A"
                            })
        except:
            pass
        
        return gateways
    
    def _get_public_ip(self):
        """Get public IP using socket (no requests library)"""
        public_ip_info = {"ipv4": None, "location": None, "isp": None}
        
        try:
            # Using socket to get public IP
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("api.ipify.org", 80))
            s.send(b"GET / HTTP/1.0\r\nHost: api.ipify.org\r\n\r\n")
            response = s.recv(1024).decode()
            s.close()
            
            ip_match = re.search(r'\d+\.\d+\.\d+\.\d+', response)
            if ip_match:
                public_ip_info["ipv4"] = ip_match.group()
        except:
            try:
                # Alternative method
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(("ifconfig.me", 80))
                s.send(b"GET /ip HTTP/1.0\r\nHost: ifconfig.me\r\n\r\n")
                response = s.recv(1024).decode()
                s.close()
                
                ip_match = re.search(r'\d+\.\d+\.\d+\.\d+', response)
                if ip_match:
                    public_ip_info["ipv4"] = ip_match.group()
            except:
                public_ip_info["ipv4"] = "Unable to fetch"
        
        return public_ip_info
    
    def _get_wifi_info(self):
        """Get WiFi information"""
        wifi_info = {"ssid": None, "signal_strength": None}
        
        try:
            if self.system == "Windows":
                result = subprocess.run(['netsh', 'wlan', 'show', 'interfaces'], capture_output=True, text=True, shell=True)
                for line in result.stdout.split('\n'):
                    if 'SSID' in line and 'BSSID' not in line:
                        wifi_info["ssid"] = line.split(':')[-1].strip()
                    elif 'Signal' in line:
                        wifi_info["signal_strength"] = line.split(':')[-1].strip()
        except:
            pass
        
        return wifi_info
    
    def _get_open_ports(self):
        """Get open ports"""
        open_ports = []
        
        try:
            result = subprocess.run(['netstat', '-an'], capture_output=True, text=True, shell=True)
            for line in result.stdout.split('\n'):
                if 'LISTEN' in line or 'LISTENING' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        port_match = re.search(r':(\d+)', parts[1])
                        if port_match:
                            port = int(port_match.group(1))
                            if port not in open_ports:
                                open_ports.append(port)
        except:
            pass
        
        return sorted(open_ports)[:50]  # Return first 50 ports
    
    def _get_arp_table(self):
        """Get ARP table"""
        arp_table = []
        
        try:
            if self.system == "Windows":
                result = subprocess.run(['arp', '-a'], capture_output=True, text=True, shell=True)
                lines = result.stdout.split('\n')[3:]
                
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 3 and '.' in parts[0]:
                        arp_table.append({
                            "ip": parts[0],
                            "mac": parts[1],
                            "type": parts[2]
                        })
            else:
                result = subprocess.run(['arp', '-n'], capture_output=True, text=True, shell=True)
                lines = result.stdout.split('\n')[1:]
                
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 3:
                        arp_table.append({
                            "ip": parts[0] if len(parts) > 0 else "N/A",
                            "mac": parts[2] if len(parts) > 2 else "N/A",
                            "type": parts[3] if len(parts) > 3 else "N/A"
                        })
        except:
            pass
        
        return arp_table[:20]  # Return first 20 entries
    
    def _get_network_stats(self):
        """Get network statistics"""
        stats = {}
        
        try:
            if self.system == "Windows":
                result = subprocess.run(['netstat', '-e'], capture_output=True, text=True, shell=True)
                for line in result.stdout.split('\n'):
                    if 'Bytes' in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            stats["bytes_sent"] = parts[1] if len(parts) > 1 else "N/A"
                            stats["bytes_received"] = parts[2] if len(parts) > 2 else "N/A"
            else:
                result = subprocess.run(['netstat', '-i'], capture_output=True, text=True, shell=True)
                for line in result.stdout.split('\n')[1:]:
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 8:
                            stats[parts[0]] = {
                                "rx_bytes": parts[2] if len(parts) > 2 else "N/A",
                                "tx_bytes": parts[6] if len(parts) > 6 else "N/A"
                            }
        except:
            pass
        
        return stats
    
    def to_json(self, pretty=True):
        """Return network info as JSON string"""
        if pretty:
            return json.dumps(self.info, indent=2, default=str)
        return json.dumps(self.info, default=str)
    
    def to_dict(self):
        """Return network info as dictionary"""
        return self.info
    
    def to_string(self):
        """Return formatted string without printing"""
        if not self.info:
            self.get_all_network_info()
        
        lines = []
        lines.append("=" * 80)
        lines.append("NETWORK INFORMATION")
        lines.append("=" * 80)
        lines.append(f"Timestamp: {self.info.get('timestamp', 'N/A')}")
        lines.append(f"System: {self.info.get('system', 'N/A')}")
        lines.append(f"Hostname: {self.info.get('hostname', {}).get('hostname', 'N/A')}")
        lines.append(f"FQDN: {self.info.get('hostname', {}).get('fqdn', 'N/A')}")
        lines.append("")
        
        # IP Addresses
        lines.append("-" * 40)
        lines.append("IP ADDRESSES:")
        lines.append("-" * 40)
        for ip in self.info.get('ip_addresses', {}).get('ipv4', []):
            lines.append(f"  IPv4: {ip}")
        for ip in self.info.get('ip_addresses', {}).get('ipv6', []):
            lines.append(f"  IPv6: {ip[:50]}...")
        
        # Gateway
        lines.append("")
        lines.append("-" * 40)
        lines.append("GATEWAY:")
        lines.append("-" * 40)
        for gw in self.info.get('gateway_info', {}).get('ipv4', []):
            lines.append(f"  Gateway: {gw.get('gateway', 'N/A')}")
            lines.append(f"  Interface: {gw.get('interface', 'N/A')}")
        
        # DNS
        lines.append("")
        lines.append("-" * 40)
        lines.append("DNS SERVERS:")
        lines.append("-" * 40)
        for dns in self.info.get('dns_info', {}).get('nameservers', []):
            lines.append(f"  {dns}")
        
        # Public IP
        lines.append("")
        lines.append("-" * 40)
        lines.append("PUBLIC IP:")
        lines.append("-" * 40)
        lines.append(f"  {self.info.get('public_ip', {}).get('ipv4', 'N/A')}")
        
        # WiFi
        wifi = self.info.get('wifi_info', {})
        if wifi.get('ssid'):
            lines.append("")
            lines.append("-" * 40)
            lines.append("WIFI:")
            lines.append("-" * 40)
            lines.append(f"  SSID: {wifi.get('ssid', 'N/A')}")
            lines.append(f"  Signal: {wifi.get('signal_strength', 'N/A')}")
        
        # Connections
        lines.append("")
        lines.append("-" * 40)
        lines.append("ACTIVE CONNECTIONS:")
        lines.append("-" * 40)
        tcp_count = len(self.info.get('active_connections', {}).get('tcp', []))
        udp_count = len(self.info.get('active_connections', {}).get('udp', []))
        lines.append(f"  TCP: {tcp_count}")
        lines.append(f"  UDP: {udp_count}")
        
        # Open Ports
        open_ports = self.info.get('open_ports', [])
        if open_ports:
            lines.append("")
            lines.append("-" * 40)
            lines.append("OPEN PORTS (first 20):")
            lines.append("-" * 40)
            for port in open_ports[:20]:
                lines.append(f"  {port}")
        
        # ARP Table
        arp = self.info.get('arp_table', [])
        if arp:
            lines.append("")
            lines.append("-" * 40)
            lines.append("ARP TABLE (first 10):")
            lines.append("-" * 40)
            for entry in arp[:10]:
                lines.append(f"  {entry.get('ip', 'N/A')} -> {entry.get('mac', 'N/A')}")
        
        lines.append("=" * 80)
        
        return "\n".join(lines)

def getNetworkInfo(config):
    fetcher = NetworkFetcher()
    fetcher.get_all_network_info()
    json_output = fetcher.to_json()
    formatted_output = fetcher.to_string()
    return f'{json_output}\n{formatted_output}'






