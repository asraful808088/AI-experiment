import requests
from bs4 import BeautifulSoup

url = "https://www.startech.com.bd/processors/ryzen-3-processors"

try:
    response = requests.get(url)
    response.raise_for_status()  # Raises an HTTPError for bad responses
    print("Successfully retrieved page")
except requests.exceptions.RequestException as e:
    print(f"Failed to retrieve page, error: {e}")
    exit()

soup = BeautifulSoup(response.text, 'html.parser')
prices = soup.find_all('span', class_='price')

price_texts = [price.get_text() for price in prices]

ryzen_3_price = next((price for price in price_texts if "Ryzen 3" in price), None)

if ryzen_3_price:
    print(f"The price of Ryzen 3 processor in Bangladesh is: {ryzen_3_price}")
else:
    print("Ryzen 3 processor not found on the page.")

soup.decompose()