from bs4 import BeautifulSoup

def find_next_js_project():
    # Create an account on Next.js' website
    try:
        from bs4 import BeautifulSoup

        response = requests.get('https://nextjs.com/search?q=javascript+search')
        soup = BeautifulSoup(response.content, 'html.parser')

        # Search for projects by category or keyword
        search_results = soup.find_all('div', class_='search-results__item')

        if search_results:
            # Find the first project you want to work with
            for item in search_results:
                title = item.find_all('span')[0].text.strip()
                project_category = item.find_all('span')[1].find_all('a')['href'].split('.')[0]
                project_keyword = item.find_all('span')[1].find_all('a')['href'].split('.')[-2]

                if title and project_category and project_keyword:
                    # Find the project's ID
                    project_id = item.find_all('div')[-3:][0].text.strip()

                    # Search for this project by using regex to match it with keywords
                    search_regex = r'(javascript.+?){1}'

                    project_info = requests.get(f'https://nextjs.com/api/v2/projects/{project_id}?search-regex={search_regex}')

                    if project_info:
                        print(title, ' - ', project_category, ' - ', project_keyword)

        else:
            # No projects match the criteria
            print('No matching projects found.')

    except Exception as e:
        print(f'An error occurred: {e}')

# Run the function to find your next.js project in desktop
find_next_js_project()