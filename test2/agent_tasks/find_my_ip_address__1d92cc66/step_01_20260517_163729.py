import socket

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
    except Exception as e:
        print(f"An error occurred: {e}")
        local_ip = None
    finally:
        s.close()
    
    return local_ip

if __name__ == "__main__":
    local_ip = get_ip_address()
    if local_ip is not None:
        print(f"Your local IP address is: {local_ip}")