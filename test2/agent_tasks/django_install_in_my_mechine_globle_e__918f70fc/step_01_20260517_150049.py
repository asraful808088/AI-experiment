import subprocess

# Step 1: Check Python Version
def check_python_version():
    try:
        result = subprocess.run(['python', '--version'], capture_output=True, text=True)
        print(f"Python version: {result.stdout.strip()}")
    except Exception as e:
        print(f"Error checking Python version: {e}")

# Step 2: Install Virtualenv
def install_virtualenv():
    try:
        subprocess.run(['pip', 'install', 'virtualenv'], check=True)
        print("Virtualenv installed successfully.")
    except Exception as e:
        print(f"Error installing virtualenv: {e}")

# Step 3: Create a Virtual Environment
def create_virtualenv(env_name):
    try:
        subprocess.run([f'python -m venv {env_name}'], check=True)
        print(f"Virtual environment '{env_name}' created successfully.")
    except Exception as e:
        print(f"Error creating virtual environment: {e}")

# Step 4: Activate the Virtual Environment
def activate_virtualenv(env_name):
    try:
        subprocess.run([f'source {env_name}/bin/activate'], shell=True, check=True)
        print(f"Virtual environment '{env_name}' activated.")
    except Exception as e:
        print(f"Error activating virtual environment: {e}")

# Step 5: Install Django
def install_django():
    try:
        subprocess.run(['pip', 'install', 'django'], check=True)
        print("Django installed successfully.")
    except Exception as e:
        print(f"Error installing Django: {e}")

# Step 6: Verify Django Installation
def verify_django_version():
    try:
        result = subprocess.run(['python', '-m', 'django', '--version'], capture_output=True, text=True)
        print(f"Django version: {result.stdout.strip()}")
    except Exception as e:
        print(f"Error verifying Django version: {e}")

# Step 7: Deactivate the Virtual Environment
def deactivate_virtualenv():
    try:
        subprocess.run(['deactivate'], check=True)
        print("Virtual environment deactivated successfully.")
    except Exception as e:
        print(f"Error deactivating virtual environment: {e}")

# Step 8: Set Global Pip Path (Optional but Recommended)
def set_global_pip_path(env_name):
    try:
        with open('~/.bashrc', 'a') as file:
            file.write(f'\nexport PATH="$PATH:{env_name}/bin"\n')
        subprocess.run(['source ~/.bashrc'], shell=True, check=True)
        print("Global pip path updated successfully.")
    except Exception as e:
        print(f"Error setting global pip path: {e}")

# Execute steps
check_python_version()
install_virtualenv()
create_virtualenv('myenv')
activate_virtualenv('myenv')
install_django()
verify_django_version()
deactivate_virtualenv()
set_global_pip_path('myenv')