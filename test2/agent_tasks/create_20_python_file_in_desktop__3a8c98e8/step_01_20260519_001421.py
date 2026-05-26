import os

# Navigate to desktop and create a new directory for python files
os.chdir(os.path.expanduser("~") + "/Desktop/")
if not os.path.exists("python_files"):
    os.mkdir("python_files")
os.chdir(os.path.join(os.getcwd(), "python_files"))

# Create 20 Python files using a loop
for i in range(1, 21):
    file_name = f"file{i}.py"
    with open(file_name, 'w'):
        pass

print("Files created successfully!")