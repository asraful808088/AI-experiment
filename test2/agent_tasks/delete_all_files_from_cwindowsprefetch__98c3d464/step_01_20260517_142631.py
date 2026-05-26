import os

# Define the directory path where the files are located
prefetch_dir = r"C:\Windows\Prefetch"

try:
    # Check if the directory exists
    if not os.path.exists(prefetch_dir):
        print("Directory does not exist.")
        exit()

    # List all items in the specified path
    items = os.listdir(prefetch_dir)

    # Iterate over each item in the directory
    for item in items:
        full_path = os.path.join(prefetch_dir, item)
        try:
            if os.path.isfile(full_path):
                print(f"Deleting file: {full_path}")
                os.remove(full_path)
            elif os.path.isdir(full_path) and not os.listdir(full_path):  # Check if directory is empty
                print(f"Deleting directory: {full_path}")
                os.rmdir(full_path)
        except PermissionError:
            print(f"Permission denied for {full_path}. Skipping.")
        except Exception as e:
            print(f"An error occurred while deleting {full_path}: {e}")

    # Confirmation message
    print("All files and directories have been deleted.")

except Exception as e:
    print(f"An unexpected error occurred: {e}")