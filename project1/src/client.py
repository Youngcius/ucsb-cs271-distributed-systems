import os
import re
import json
import time
import fastapi
import uvicorn
import logging
import requests
import grequests
import warnings
import threading
import timeloop
from typing import List
from datetime import timedelta
from utils import get_current_time
from blockchain import BlockChain, Transaction
from fastapi import Body

with open('config.json') as f:
    CONFIG = json.load(f)

logging.basicConfig(
    level=logging.INFO, filename='../result/activity.log',
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)

BALANCE_DELAY = 2  # seconds
TRANSFER_DELAY = 3  # seconds


class Client:
    """Each user can be regarded as a Client instance"""

    def __init__(self, id, ipv4, port, server_addr, other_clients) -> None:
        self.id = id
        self.ipv4 = ipv4
        self.port = port
        self.logic_clock = 0
        self.create_time = get_current_time()
        self.server_addr = server_addr  # e.g., http://127.0.0.1:8000'
        self.other_clients = other_clients  # other clients: {id1: addr1, id2: addr2, ...}
        self.chain = BlockChain()  # each client holds a blockchain
        self.sending_queue: List[Transaction] = []  # sending queue for each client
        self.message_queue: List[Transaction] = []  # processed message queue for each client

    def start(self):
        """Start the client (FastAPI Web application)"""
        threading.Thread(target=uvicorn.run, kwargs={
            'app': app,
            'host': self.ipv4,
            'port': self.port,
            'log_level': 'warning'
        }).start()

    def sending_queue_str(self):
        # return ['Transaction(sender_logic_clock={}, sender_id={}, recipient_id={}, amount={}, status={})'.format(
        #     trans.sender_logic_clock, trans.sender_id, trans.recipient_id, trans.amount, trans.status
        # ) for trans in self.sending_queue]
        return ['Transaction({})'.format(trans.to_tuple()) for trans in self.sending_queue]

    def message_queue_str(self):
        # return ['Transaction(sender_logic_clock={}, sender_id={}, recipient_id={}, amount={}, status={})'.format(
        #     trans.sender_logic_clock, trans.sender_id, trans.recipient_id, trans.amount, trans.status
        # ) for trans in self.message_queue]
        return ['Transaction({})'.format(trans.to_tuple()) for trans in self.message_queue]

    def all_info(self):
        return """
        {}
            other_clients={},
            sending_queue={},
            message_queue={},
        """.format(self, self.other_clients,
                   self.sending_queue_str(),
                   self.message_queue_str())

    def __repr__(self) -> str:
        return 'Client(id={}, ipv4={}, port={}, logic_clock={}, create_time)'.format(self.id, self.ipv4, self.port,
                                                                                     self.logic_clock, self.create_time)

    def interact(self):
        """Begin user interacting"""
        self.prompt()
        while True:
            cmd = input('>>> ').strip().lower()
            if re.match(r'(exit|quit|q)$', cmd):
                print('Exiting...')
                self.shutdown()
                os._exit(0)
            elif re.match(r'^(transfer|t)\s+\d+\s+\d+(\.\d+)?$', cmd):
                try:
                    recipient, amount = cmd.split()[1:]
                except ValueError:
                    print('Invalid transfer command')
                    continue
                self.transfer(int(recipient), float(amount))
            elif re.match(r'(balance|bal|b)$', cmd):
                print('Client {}, Balance {}'.format(self.id, self.balance()))
            elif re.match(r'(print\s+chain|print_chain|print|p)$', cmd):
                self.chain.display()
            elif re.match(r'(all|printall|print_all|print\s+all)$', cmd):
                print(self.all_info())
            elif re.match(r'(m|msg|message)$', cmd):
                print('Sending queue: ', self.sending_queue_str())
                print('Processed message queue: ', self.message_queue_str())
            else:
                print('Invalid command')
            print()

    def prompt(self):
        """Prompt the user for input"""
        time.sleep(1)
        print('Welcome to the blockchain client!')
        print('Commands:')
        print('  1. transfer <recipient> <amount> (e.g., transfer 2 100, or, t 2 100)')
        print('  2. balance (or bal, b)')
        print('  3. print_chain (or print, p)')
        print('  4. exit (or quit, q)')
        print('  5. all (print detailed info of the client)')
        print('  6. msg (or m; print message queue)')
        print('Enter a command:')

    def request_transaction(self):
        """send "request" and expect to receive "reply" from each other client"""
        # print(self.sending_queue[-1])
        print('sending requests to other clients...:', self.other_clients)
        self.message_queue.append(self.sending_queue[-1])
        self.message_queue.sort(key=lambda trans: (trans.sender_logic_clock, trans.sender_id))
        res_list = grequests.map([
            grequests.post(
                client_addr + '/transfer-request',
                json=self.sending_queue[-1].dict()
            ) for client_addr in self.other_clients.values()
        ])
        assert all([res.status_code == 200 for res in res_list])
        for res, client_id in zip(res_list, self.other_clients.keys()):
            logging.info('Client {} sends request to client {}'.format(self.id, client_id))
            logging.info('{} Transaction {} request received by client {}; now num_replies={}'.format(
                res.status_code,
                self.sending_queue[-1].to_tuple(),
                client_id,
                self.sending_queue[-1].num_replies + 1)
            )
            if res.json()['result'] == 'success':
                self.sending_queue[-1].num_replies += 1

    def finish_transaction(self):
        """send "release" to pop the message from the queue of each other client"""
        res_list = grequests.map([
            grequests.post(
                client_addr + '/transfer-finish',
                json=self.sending_queue[0].dict()
            ) for client_addr in self.other_clients.values()
        ])
        assert all([res.status_code == 200 for res in res_list])
        for client_id in self.other_clients.keys():
            logging.info('Client {} sends transaction release (finished) to client {}'.format(self.id, client_id))
        print('Client {} finished transaction {}'.format(self.id, self.sending_queue[0]))

    def transfer(self, recipient_id, amount):
        """Transfer transaction via HTTP request (Lamport distributed protocol)"""
        if recipient_id not in self.other_clients:
            warnings.warn('Recipient {} not in other clients'.format(recipient_id))
            return
        if amount < 0:
            warnings.warn('Amount must be positive')
            return

        print('Attempting transferring ${} from {} to {}'.format(amount, self.id, recipient_id))

        # 1. mutual exclusion checking
        self.logic_clock += 1  # send event
        self.sending_queue.append(Transaction(
            sender_id=self.id,
            recipient_id=recipient_id,
            amount=amount,
            sender_logic_clock=self.logic_clock
        ))
        print('Client {} broadcasts transfer requests to {}; logic clock +1, now {}'.format(
            self.id, list(self.other_clients.keys()), self.logic_clock
        ))
        logging.info('Client {} broadcasts transfer requests to {}; logic clock +1, now {}'.format(
            self.id, list(self.other_clients.keys()), self.logic_clock
        ))
        self.request_transaction()  # add it to message queue, then TimeLoop will automatically process it

    def balance(self, for_transfer=False):
        """Balance transaction via HTTP request"""
        res = requests.get(self.server_addr + '/balance/{}'.format(self.id))
        assert res.status_code == 200
        if not for_transfer:
            self.logic_clock += 1  # local event
        logging.info('Client {} checked balance {}; logic clock +1, now {}'.format(self.id, res.json()['balance'],
                                                                                   self.logic_clock))
        print('Client {} logic clock +1, now {}'.format(self.id, self.logic_clock))
        return res.json()['balance']

    def shutdown(self):
        """Shutdown the client"""
        # update this shutdown client info to the server
        res = requests.get(self.server_addr + '/exit/{}'.format(self.id))
        assert res.status_code == 200

        # update this shutdown client info to all other clients

        res_list = grequests.map([
            grequests.get(
                other_client_addr + '/exit/{}'.format(self.id)
            ) for other_client_addr in self.other_clients.values()
        ])
        assert all([res.status_code == 200 for res in res_list])
        logging.info('Client {} exit the system'.format(self.id))


