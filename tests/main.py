#external 
import pytest 
import requests as re 


#internal
from provider_integrations.tools.main import generate_search_queries
from provider_integrations.tools.main import search_tool, scrape_site

def test_search_tool():
    response = search_tool("What is the capital of France?")
    print(response)
    # assert response is not None
    # assert len(response) > 0
    # assert isinstance(response, list)
    # assert isinstance(response[0], str)
    # assert len(response[0]) > 0
    # assert isinstance(response[0], str)


def test_query_generation():
    response = generate_search_queries("Benson Oil Palm Plantation Ltd")
    return response 

def basic_parse(url: str):
    response = re.get(url)
    return response


if __name__ == "__main__":
    # from pprint import pprint
    # response = test_query_generation()

    # print(response)
    # # print(type(response))

    # for pillar in response['pillars']:
    #     for query in pillar['queries']:
    #         search_results = search_tool(query)
    #         pprint(search_results)
    #         print("\n\n")

    title, body = scrape_site('https://africanfinancials.com/document/gh-bopp-2025-ir-hy/')
    print(title)
    print(body)