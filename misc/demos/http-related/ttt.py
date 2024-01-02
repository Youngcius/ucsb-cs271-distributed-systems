import requests

res = requests.get("http://localhost:8000/hello")

print(res.json())
