"""Take global snapshots of a token-passing application via Chandy-Lamport global snapshot algorithm"""
import os
import re
import uuid
import time
import json
import asyncio
import requests
import grequests
import uvicorn
import logging
import threading
import fastapi
import networkx as nx
from pprint import pprint
from numpy.random import choice
from typing import List, Dict, Tuple
from utils import LocalState, ChannelState, Snapshot, Marker, generate_token

with open("config.json") as f:
    CONFIG = json.load(f)

logging.basicConfig(
    level=logging.INFO, filename='../result/activity.log',
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)

TOKEN_LOST_PROB = CONFIG['TOKEN_LOST_PROB']
MSG_RECV_DELAY = CONFIG['MSG_RECV_DELAY']
TOKEN_HOLDING_TIME = CONFIG['TOKEN_HOLDING_TIME']

layout = nx.read_graphml(CONFIG['LAYOUT_FILE'])
client_addresses = {}
for i, node in enumerate(layout.nodes):
    client_addresses[node] = 'http://{}:{}'.format(CONFIG['HOST_IPv4'], CONFIG['HOST_PORT'] + i)


class Client:
    __instance = None  # Singleton pattern

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super().__new__(cls, *args, **kwargs)
        return cls.__instance

    def __init__(self):
        self.init_layout()
        self.message_queue: List[Tuple[str, str]] = []  # [(label, msg), ...]
        self.channel_msg_buffer: Dict[str, Dict[str, List[str]]] = {}  # {stamp: {src: [msg, ...], ...}, ...}
        self.process_msg_buffer: Dict[str, List[Tuple[str, str]]] = {}  # {stamp: [(label, msg), ...], ...}

        self.has_recorded: Dict[str, bool] = {}  # {stamp: True, ...}, whether the client has recorded its local state for specific global snapshot activities
        self.markers_recv_nums: Dict[str, int] = {}  # {stamp: 0, ...}, the number of markers received from its incoming channels for specific global snapshot activities, it is used to determine whether the client is ready to send back its local snapshot to the initiator
        self.snapshots: List[Snapshot] = []  # each initialized snapshot activity corresponds a snapshot, identified by the `stamp` attribute
        self.global_snapshots: List[Snapshot] = []  # similarly
        self.snapshots_recv_nums: Dict[str, int] = {}  # {stamp: 0, ...}, the number of snapshots received from all other N-1 clients for specific global snapshot activities

        self.stored_tokens = []

        self.router = fastapi.APIRouter()
        self.router.add_api_route('/', self.index, methods=['GET'])
        self.router.add_api_route('/initialization', self.init_global_snapshot_passively, methods=['GET'])
        self.router.add_api_route('/marker', self.recv_marker, methods=['POST'])
        self.router.add_api_route('/message', self.recv_message, methods=['GET'])
        self.router.add_api_route('/snapshot', self.recv_snapshot, methods=['POST'])

    def __repr__(self):
        return 'Client(id={}, name={}, addr={}, outgoing={}, incoming={})'.format(self.id, self.name, self.addr,
                                                                                  self.outgoing, self.incoming)

    def init_layout(self):
        """Occupy a node among the layout"""
        for i, (name, addr) in enumerate(client_addresses.items()):
            try:
                requests.get(addr)
            except requests.ConnectionError:
                self.id = i
                self.name = name
                self.addr = addr
                self.outgoing = sorted(list(layout.neighbors(name)))
                self.incoming = sorted(list(layout.predecessors(name)))
                print("No. {} client initialized the node\n\t{}".format(self.id, self))
                return
        raise ConnectionError("No available node in the layout")

    def display_info(self):
        """Display stored information of the client"""
        print('Client information:', self)
        print('   message queue:', self.message_queue)
        print('   channel message buffer:')
        pprint(self.channel_msg_buffer)
        print('   has_recorded:', self.has_recorded)

    def prompt(self):
        """Output command prompt information"""
        print('Commands:')
        print('  1. <st>: start token passing from this client')
        print('  2. <is>: initiate a global snapshot from this client')
        print('  3. <all>: display all info of current client')
        print('  4. <exit>, <quit>, or <q>: exit the client interface')

    def interact(self):
        """Interact with the token-passing application"""
        time.sleep(1)
        self.prompt()
        while True:
            cmd = input('>>> ').strip().lower()
            if re.match(r'(st|starttoken|start_token|start\s+token)$', cmd):
                token = generate_token(use_input=True)
                print('Token {} generated'.format(token))
                threading.Thread(target=self.send_message, args=(token,)).start()
            elif re.match(r'(is|initsnapshot|init_snapshot|init\s+snapshot)$', cmd):
                self.init_global_snapshot()
            elif re.match(r'(exit|quit|q)$', cmd):
                os._exit(0)
            elif re.match(r'(all|printall|print_all|print\s+all)$', cmd):
                self.display_info()
            else:
                print('Invalid command')
            print()

    async def index(self):
        return {'result': 'success', 'client': client.name}

    def init_global_snapshot(self):
        """Any client can initiate the global snapshot protocol at any point during execution"""
        # related fields initialization
        stamp = '-'.join([self.name, str(uuid.uuid4()).split('-')[0]])
        self.snapshots.append(Snapshot(stamp=stamp, initiator=self.name, owner=self.name))
        self.global_snapshots.append(Snapshot(stamp=stamp, initiator=self.name, owner=self.name))
        self.snapshots_recv_nums[stamp] = 0
        self.has_recorded[stamp] = False
        self.markers_recv_nums[stamp] = 0
        self.channel_msg_buffer[stamp] = {src: [] for src in self.incoming}
        self.process_msg_buffer[stamp] = self.message_queue.copy()

        # notify all other clients to initiate a global snapshot activity by async concurrency but sync blocking
        grequests.map([
            grequests.get(
                addr + '/initialization', params={'stamp': stamp, 'initiator': self.name}
            ) for addr in client_addresses.values() if addr != self.addr
        ])

        logging.info('Client {} initiated a global snapshot with unique stamp [{}]'.format(self.name, stamp))
        print('Client {} initiated a global snapshot with unique stamp [{}]'.format(self.name, stamp))
        threading.Thread(target=self.send_marker,
                         args=(Marker(stamp=stamp, initiator=self.name, src=self.name),)).start()

    async def init_global_snapshot_passively(self, stamp: str, initiator: str):
        """Passively initiate a global snapshot activity after receiving the initiator's notification"""
        self.snapshots.append(Snapshot(stamp=stamp, initiator=initiator, owner=self.name))
        self.has_recorded[stamp] = False
        self.markers_recv_nums[stamp] = 0
        self.channel_msg_buffer[stamp] = {src: [] for src in self.incoming}
        self.process_msg_buffer[stamp] = self.message_queue.copy()
        return {'result': 'success'}

    def reduce_local_states(self, stamp: str):
        """If the last action is "send", means there is no token saved in this client"""
        snapshot = [s for s in self.snapshots if s.stamp == stamp][0]
        if snapshot.local_states:  # not empty
            if snapshot.local_states[-1].label == 'send' or self.process_msg_buffer[stamp][-1][0] == 'send':
                snapshot.local_states.clear()
            else:
                snapshot.local_states = [snapshot.local_states[-1]]

    def send_message(self, token: str):
        """
        Pass the token to the next client
        ---
        1. hold on the token for 1 second
        2. randomly choose one outgoing channel to send the token (with a small probability of losing the token) 
        """
        self.message_queue.append(('send', token))
        dst = choice(self.outgoing)
        logging.info('Client {} sent token {} to {}'.format(self.name, token, dst))
        print('Client {} sent token {} to {}'.format(self.name, token, dst))
        for stamp, recorded in self.has_recorded.items():
            # if not recorded:
            # self.process_msg_buffer[stamp].append(('send', token))
            self.process_msg_buffer[stamp].append(('send', token))
        res = requests.get(client_addresses[dst] + '/message', params={'token': token, 'src': self.name})
        # assert res.json()['result'] == 'success'
        if res.json()['result'] == 'success':
            logging.info('Token {} sent successfully by {} to {}'.format(token, self.name, dst))
            print('Token {} sent successfully by {} to {}'.format(token, self.name, dst))
        else:
            logging.info('Token {} lost when sending from {} to {}'.format(token, self.name, dst))
            print('Token {} lost when sending from {} to {}'.format(token, self.name, dst))

    async def recv_message(self, token: str, src: str):
        """
        Receive and store the token
        ---
        For each potential global snapshot activity, the client should distinguish the following two cases:
        1. if it has not recorded its local state, then just save the token in its self.message_queue
        2. otherwise, it should save the token in the corresponding channel message buffer
        """
        await asyncio.sleep(MSG_RECV_DELAY)

        logging.info('Client {} received token {}'.format(self.name, token))
        print('Client {} received token {}'.format(self.name, token))

        # in emulation, the token may be lost once it is received
        if choice([0, 1], p=[TOKEN_LOST_PROB, 1 - TOKEN_LOST_PROB]) == 0:
            logging.info('Client {} Token {} lost'.format(self.name, token))
            print('Client {} Token {} lost'.format(self.name, token))
            return {'result': 'failure'}

        self.message_queue.append(('recv', token))
        for stamp, recorded in self.has_recorded.items():
            self.process_msg_buffer[stamp].append(('recv', token))
            # if recorded:
            #     self.channel_msg_buffer[stamp][src].append(token)
            # else:
            #     self.process_msg_buffer[stamp].append(('recv', token))
        time.sleep(TOKEN_HOLDING_TIME)
        threading.Thread(target=self.send_message, args=(token,)).start()
        return {'result': 'success'}

    def send_marker(self, marker: Marker):
        """
        Execute "Marker Sending Rules" to initiate the global snapshot protocol
        ---
        1. record its local state
        2. send marker to all its outgoing channels by which markers have not been sent
        """
        snapshot = [s for s in self.snapshots if s.stamp == marker.stamp][0]
        snapshot.local_states.extend([LocalState(node=self.name, label=label, message=msg) for label, msg in
                                      self.process_msg_buffer[marker.stamp]])
        self.has_recorded[marker.stamp] = True

        for dst in self.outgoing:
            logging.info('Stamp: [{}]: Client {} sent marker {} to {}'.format(marker.stamp, self.name, marker, dst))
            print('Stamp: [{}]: Client {} sent marker {} to {}'.format(marker.stamp, self.name, marker, dst))

        grequests.map([grequests.post(client_addresses[dst] + '/marker', json=marker.dict()) for dst in self.outgoing])

    async def recv_marker(self, marker: Marker):
        """
        Execute "Marker Receiving Rules" on receiving a marker
        ---
        1. if it has not recorded its local state
            1) record the channel state as empty
            2) follow the "Marker Sending Rules"
        2. if it has recorded its local state
            1) record the channel state as the set of messages received in the time window [its local state was recorded, the marker was received]
        """
        await asyncio.sleep(MSG_RECV_DELAY)

        logging.info('Stamp [{}]: Client {} received marker {}'.format(marker.stamp, self.name, marker))
        print('Stamp [{}]: Client {} received marker {}'.format(marker.stamp, self.name, marker))
        snapshot = [s for s in self.snapshots if s.stamp == marker.stamp][0]
        if not self.has_recorded[marker.stamp]:
            threading.Thread(target=self.send_marker,
                             args=(Marker(stamp=marker.stamp, initiator=marker.initiator, src=self.name),)).start()
        else:
            for msg in self.channel_msg_buffer[marker.stamp][marker.src]:
                snapshot.channel_states.append(ChannelState(src=marker.src, dst=self.name, message=msg))

        # if all incoming channels received markers, then send snapshot to the initiator
        self.markers_recv_nums[marker.stamp] += 1
        print('Client {}: markers_recv_nums:'.format(self.name), self.markers_recv_nums)
        if self.markers_recv_nums[marker.stamp] == len(self.incoming):
            self.reduce_local_states(marker.stamp)  # reduce local states before adding them to the global snapshot
            if self.name == marker.initiator:
                global_snapshot = [gs for gs in self.global_snapshots if gs.stamp == snapshot.stamp][0]
                global_snapshot.local_states.extend(snapshot.local_states)
                global_snapshot.channel_states.extend(snapshot.channel_states)
                self.finish_global_snapshot(marker.stamp)
            else:
                threading.Thread(target=self.send_snapshot, args=(marker.stamp, marker.initiator)).start()
        return {'result': 'success'}

    def send_snapshot(self, stamp: str, initiator: str):
        """Send its local snapshot (local state & its incoming channels states) to the initiator"""
        snapshot = [s for s in self.snapshots if s.stamp == stamp][0]
        logging.info('Stamp [{}]: Client {} sent its snapshot to {}'.format(stamp, self.name, initiator))
        print('Stamp [{}]: Client {} sent its snapshot to {}'.format(stamp, self.name, initiator))
        print('-' * 20)
        pprint(snapshot)
        print('-' * 20)

        res = requests.post(client_addresses[initiator] + '/snapshot', json=snapshot.dict())
        assert res.status_code == 200

        # local snapshot info is no longer needed
        self.snapshots.remove(snapshot)
        self.has_recorded.pop(stamp)
        self.markers_recv_nums.pop(stamp)
        self.channel_msg_buffer.pop(stamp)

    async def recv_snapshot(self, snapshot: Snapshot):
        """
        Only the initiator of a global snapshot activity will execute this method.
        Theoretically, the method will be executed N-1 times to collect local snapshots from other N-1 clients.
        """
        print(self.global_snapshots)
        for it in self.global_snapshots:
            pprint(it)
        global_snapshot = [gs for gs in self.global_snapshots if gs.stamp == snapshot.stamp][0]
        global_snapshot.local_states.extend(snapshot.local_states)
        global_snapshot.channel_states.extend(snapshot.channel_states)
        self.snapshots_recv_nums[snapshot.stamp] += + 1

        logging.info(
            'Stamp [{}]: Client {} received snapshot from {}'.format(snapshot.stamp, self.name, snapshot.owner))
        print('Stamp [{}]: Client {} received snapshot from {}'.format(snapshot.stamp, self.name, snapshot.owner))
        self.finish_global_snapshot(snapshot.stamp)
        return {'result': 'success'}

    def finish_global_snapshot(self, stamp: str):
        """Try to finish the global snapshot activity."""
        if self.snapshots_recv_nums[stamp] == layout.number_of_nodes() - 1 and self.markers_recv_nums[stamp] == len(
                self.incoming):
            global_snapshot = [gs for gs in self.global_snapshots if gs.stamp == stamp][0]
            logging.info('Stamp [{}]: Global snapshot is constructed completely: \n{}'.format(stamp,
                                                                                              global_snapshot.json(
                                                                                                  indent=4)))
            print('==========================================')
            print('Stamp: [{}]: Global snapshot is constructed completely:'.format(stamp))
            print(global_snapshot.json(indent=4))
            print('==========================================')
            self.has_recorded.pop(stamp)
            self.markers_recv_nums.pop(stamp)
            self.channel_msg_buffer.pop(stamp)


