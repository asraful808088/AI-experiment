import requests
from bs4 import BeautifulSoup

url = "https://www.startech.com.bd/processors/ryzen-7-processors"

try:
    response = requests.get(url)
    response.raise_for_status()  # Raises an HTTPError for bad responses
    print("Successfully retrieved page")
else:
    print(f"Failed to retrieve page, status code: {response.status_code}")
    exit()

soup = BeautifulSoup(response.text, 'html.parser')
prices = soup.find_all('span', class_='price')

price_texts = [price.get_text() for price in prices]

ryzen_7_price = next((price for price in price_texts if "Ryzen 7" in price), None)

if ryzen_7_price:
    print(f"The price of Ryzen 7 processor in Bangladesh is: {ryzen_7_price}")
else:
    print("Ryzen 7 processor not found on the page.")

soup.decompose()