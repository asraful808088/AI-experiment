import os
import platform
from selenium import webdriver

def browse_webpage(url):
    try:
        # Identify the default web browser on the machine
        if platform.system() == "Windows":
            path = r"C:\Program Files\Internet Explorer\iexplore.exe"
            if os.path.exists(path):
                browser = webdriver.Ie(executable_path=path)
            else:
                print("Default browser is not Internet Explorer")
                return

        elif platform.system() == "Darwin":
            browser = webdriver.Chrome()
        elif platform.system() == "Linux":
            browser = webdriver.Firefox()

        if browser:
            # Navigate to the desired URL
            browser.get(url)

            # Wait for the page to load completely
            browser.implicitly_wait(10)  # Adjust as needed

            # Extract necessary information
            title = browser.title
            print(f"Page Title: {title}")

            text_content = browser.find_element_by_tag_name('body').text
            print("Text Content:")
            print(text_content)

            # Optionally handle user interaction or input
            try:
                search_box = browser.find_element_by_id('search-input')
                search_box.send_keys("Python")
                search_box.submit()
            except Exception as e:
                print(f"Error handling user interaction: {e}")

            # Close the browser session
            browser.quit()

    except Exception as e:
        print(f"An error occurred while browsing: {e}")

# Example usage
browse_webpage("https://www.example.com")