def register_client(server_addr) -> Client:
    """Register a new client with the server"""
    print('Registering client to server {}...'.format(server_addr))
    print('Registering client to server {}...'.format(server_addr))
    print('Registering client to server {}...'.format(server_addr))
    res = requests.get(server_addr + '/register')
    assert res.status_code == 200

    # create a new client
    info = res.json()
    client_id = info['client_id']
    client_addr = 'http://{}:{}'.format(CONFIG['HOST_IPv4'], CONFIG['HOST_PORT'] + client_id)
    other_clients = info['other_clients']
    other_clients = dict(map(lambda item: (int(item[0]), item[1]), other_clients.items()))
    server_addr = info['server_addr']

    print('Client {} registered'.format(client_id))
    logging.info('Client {} registered'.format(client_id))
    # print('Client address: {}'.format(client_addr))
    # print('Server address: {}'.format(server_addr))
    # print('Other clients: {}'.format(other_clients))

    # update this registered client info to all other clients
    res_list = grequests.map([
        grequests.post(
            other_client_addr + '/register',
            json={'client_id': client_id, 'client_addr': client_addr}
        ) for other_client_addr in other_clients.values()
    ])
    assert all([res.status_code == 200 for res in res_list])
    for other_client_id in other_clients.keys():
        logging.info('Client {} added this system > notifying Client {}'.format(client_id, other_client_id))

    return Client(client_id, CONFIG['HOST_IPv4'], port=CONFIG['HOST_PORT'] + client_id, server_addr=server_addr,
                  other_clients=other_clients)


app = fastapi.FastAPI()
server_address = 'http://{}:{}'.format(CONFIG['HOST_IPv4'], CONFIG['HOST_PORT'])
client = register_client(server_address)
msg_process_loop = timeloop.Timeloop()


