import threading
import time
import fastapi
import asyncio
import uvicorn

from fastapi import FastAPI, APIRouter


class Hello:

    def __init__(self, name: str):
        self.name = name
        self.router = APIRouter()
        self.router.add_api_route("/hello", self.hello, methods=["GET"])

    async def hello(self):
        await asyncio.sleep(3)
        return {"Hello": self.name}


app = FastAPI()
hello = Hello("World (APIRouter)")
app.include_router(hello.router)
threading.Thread(target=uvicorn.run, args=(app,), kwargs={"host": "localhost", "port": 8000}).start()
# uvicorn.run(app, host="localhost", port=8000)

while True:
    time.sleep(3)
    print("Hello World")
