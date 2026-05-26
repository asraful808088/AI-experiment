import psutil
import wmi
import os
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Step 1: Install necessary software (already covered via imports)

# Step 2: Gather System Information
def gather_system_info():
    system_info = {
        "cpu": psutil.cpu_count(logical=False),
        "ram": psutil.virtual_memory().total,
        "storage": [disk.mountpoint for disk in psutil.disk_partitions()],
        "gpu": get_gpu_info(),
        "motherboard": None,  # Requires additional tooling like pySMART
        "os_info": {
            "type": os.name,  # Use os.name instead of os.uname()
            "version": None,  # os.uname() is not available on all platforms
            "build_number": None,  # os.uname() is not available on all platforms
            "install_date": psutil.boot_time()  # Time when OS was booted
        }
    }
    return system_info

def get_gpu_info():
    try:
        import pySMART  # Install pySMART using pip: pip install pysmart
        manager = pySMART.Manager()
        for drive in manager.disks:
            if 'GPU' in drive.model_name:
                return {
                    "model": drive.model_name,
                    "serial": drive.serial_number,
                    "temperature": drive.temperature_current
                }
    except Exception as e:
        print(f"Error retrieving GPU information: {e}")
        return None

# Step 3: Gather Configuration Details
def gather_configuration_details():
    # Use a fallback method to list installed software if psutil.pypm is not available
    try:
        from distro import name, version
        installed_software = [(name(), version())]
    except ImportError:
        installed_software = []

    config_details = {
        "network_interfaces": psutil.net_if_addrs(),
        "installed_software": installed_software,
        "active_processes": {proc.info['name']: proc.memory_info().rss / 1024 / 1024 for proc in psutil.process_iter(['name', 'memory_info'])},
        "system_services": [service.name() for service in wmi.WMI().Win32_Service()]
    }
    return config_details

# Step 4: Create a Configuration Report
def create_configuration_report(info, details):
    report = {
        "system_info": info,
        "configuration_details": details
    }
    with open("pc_health_config.json", "w") as file:
        json.dump(report, file, indent=4)

# Step 5: Export the Report (via email)
def export_report_via_email():
    try:
        # Email configuration
        sender_email = "your-email@example.com"
        receiver_email = "recipient-email@example.com"
        password = "your-email-password"

        message = MIMEMultipart()
        message['From'] = sender_email
        message['To'] = receiver_email
        message['Subject'] = 'PC Health and Configuration Report'

        # Attach the JSON report
        with open("pc_health_config.json", "rb") as file:
            attachment = MIMEText(file.read(), "base64")
            attachment.add_header('Content-Disposition', f"attachment; filename=pc_health_config.json")

        server = smtplib.SMTP('smtp.example.com', 587)
        server.starttls()
        server.login(sender_email, password)
        text = message.as_string()
        server.sendmail(sender_email, receiver_email, text)
        print("Report exported via email successfully.")
    except Exception as e:
        print(f"Error exporting report via email: {e}")

# Step 6: Cleanup and Exit
def cleanup():
    # Ensure no resources are left open
    pass

# Main Execution
if __name__ == "__main__":
    system_info = gather_system_info()
    config_details = gather_configuration_details()
    create_configuration_report(system_info, config_details)
    export_report_via_email()
    cleanup()