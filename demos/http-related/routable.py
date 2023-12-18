import fastapi
import uvicorn
from classy_fastapi import Routable, get, post


class Demo(Routable):
    def __init__(self, name: str):
        super().__init__()
        self.name = name

    @get("/hello")
    def hello(self, name: str = None):
        return {"message": f"Hello {name or self.name}"}


d = Demo("World")
app = fastapi.FastAPI()
app.include_router(d.router)
uvicorn.run(app, host="localhost", port=8000)
