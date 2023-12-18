import asyncio
import requests
import fastapi
import uuid
import uvicorn

app = fastapi.FastAPI()
rand_name = str(uuid.uuid4()).split('-')[0]


@app.get('/')
async def index():
    return {'result': 'success', }


@app.get('/start')
async def start():
    """Start the token passing process"""
    await asyncio.sleep(3)
    return {'result': 'success', 'name': rand_name}


@app.get('/end')
async def end():
    await asyncio.sleep(2)
    return {'result': 'success', 'name': rand_name}


def get_available_port():
    i = 0
    host = 'localhost'
    while True:
        try:
            requests.get('http://{}:{}'.format(host, 8000 + i))
            i += 1
        except requests.ConnectionError:
            return 8000 + i


if __name__ == '__main__':
    uvicorn.run('client-async-test:app', host='localhost', port=get_available_port(), reload=True)
