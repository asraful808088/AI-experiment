import os
import subprocess

def install_nodejs():
    print("Node.js is not installed. Installing Node.js...")
    subprocess.run(["start", "https://nodejs.org/", "/d"], shell=True)
    input("Press Enter after installing Node.js...")

def create_nextjs_project():
    print("Creating a new Next.js project...")
    try:
        subprocess.run(["npx", "create-next-app@latest", "my-nextjs-app"], check=True, text=True)
        os.chdir("my-nextjs-app")
    except subprocess.CalledProcessError as e:
        print(f"Failed to create Next.js project: {e}")
        return False
    return True

def start_development_server():
    print("Starting the development server...")
    try:
        subprocess.run(["npm", "run", "dev"], check=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to start development server: {e}")

def build_production_project():
    print("Building the project for production...")
    try:
        subprocess.run(["npm", "run", "build"], check=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to build project: {e}")

def start_production_server():
    print("Starting the production server...")
    try:
        subprocess.run(["npm", "start"], check=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to start production server: {e}")

if __name__ == "__main__":
    node_version = subprocess.run(["node", "-v"], capture_output=True, text=True)
    if node_version.returncode != 0 or "v" not in node_version.stdout:
        install_nodejs()

    if create_nextjs_project():
        start_development_server()
        build_production_project()
        start_production_server()