import httpx

def fetch(url):
    # fetches a URL using httpx
    return httpx.get(url)
