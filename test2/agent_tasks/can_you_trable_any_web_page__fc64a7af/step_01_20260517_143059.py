import requests
from bs4 import BeautifulSoup

# Define the URL to scrape
url = 'https://www.example.com'

try:
    # Make an HTTP request
    response = requests.get(url)
    
    # Check if the request was successful
    if response.status_code == 200:
        print("Success!")
        
        # Parse the HTML content
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract data from the page
        paragraphs = soup.find_all('p')
        for p in paragraphs:
            print(p.get_text())
            
        # Save extracted data to a text file
        with open('extracted_data.txt', 'w') as file:
            for p in paragraphs:
                file.write(p.get_text() + '\n')
    else:
        print(f"Failed with status code {response.status_code}")
except requests.exceptions.RequestException as e:
    print(f"An error occurred: {e}")