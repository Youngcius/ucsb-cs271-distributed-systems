import socket
import time
import requests
import json


def main():
    client = socket.socket()
    hostname = socket.gethostname()
    port = 6789
    client.connect((hostname, port))
    print(type(client.recv(1024)))
    msg = client.recv(1024).decode('utf-8')
    print(msg)
    client.send('ok，连接成功，合作愉快'.encode('utf-8'))
    client.close()


if __name__ == '__main__':
    main()