@msg_process_loop.job(interval=timedelta(seconds=0.1))
def process_message():
    """Process transaction from the client itself"""
    if not client.message_queue or not client.sending_queue:
        return

    if client.sending_queue[0] == client.message_queue[0] and client.sending_queue[0].num_replies == len(
            client.other_clients):
        # process transaction
        # 2. check balance (transaction will be SUCCESS or ABORT)
        cur_balance = client.balance(for_transfer=True)
        print('Now processing transaction: {}'.format(client.sending_queue[0]))
        print('Before transfer: Client {}, Balance {}'.format(client.id, cur_balance))
        if cur_balance < client.sending_queue[0].amount:
            # 3. if balance is not enough, abort the transaction
            print('Transfer FAILURE (Insufficient balance)')
            logging.info('Transaction {} FAILED (Insufficient balance); added to Client {} blockchain'.format(
                client.sending_queue[0].to_tuple(), client.id
            ))
            logging.info('Client {} broadcasts transaction releases (finished) to {}'.format(
                client.id, list(client.other_clients.keys())
            ))
            client.sending_queue[0].status = 'ABORT'
            client.chain.add_transaction(client.sending_queue[0])
            client.finish_transaction()  # send "release" to every other client

        else:
            # 3. if balance is enough, continue this transaction (to Server and to the other Client)
            res = requests.post(client.server_addr + '/transfer', json=client.sending_queue[0].dict())
            assert res.status_code == 200
            assert res.json()['result'] == 'success'

            logging.info('Transaction {} SUCCESS; added to Client {} blockchain'.format(
                client.sending_queue[0].to_tuple(), client.id
            ))
            logging.info('Client {} broadcasts transaction releases (finished) to {}'.format(
                client.id, list(client.other_clients.keys())
            ))
            client.sending_queue[0].status = 'SUCCESS'
            client.chain.add_transaction(client.sending_queue[0])
            client.finish_transaction()  # send "release" to every other client

            # 4. check balance again
            assert client.balance(for_transfer=True) == cur_balance - client.sending_queue[0].amount
            assert res.status_code == 200
            print('After transfer: Client {}, Balance {}'.format(client.id, client.balance()))
            print('Transfer SUCCESS')
        client.message_queue.remove(client.sending_queue.pop(0))  # "transaction" is unnecessarily at the head


@app.post('/transfer-request')
async def receive_transfer_request(transaction: Transaction):
    """Receive a transfer request from another client"""
    time.sleep(TRANSFER_DELAY)  # random message passing delay to each other client
    client.message_queue.append(transaction)  # put it in to the recipient's queue
    client.message_queue.sort(key=lambda trans: (trans.sender_logic_clock, trans.sender_id))
    logging.info(
        'Client {} received transfer-request {} now message queue: {}'.format(client.id, transaction.to_tuple(),
                                                                              client.message_queue_str()))

    client.logic_clock = max(client.logic_clock, transaction.sender_logic_clock) + 1  # receive event
    print('Received transfer request {} from client {}; logic clock updated, now {}'.format(
        transaction, transaction.sender_id, client.logic_clock
    ))
    logging.info('Client {} received request from client {}, and replies'.format(client.id, transaction.sender_id))
    return {'result': 'success'}


@app.post('/transfer-finish')
async def receive_transfer_finish(transaction: Transaction):
    """Receive a transfer finish from another client"""
    time.sleep(TRANSFER_DELAY)  # random message passing delay to each other client
    print('==' * 20)
    print(transaction.to_tuple())
    print(client.message_queue_str())
    print('==' * 20)
    logging.info('Client {} received transfer-finish {} now message queue: {}'.format(client.id, transaction.to_tuple(),
                                                                                      client.message_queue_str()))

    assert transaction.num_replies == len(client.other_clients)
    client.message_queue.remove(transaction)  # "transaction" is unnecessarily at head; use `remove` instead of  `pop`
    if transaction.status == 'SUCCESS' and transaction.recipient_id == client.id:
        print('Received a transaction: sender {} amount {}'.format(transaction.sender_id, transaction.amount))
        print('Current balance: {}'.format(client.balance()))
    client.chain.add_transaction(transaction)
    logging.info('Client {} add {} to its blockchain'.format(client.id, transaction))


@app.post('/register')
async def add_new_registered_client(client_id: int = Body(..., embed=True), client_addr: str = Body(..., embed=True)):
    """Add a new registered client"""
    client.other_clients.update({client_id: client_addr})
    print('New registered client {} at {} added this system'.format(client_id, client_addr))
    print('Now other clients: {}'.format(client.other_clients))
    return {'result': 'success'}


@app.get('/exit/{client_id}')
async def remove_shutdown_client(client_id: int):
    """Remove a shutdown client"""
    client.other_clients.pop(client_id)
    print('Client {} eix the system'.format(client_id))
    print('Now other clients: {}'.format(client.other_clients))
    return {'result': 'success'}


if __name__ == '__main__':
    print('Registered client with server:')
    print(client)
    client.start()
    # client.prompt()
    msg_process_loop.start()
    client.interact()