# @app.get('/initialization')
# async def init_global_snapshot_passively(stamp: str, initiator: str):
#     """Initialize a global snapshot activity once the client receives the notification from the initiator"""
#     client.init_global_snapshot_passively(stamp, initiator)
#     return {'result': 'success'}


# @app.post('/marker')
# async def recv_marker(marker: Marker):
#     """Receive a marker from the incoming channel"""
#     await asyncio.sleep(MSG_RECV_DELAY)
#     client.recv_marker(marker)  # "Marker Receiving Rules"
#     return {'result': 'success'}


# @app.get('/message')
# async def recv_message(token:str, src: str):
#     """Receive a token or marker from the incoming channel"""
#     await asyncio.sleep(MSG_RECV_DELAY)
#     if client.recv_message(token, src):  # the message could be lost
#         return {'result': 'success'}
#     return {'result': 'failure'}


# @app.post('/snapshot')
# async def collect_snapshots(snapshot: Snapshot):
#     """Collect the local snapshot from all other clients to construct a global snapshot"""
#     client.recv_snapshot(snapshot)
#     return {'result': 'success'}


if __name__ == "__main__":
    client = Client()
    app = fastapi.FastAPI()
    app.include_router(client.router)
    print(client)
    threading.Thread(target=uvicorn.run, kwargs={
        'app': app,
        'host': client.addr.split(':')[-2].strip('/'),
        'port': int(client.addr.split(':')[-1]),
        'log_level': 'warning'
    }).start()
    client.interact()
