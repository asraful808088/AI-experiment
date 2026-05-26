import requests
from bs4 import BeautifulSoup

url = "https://www.startech.com.bd"

try:
    response = requests.get(url)
    if response.status_code == 200:
        print("Request successful")
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Example: Find all div elements with class 'info-section'
        info_sections = soup.find_all('div', class_='info-section')
        
        for section in info_sections:
            print(section.get_text())
    else:
        print(f"Failed to retrieve data. Status code: {response.status_code}")
except Exception as e:
    print(f"An error occurred: {e}")

# Optionally, save the extracted information to a file
try:
    with open('startech_info.txt', 'w') as file:
        for section in info_sections:
            file.write(section.get_text() + '\n')
except Exception as e:
    print(f"Failed to write to file: {e}")