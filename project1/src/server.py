import fastapi
import json
import uvicorn
import time
import os
import threading
import logging
from blockchain import Account, Transaction
from utils import get_current_time
import numpy as np
from pprint import pprint
from typing import List

with open('config.json') as f:
    CONFIG = json.load(f)

logging.basicConfig(
    level=logging.INFO, filename='../result/activity.log',
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)


class BankServer:
    """Bank server"""
    __instance = None  # Singleton pattern

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super().__new__(cls, *args, **kwargs)
        return cls.__instance

    def __init__(self) -> None:
        self.accounts: List[Account] = []  # Account information for clients
        self.transactions: List[Transaction] = []  # Transaction records
        self.clients = {}  # Client addresses: {id1: addr1, id2: addr2, ...}
        self.ipv4 = CONFIG['HOST_IPv4']
        self.port = CONFIG['HOST_PORT']

        self.router = fastapi.APIRouter()
        self.router.add_api_route('/', self.root, methods=['GET'])
        self.router.add_api_route('/register', self.register, methods=['GET'])
        self.router.add_api_route('/balance/{client_id}', self.balance, methods=['GET'])
        self.router.add_api_route('/transfer', self.transfer, methods=['POST'])
        self.router.add_api_route('/exit/{client_id}', self.shutdown_client, methods=['GET'])

    ########################################################################################################
    # ordinary functions
    ########################################################################################################
    def prompt(self):
        """Prompt for commands"""
        time.sleep(1)
        print('Welcome to the blockchain bank server!')
        print('Commands:')
        print('  1. print_table (or print, p)')
        print('  2. exit (or quit, q)')
        print('Enter a command:')

    def interact(self):
        """Interact with the server"""
        while True:
            cmd = input('>>> ').strip()
            if cmd in ['exit', 'quit', 'q']:
                print('Exiting...')
                os._exit(0)
            elif cmd in ['print_table', 'print', 'p']:
                self.print_balance_table()
            elif cmd == 'clients':
                print(self.clients)
            else:
                print('Invalid command')
            print()

    def print_balance_table(self):
        """Print the balance table"""
        self._check_account_info()
        print('client_id\tbalance\trecent_access_time')
        for account in self.accounts:
            print('        {}\t{}\t{}'.format(account.id, account.balance, account.recent_access_time))

    def __repr__(self) -> str:
        return 'BankServer(num_accounts={}, num_transactions={})'.format(len(self.accounts), len(self.transactions))

    def _check_account_info(self):
        # total = sum([account.balance for account in self.accounts])
        # assert total == 10 * len(self.accounts) # unnecessarily satisfied once any client shutdown
        assert len(self.accounts) == len(self.clients)
        assert len(np.unique([account.id for account in self.accounts])) == len(self.accounts)

    ########################################################################################################
    # view functions
    ########################################################################################################
    async def root(self):
        return {'message': 'Welcome to the blockchain bank server!'}

    async def balance(self, client_id: int):
        """Get the balance of a client"""
        self._check_account_info()
        account = [account for account in self.accounts if account.id == client_id][0]
        account.recent_access_time = get_current_time()
        return {'balance': account.balance}

    async def register(self):
        """Register a new client"""
        self._check_account_info()
        ids = [account.id for account in self.accounts]
        if ids:
            client_id = max(ids) + 1
        else:
            client_id = 1
        self.accounts.append(Account(id=client_id))
        print('New client: {}, now all clients:'.format(client_id))
        pprint(self.clients)
        other_clients = self.clients.copy()
        self.clients.update({client_id: 'http://{}:{}'.format(CONFIG['HOST_IPv4'], CONFIG['HOST_PORT'] + client_id)})

        return {
            'client_id': client_id,
            'other_clients': other_clients,
            'server_addr': f'http://{self.ipv4}:{self.port}'
        }

    async def transfer(self, transaction: Transaction):
        """Transfer money from sender to recipient"""
        self._check_account_info()
        sender = [account for account in self.accounts if account.id == transaction.sender_id][0]
        recipient = [account for account in self.accounts if account.id == transaction.recipient_id][0]
        sender.recent_access_time = get_current_time()
        recipient.recent_access_time = sender.recent_access_time
        sender.balance -= transaction.amount
        recipient.balance += transaction.amount
        self.transactions.append(transaction)
        logging.info('Bank server record updated: Client {} transferring {} to client {}'.format(
            transaction.sender_id, transaction.amount, transaction.recipient_id
        ))
        return {'result': 'success'}

    async def shutdown_client(self, client_id: int):
        """Shutdown a client"""
        self.clients.pop(client_id)
        self.accounts = [account for account in self.accounts if account.id != client_id]
        print('Client {} shutdown, now all clients:'.format(client_id))
        pprint(self.clients)
        return {'result': 'success'}


if __name__ == '__main__':
    server = BankServer()
    app = fastapi.FastAPI()
    app.include_router(server.router)
    threading.Thread(target=uvicorn.run, kwargs={
        'app': app,
        'host': server.ipv4,
        'port': server.port,
        'log_level': 'warning'
    }).start()
    print('Bank server:', server)
    server.prompt()
    server.interact()
