import uvicorn
import argparse
import threading
from fastapi import FastAPI
from client import create_client, recover_client


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-r', '--client_to_recover', type=int, default=-1, help='which client to recover')
    args = parser.parse_args()

    if args.client_to_recover >= 0:
        client = recover_client(args.client_to_recover)
    else:
        client = create_client()

    app = FastAPI()
    app.include_router(client.router)
    threading.Thread(target=uvicorn.run, args=(app,), kwargs={
        'host': client.host,
        'port': client.port,
        'log_level': 'warning',
    }).start()
    client.interact()
