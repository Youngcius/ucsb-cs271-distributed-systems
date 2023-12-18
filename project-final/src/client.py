import os
import re
import time
import json
import pprint
import random
import asyncio
import logging
import warnings
import threading
import grequests
import requests
from fastapi import Body, Query
from classy_fastapi import Routable, get, post
from typing import List, Dict, Set, Tuple, Optional, Union

import utils
from dictionary import IdentityStatus, CommandType, Operation, LogEntry, AppendEntriesData, HeartbeatData


logging.basicConfig(
    level=logging.INFO, filename='../result/activity.log',
    format='%(asctime)s %(levelname)s\t %(name)s %(message)s'
)

logging.info('='*100)

HOST_PORT = 8000
NUM_CLIENTS = 5
NUM_MAJORITY = NUM_CLIENTS // 2 + 1
MSG_RECV_DELAY = 3
TIMEOUT_LOWER_LIMIT = 5
TIMEOUT_UPPER_LIMIT = 12
HEARTBEAT_INTERVAL = 1

client_addresses = {i: ('localhost', HOST_PORT + i) for i in range(NUM_CLIENTS)}


class Client(Routable):
    __instance = None  # Singleton pattern

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super().__new__(cls, *args, **kwargs)
        return cls.__instance

    def __init__(self):
        super().__init__()
        self.id: Optional[int] = None
        self.host: Optional[str] = None
        self.port: Optional[int] = None
        self.timeout = TIMEOUT_LOWER_LIMIT + random.random() * (TIMEOUT_UPPER_LIMIT - TIMEOUT_LOWER_LIMIT)

        # replication-related
        self.dpath: Optional[str] = None
        self.meta_path: Optional[str] = None
        self.log_path: Optional[str] = None
        self.own_keys: Dict[str, str] = {'public': '', 'private': ''}
        self.logs: List[LogEntry] = []  # log entries; each entry contains command for state machine, and term when entry was received by leader (first index is 1)

        # key-related
        self.other_public_keys: Dict[int, str] = {}
        self.other_sites = set()

        # additional persistent state on all servers
        self.status = IdentityStatus.Follower
        self.votes_received = 0
        self.leader_id: Optional[int] = None

        # persistent state on all servers
        self.current_term = 0  # every time when updating current_term, vote_for must be also updated!!!
        self.voted_for: Optional[int] = None

        # volatile state on all servers
        self.commit_index = 0  # index of highest log entry known to be committed (initialized to 0, increases monotonically)
        self.last_applied = 0  # index of highest log entry applied to state machine (initialized to 0, increases monotonically)
        self.heartbeat_listener = threading.Timer(self.timeout, lambda: None)

        # volatile state on the leader
        self.next_indices = {id: self.last_log_index + 1 for id in range(NUM_CLIENTS)}  # for each server, index of the next log entry to send to that server (initialized to leader last log index + 1)
        self.match_indices = {id: 0 for id in range(NUM_CLIENTS)}  # for each server, index of highest log entry known to be replicated on server (initialized to 0, increases monotonically)
        # self.heartbeat_timeloop = timeloop.Timeloop()
        # self.heartbeat_sender = threading.Timer(HEARTBEAT_INTERVAL, self.heartbeat)

        # data-operation-related
        self.counter = 0  # for generating global unique dictionary ID
        self.dict_meta = {}  # {dict_id: {'private': <private key>, 'public': <public key>, 'members': <member client ids>}}
        self.dict_content = {}  # {dict_id: {key: value}}

    def __repr__(self):
        return "Client {} at <{}:{}> ({})".format(self.id, self.host, self.port, self.status)

    ########################################################################################################
    # Identity change
    ########################################################################################################
    def to_leader(self):
        self.status = IdentityStatus.Leader
        self.leader_id = self.id
        self.next_indices = {id: self.last_log_index + 1 for id in range(NUM_CLIENTS)}
        self.match_indices = {id: 0 for id in range(NUM_CLIENTS)}
        logging.info('Client {} Term {} --> Leader'.format(self.id, self.current_term))
        print('Client {} Term {} --> Leader'.format(self.id, self.current_term))
        threading.Thread(target=self.heartbeat).start()

    def to_candidate(self):
        self.status = IdentityStatus.Candidate
        self.voted_for = self.id
        self.votes_received = 1
        self.leader_id = None
        logging.info('Client {} Term {} --> Candidate'.format(self.id, self.current_term))
        print('Client {} Term {} --> Candidate'.format(self.id, self.current_term))

    def to_follower(self):
        self.status = IdentityStatus.Follower
        logging.info('Client {} Term {} --> Follower'.format(self.id, self.current_term))
        print('Client {} Term {}--> Follower'.format(self.id, self.current_term))

    ########################################################################################################
    # Leader election
    ########################################################################################################
    def heartbeat(self):
        """Send heartbeats to all other servers"""
        # logging.info('Client {} sends heartbeats to {}'.format(self.id, self.other_sites))
        self.persist()
        grequests.map([
            grequests.post('http://{}:{}/heartbeat'.format(*client_addresses[id]), json=HeartbeatData(
                leader_id=self.id, term=self.current_term,
                prev_log_index=self.last_log_index, prev_log_term=self.last_log_term,
            ).dict(), hooks={'response': self.heartbeat_hook}) for id in self.other_sites
        ])
        time.sleep(HEARTBEAT_INTERVAL)
        if self.status is IdentityStatus.Leader:
            self.heartbeat()

    def heartbeat_hook(self, response, *args, **kwargs):
        if response.status_code == 200:
            if not response.json()['accepted']:
                self.current_term = response.json()['term']
                print('Leader {} steps down'.format(self.id))
                self.voted_for = None
                self.to_follower()

    @post('/heartbeat')
    async def listen_heartbeat(self, data: HeartbeatData):
        """Accept heartbeat from the leader"""
        self.persist()
        if data.term >= self.current_term:
            self.current_term = data.term
            self.voted_for = None
            if self.status is not IdentityStatus.Follower:
                self.to_follower()
            self.reset_heartbeat_listener()
            self.leader_id = data.leader_id
            return {'accepted': True, 'client_id': self.id, 'term': self.current_term}
        return {'accepted': False, 'client_id': self.id, 'term': self.current_term}

    def reset_heartbeat_listener(self, first_time=False):
        self.heartbeat_listener.cancel()
        if first_time:
            self.heartbeat_listener = threading.Timer(self.timeout, self.sponsor_election, args=(True,))
        else:
            self.heartbeat_listener = threading.Timer(self.timeout, self.sponsor_election)
        self.heartbeat_listener.start()

    def sponsor_election(self, first_time=False):
        """
        Start a new election
        ---
        1) increment current term
        2) change status to candidate; vote for self
        3) send RequestVote RPCs to all other servers, retry until either:
            3.1) if votes received from the majority of servers: become leader; send heartbeats to all other servers
            3.2) if AppendEntries RPC received from new leader: return to follower status
            3.3) if election timeout elapses: start new election
        """
        # if self.status is IdentityStatus.Follower and self.leader_id is not None:
        print('1) Attempting to sponsor election ...', first_time)
        if first_time:
            if self.status is IdentityStatus.Follower and (self.voted_for is not None or self.leader_id is not None):
                return
        print('2) Attempting to sponsor election ...', first_time)
        self.current_term += 1
        self.to_candidate()
        logging.info('Client {} Term {} election starts'.format(self.id, self.current_term))
        print('Client {} Term {} election starts'.format(self.id, self.current_term))
        threading.Timer(self.timeout, self.reelect_or_not).start()
        self.request_vote()

    def reelect_or_not(self):
        if self.status == IdentityStatus.Candidate and self.votes_received < NUM_MAJORITY and self.leader_id is None:
            self.sponsor_election()

    def request_vote(self):
        """
        RequestVote PRC
        ---
        The candidate send request with its log information,
            if the log of the voter is more up-to-date than the candidate's, it will not vote for the candidate
        """
        grequests.map([
            grequests.post('http://{}:{}/request-vote'.format(*client_addresses[id]), json={
                'candidate_id': self.id,
                'term': self.current_term,
                'last_log_term': self.last_log_term,
                'last_log_index': self.last_log_index,
            }, hooks={'response': self.request_vote_hook}) for id in self.other_sites
        ])

    def request_vote_hook(self, response, *args, **kwargs):
        """
        1) if RPC request or response contains term T > currentTerm: set currentTerm = T, convert to follower
        2) if votedFor is null or candidateId, and candidate's log is at least as up-to-date as receiver's log, grant vote
        """
        if self.status is not IdentityStatus.Candidate:
            return
        if response.status_code == 200:
            data = response.json()
            if data['term'] > self.current_term:
                assert not data['vote_granted']
                logging.info('Client {} Term {} election is ended'.format(self.id, self.current_term))
                self.current_term = data['term']
                self.voted_for = None
                print('Client {} Term {} election is ended'.format(self.id, self.current_term))
                self.to_follower()
            elif data['vote_granted']:
                self.votes_received += 1
                if self.votes_received >= NUM_MAJORITY:
                    logging.info('Client {} Term {} election is ended'.format(self.id, self.current_term))
                    print('Client {} Term {} election is ended'.format(self.id, self.current_term))
                    self.to_leader()
        else:
            print('RequestVote RPC failed: {}'.format(response.status_code))

    @post('/request-vote')
    async def request_vote_handler(self, candidate_id: int = Body(..., embed=True), term: int = Body(..., embed=True),
                                   last_log_index: int = Body(..., embed=True), last_log_term: int = Body(..., embed=True)):
        await asyncio.sleep(MSG_RECV_DELAY)
        self.persist()
        if term > self.current_term:
            self.current_term = term
            self.voted_for = candidate_id
            logging.info('<Yes> Client {} Term {} votes for Client {}'.format(self.id, self.current_term, candidate_id))
            self.to_follower()  # step down (turn into Follower) no matter what status it is
            return {'term': self.current_term, 'vote_granted': True}
        elif term == self.current_term:
            if self.status is not IdentityStatus.Leader and \
                    (self.voted_for is None or self.voted_for == candidate_id) and \
                    (last_log_term, last_log_index) >= (self.last_log_term, self.last_log_index):
                self.voted_for = candidate_id
                logging.info('<Yes> Client {} Term {} votes for Client {}'.format(self.id, self.current_term, candidate_id))
                self.to_follower()
                return {'term': self.current_term, 'vote_granted': True}
            else:
                logging.info('<No> Client {} Term {} does not vote for Client {}'.format(self.id, self.current_term, candidate_id))
                return {'term': self.current_term, 'vote_granted': False}
        else:
            logging.info('<No> Client {} Term {} does not vote for Client {}'.format(self.id, self.current_term, candidate_id))
            return {'term': self.current_term, 'vote_granted': False}

    ########################################################################################################
    # Normal operation
    ########################################################################################################
    def respond_to_user(self, opr: Operation) -> dict:
        """
        ---
        1) If there is not leader, elect a new leader and retry
        2) If the user is interacting with a Leader process, perform normal operation and return the result
        3) If the user is interacting with a Follower process, redirect to Leader (Leader will perform normal operation)
            and then return the result
        """
        if self.leader_id is None:
            print('No leader found, electing a new leader...')
            self.sponsor_election()
            return self.respond_to_user(opr)

        # the current process must have access to this dictionary with opr.dict_id
        # it is in charge of capsulating the Operation into a LogEntry
        entry = self.construct_log_entry(opr)

        if self.status is IdentityStatus.Leader:  # perform normal operation as the Leader
            return self.perform_normal_operation(entry)

        print('Redirecting to leader...')
        result = requests.post('http://{}:{}/redirecting'.format(*client_addresses[self.leader_id]), json=entry.dict()).json()
        if opr.cmd is CommandType.Get:
            result.update({'value': self.get_value(entry)})
        return result

    @post('/redirecting')
    async def redirected_as_leader(self, entry: LogEntry):
        print('{} is redirected to Client {}'.format(entry, self.id))
        return self.perform_normal_operation(entry)

    def perform_normal_operation(self, entry: LogEntry) -> dict:
        """
        This method must be executed by the leader
        ---
        1) if command received from client: append entry to local log, respond after entry applied to state machine
        2) if last log index ≥ nextIndex for a follower: send AppendEntries RPC with log entries starting at nextIndex
        3) if successful: update nextIndex and matchIndex for follower
        4) if AppendEntries fails because of log inconsistency: decrement nextIndex and retry
        5) if there exists an N such that N > commitIndex, a majority of matchIndex[i] ≥ N, and log[N].term == currentTerm:
            set commitIndex = N
        """
        assert self.status is IdentityStatus.Leader
        self.append_logs(entry)
        print('Client {} Term {} appended entry: {}'.format(self.id, self.current_term, entry))

        # Phase-1: Preparation
        self.append_entries_for_all()

        if self.update_commit_index():
            result = {'committed': True}
        else:
            result = {'committed': False}

        # Phase 2: Commit
        print('Phase-1 Preparation succeeded; Phase-2 Commit began', self.commit_index > self.last_applied)
        if self.commit_index > self.last_applied:
            result.update(self.pass_to_state_machine_once())  # up to now the result can be returned to the user
        self.pass_to_state_machine()  # but still need to execute pass_to_state_machine() until last_applied == commit_index
        self.append_entries_for_all()

        return result

    def append_entries_for_all(self):
        """AppendEntries RPC to all followers"""
        self.persist()
        follower_ids = list(self.other_sites)
        entries_for_all = [self.logs_from(self.next_indices[id]) for id in follower_ids]  # send process "i" entries with index >= nextIndex[i]
        prev_log_indices = [self.next_indices[id] - 1 for id in follower_ids]
        prev_log_terms = [self.log_at(prev_log_index).term if prev_log_index > 0 else 0 for prev_log_index in prev_log_indices]
        logging.info('Client {} Term {} is sending AppendEntries to Clients {}'.format(self.id, self.current_term, follower_ids))
        return grequests.map([
            grequests.post('http://{}:{}/append-entries'.format(*client_addresses[id]), json=AppendEntriesData(
                term=self.current_term, leader_id=self.id, leader_commit=self.commit_index,
                prev_log_index=prev_log_index, prev_log_term=prev_log_term, entries=entries,
            ).dict(), hooks={'response': self.append_entries_hook}) for (id, prev_log_index, prev_log_term, entries) in zip(follower_ids, prev_log_indices, prev_log_terms, entries_for_all)
        ])

    def append_entries_hook(self, response, *args, **kwargs):
        """If fail then decrease the value of nextIndex[i] and retry until success"""
        data = response.json()
        if data['entries_size'] == 0:
            return response
        if data['term'] > self.current_term:
            logging.info('Client {} Term {} is out of date'.format(self.id, self.current_term))
            warnings.warn('Client {} Term {} is out of date'.format(self.id, self.current_term))
        if data['success']:
            self.match_indices[data['client_id']] = data['prev_log_index'] + data['entries_size']
            self.next_indices[data['client_id']] = self.match_indices[data['client_id']] + 1
            return response
        self.next_indices[data['client_id']] -= 1
        entries = self.logs_from(self.next_indices[data['client_id']])
        print('Retrying AppendEntriesRPC ...')
        self.persist()
        return requests.post('http://{}:{}/append-entries'.format(*client_addresses[data['client_id']]), json=AppendEntriesData(
            term=self.current_term, leader_id=self.id, leader_commit=self.commit_index, entries=entries,
            prev_log_index=self.next_indices[data['client_id']] - 1, prev_log_term=self.log_at(self.next_indices[data['client_id']] - 1).term,
        ).dict(), hooks={'response': self.append_entries_hook})

    @post('/append-entries')
    async def append_entries_handler(self, data: AppendEntriesData):
        """AppendEntries RPC handler"""
        await asyncio.sleep(MSG_RECV_DELAY)
        term, leader_id, entries = data.term, data.leader_id, data.entries
        prev_log_index, prev_log_term, leader_commit = data.prev_log_index, data.prev_log_term, data.leader_commit
        print("Client {} Term {} received AppendEntries RPC from Client {} Term {}".format(self.id, self.current_term, leader_id, term))
        logging.info("Client {} Term {} received AppendEntries RPC from Client {} Term {}".format(self.id, self.current_term, leader_id, term))
        pprint.pprint([str(entry) for entry in entries])
        self.leader_id = leader_id
        self.persist()

        if term < self.current_term:
            return {'term': self.current_term, 'client_id': self.id, 'prev_log_index': prev_log_index, 'entries_size': len(entries), 'success': False}

        if prev_log_index > 0:
            entries_coincided = [entry for entry in self.logs if entry.term == prev_log_term and entry.index == prev_log_index]
            if not entries_coincided:
                return {'term': self.current_term, 'client_id': self.id, 'prev_log_index': prev_log_index, 'entries_size': len(entries), 'success': False}
            assert len(entries_coincided) == 1  # there must be 1 coincided preceding entry

        self.logs = self.logs_to(prev_log_index, included=True) + entries
        if leader_commit > self.commit_index:
            self.commit_index = min(leader_commit, self.last_log_index)
        self.pass_to_state_machine()
        return {'term': self.current_term, 'client_id': self.id, 'prev_log_index': prev_log_index, 'entries_size': len(entries), 'success': True}

    def persist(self):
        """Persist current state before responding to RPCs"""
        with open(os.path.join(self.dpath, 'meta.json'), 'w') as f:
            json.dump({
                'current_term': self.current_term, 'voted_for': self.voted_for,
                'counter': self.counter, 'dict_meta': self.dict_meta,
                'commit_index': self.commit_index, 'last_applied': self.last_applied
            }, f, indent=4)
        with open(os.path.join(self.dpath, 'log.json'), 'w') as f:
            json.dump([entry.dict() for entry in self.logs], f, indent=4)

    def recover(self):
        """Recover from disk"""
        print('Recovering ...')
        with open(os.path.join(self.dpath, 'private.pem'), 'r') as f:
            self.own_keys['private'] = f.read()
            self.own_keys['public'] = utils.key_to_str(utils.str_to_key(self.own_keys['private'], type='private').public_key())

        with open(os.path.join(self.dpath, 'other_public.json'), 'r') as f:
            self.other_public_keys = json.load(f)
            self.other_public_keys = {int(k): v for k, v in self.other_public_keys.items()}
            self.other_sites = set(self.other_public_keys.keys())
            # mayber other_sites also contains other crashed sites
            for client_id in self.other_sites:
                try:
                    requests.post('http://{}:{}/'.format(*client_addresses[client_id]))
                except:
                    self.other_sites.remove(client_id)

        with open(os.path.join(self.dpath, 'meta.json'), 'r') as f:
            meta = json.load(f)
            self.current_term, self.voted_for, self.counter = meta['current_term'], meta['voted_for'], meta['counter']
            self.commit_index, self.last_applied = meta['commit_index'], meta['last_applied']
            self.dict_meta = meta['dict_meta']
            for dict_id in self.dict_meta:
                encrypted_private_keys = self.dict_meta[dict_id]['encrypted_private_keys']
                self.dict_meta[dict_id]['encrypted_private_keys'] = {int(k): v for k, v in encrypted_private_keys.items()}

        with open(os.path.join(self.dpath, 'log.json'), 'r') as f:
            self.logs = [LogEntry(**entry) for entry in json.load(f)]

        # rejoin the system
        self.online()
        self.reset_heartbeat_listener(first_time=True)

        # perform normal operations according to backup log entries
        self.commit_index = self.last_log_index
        self.last_applied = 0
        self.pass_to_state_machine()

    def pass_to_state_machine_once(self) -> dict:
        self.last_applied += 1
        entry_to_apply = self.log_at(self.last_applied)
        result = self.apply_log_entry(entry_to_apply)
        print('Client {} applied log entry {}'.format(self.id, entry_to_apply))
        logging.info('Client {} applied log entry {}'.format(self.id, entry_to_apply))
        return result

    def pass_to_state_machine(self):
        """Commit --> Apply"""
        while self.last_applied < self.commit_index:
            self.pass_to_state_machine_once()

    def construct_log_entry(self, opr: Operation) -> LogEntry:
        if opr.cmd is CommandType.Create:
            return self._construct_create_log_entry(opr)
        if opr.cmd is CommandType.Get:
            return self._construct_get_log_entry(opr)
        if opr.cmd is CommandType.Put:
            return self._construct_put_log_entry(opr)

    def _construct_create_log_entry(self, opr: Operation) -> LogEntry:
        members = opr.members
        dict_id = self.generate_dict_id(opr.client_id)
        key_pair = utils.generate_key_pair(output='str')
        encrypted_private_keys = {}
        for id in members:
            if id in self.other_sites:
                public_key = self.other_public_keys[id]
            else:
                public_key = self.own_keys['public']
            encrypted_private_keys[id] = utils.encrypt_with_public_key_str(key_pair['private'], public_key)

        self.dict_meta.update({dict_id: {'members': members, 'public': key_pair['public'], 'encrypted_private_keys': encrypted_private_keys}})
        creation_info = self.dict_meta[dict_id].copy()
        creation_info.update({'dict_id': dict_id})
        grequests.map([grequests.post('http://{}:{}/creation'.format(*client_addresses[id]), json=creation_info) for id in self.other_sites])

        return LogEntry(
            term=self.current_term, index=self.last_log_index + 1, client_id=opr.client_id, pre_hash=self.last_log_hash,
            cmd=CommandType.Create, dict_id=dict_id, members=members, pub_key=key_pair['public'],
            priv_keys_enc=encrypted_private_keys
        )

    @post('/creation')
    async def sync_dict_creation(self, dict_id: str = Body(..., embed=True), members: List[int] = Body(..., embed=True),
                                 public: str = Body(..., embed=True), encrypted_private_keys: Dict[int, str] = Body(..., embed=True)):
        self.dict_meta.update({dict_id: {'members': members, 'public': public,'encrypted_private_keys': encrypted_private_keys}})
        self.dict_content.update({dict_id: {}})  # this is not necessary in principle, but for safety in practice
        return {'success': True}

    def _construct_get_log_entry(self, opr: Operation) -> LogEntry:
        encrypted_key = utils.encrypt_with_public_key_str(opr.key, self.dict_meta[opr.dict_id]['public'])
        return LogEntry(
            term=self.current_term, index=self.last_log_index + 1, client_id=opr.client_id,pre_hash=self.last_log_hash,
            cmd=CommandType.Get, dict_id=opr.dict_id, key_enc=encrypted_key
        )

    def _construct_put_log_entry(self, opr: Operation) -> LogEntry:
        encrypted_key_value = utils.encrypt_with_public_key_str('({}, {})'.format(opr.key, opr.value), self.dict_meta[opr.dict_id]['public'])
        return LogEntry(
            term=self.current_term, index=self.last_log_index + 1, client_id=opr.client_id, pre_hash=self.last_log_hash,
            cmd=CommandType.Put, dict_id=opr.dict_id, key_value_enc=encrypted_key_value
        )

    def apply_log_entry(self, entry: LogEntry) -> dict:
        if entry.cmd is CommandType.Create:
            return self._apply_create_log_entry(entry)  # {'applied': True}
        if entry.cmd is CommandType.Get:
            return self._apply_get_log_entry(entry)  # {'applied': True, 'value': value}
        if entry.cmd is CommandType.Put:
            return self._apply_put_log_entry(entry)  # {'applied': True}

    def _apply_create_log_entry(self, entry: LogEntry) -> dict:
        if self.id in entry.members:
            self.dict_content.update({entry.dict_id: {}})
            logging.info('Client {} created dict {}'.format(self.id, entry.dict_id))
        return {'applied': True}

    def _apply_get_log_entry(self, entry: LogEntry) -> dict:
        """Get: return the value"""
        members = self.dict_meta[entry.dict_id]['members']
        if self.id not in members:
            return {'applied': True, 'value': None}
        return {'applied': True, 'value': self.get_value(entry)}

    def get_value(self, entry: LogEntry) -> Optional[str]:
        """Get: return the value"""
        key = utils.decrypt_with_private_key_str(entry.key_enc, self.extract_dict_private_key(entry.dict_id))
        return self.dict_content[entry.dict_id].get(key)

    def _apply_put_log_entry(self, entry: LogEntry) -> dict:
        if self.id in self.dict_meta[entry.dict_id]['members']:
            key_value = utils.decrypt_with_private_key_str(entry.key_value_enc, self.extract_dict_private_key(entry.dict_id))
            key, value = key_value[1:-1].split(', ')
            self.dict_content[entry.dict_id][key] = value
            logging.info('Client {} put key-value pair ({}, {}) into dict {}'.format(self.id, key, value, entry.dict_id))
        return {'applied': True}

    ########################################################################################################
    # network fault-tolerant functions
    ########################################################################################################
    def fail_link(self, clients: List[int]):
        """Fail the link with another client"""
        print('Client {} is failing the link with clients {}'.format(self.id, clients))
        for client_id in clients:
            if client_id in self.other_sites:
                self.other_sites.remove(client_id)
                if client_id == self.leader_id:  # remark: seemingly be redundant
                    self.leader_id = None
                requests.get('http://{}:{}/fail-link'.format(*client_addresses[client_id]), params={'client_id': self.id})
                print('Failed the link {} <--> {}'.format(self.id, client_id))
            else:
                print('Client {} does not have a link with client {}'.format(self.id, client_id))

    @get('/fail-link')
    async def fail_link_handler(self, client_id: int = Query(..., embed=True)):
        print('Client {} failed the link with client {}'.format(self.id, client_id))
        self.other_sites.remove(client_id)
        if client_id == self.leader_id:  # remark: seemingly be redundant
            self.leader_id = None
        return {'result': 'success'}

    def fix_link(self, clients: List[int]):
        """Fix the link with another client"""
        print('Client {} is fixing the link with clients {}'.format(self.id, clients))
        for client_id in clients:
            if client_id not in self.other_sites:
                self.other_sites.add(client_id)
                requests.get('http://{}:{}/fix-link'.format(*client_addresses[client_id]),
                             params={'client_id': self.id}, hooks={'response': self.update_dict_hook})
                print('Fixed the link {} <--> {}'.format(self.id, client_id))
            else:
                print('Client {} already has a link with client {}'.format(self.id, client_id))

    @get('/fix-link')
    async def fix_link_handler(self, client_id: int = Query(..., embed=True)):
        print('Client {} fixed the link with client {}'.format(self.id, client_id))
        self.other_sites.add(client_id)
        return self.dict_meta

    ########################################################################################################
    # other utils functions
    ########################################################################################################
    def generate_dict_id(self, client_id: int = None) -> str:
        client_id = client_id if client_id is not None else self.id
        dict_id = '{}-{}'.format(client_id, self.counter)
        self.counter += 1
        return dict_id

    def extract_dict_private_key(self, dict_id: str) -> str:
        if dict_id not in self.dict_meta:
            raise KeyError('Client {} does not have dict_id {}'.format(self.id, dict_id))
        if self.id not in self.dict_meta[dict_id]['members']:
            raise ValueError('Client {} is not a member of dict_id {}'.format(self.id, dict_id))
        return utils.decrypt_with_private_key_str(self.dict_meta[dict_id]['encrypted_private_keys'][self.id], self.own_keys['private'])

    def show_logs_info(self):
        print('Length of logs: {}'.format(len(self.logs)))
        print('Last log index: {}'.format(self.last_log_index))
        print('Last log term: {}'.format(self.last_log_term))
        pprint.pprint([str(entry) for entry in self.logs])

    def update_dict_hook(self, response, *args, **kwargs):
        if response.status_code == 200:
            self.dict_meta.update(response.json())
            for dict_id in self.dict_meta:
                encrypted_private_keys = self.dict_meta[dict_id]['encrypted_private_keys']
                self.dict_meta[dict_id]['encrypted_private_keys'] = {int(k): v for k, v in encrypted_private_keys.items()}
                if dict_id not in self.dict_content:
                    self.dict_content.update({dict_id: {}})

    def update_commit_index(self) -> bool:
        """
        if there exists an N such that N > commitIndex, a majority of matchIndex[i] ≥ N, and log[N].term == currentTerm:
            set commitIndex = N
        """
        for idx in range(self.last_log_index, self.commit_index, -1):
            if self.log_at(idx).term == self.current_term:
                if sum([self.match_indices[client_id] >= idx for client_id in self.other_sites]) + 1 >= NUM_MAJORITY:
                    self.commit_index = idx
                    return True
        return False

    @property
    def owned_dictionaries(self) -> List[str]:
        return [dict_id for dict_id in self.dict_meta if self.id in self.dict_meta[dict_id]['members']]

    @property
    def last_log_term(self) -> int:
        if not self.logs:
            return 0
        return self.logs[-1].term

    @property
    def last_log_index(self) -> int:
        if not self.logs:
            return 0
        return self.logs[-1].index

    @property
    def last_log_hash(self) -> str:
        if not self.logs:
            return utils.hash256('')
        return utils.hash256(self.logs[-1])

    def logs_from(self, index: int) -> List[LogEntry]:
        """log entries whose indices >= index"""
        return self.logs[index - 1:]

    def logs_to(self, index: int, included: bool = False) -> List[LogEntry]:
        """log entries whose indices < index or == index"""
        if included:
            return self.logs[:index]
        return self.logs[:index - 1]

    def log_at(self, index: int) -> LogEntry:
        """log entry with specific index"""
        return self.logs[index - 1]

    def append_logs(self, entries: Union[List[LogEntry], LogEntry]):
        """
        Every time append new entries to the self.logs, we might need to reset their indices
        """
        if isinstance(entries, LogEntry):
            entries = [entries]
        for entry in entries:
            entry.index = self.last_log_index + 1
        self.logs.extend(entries)

    @get('/')
    async def index(self):
        return {'message': 'Hello, this is client {}'.format(self.id)}

    @get('/public-key')
    async def public_key(self):
        """Return public key to the requestor"""
        return {'client_id': self.id, 'public_key': self.own_keys['public']}

    @post('/public-key')
    async def public_key_handler(self, client_id: int = Body(..., embed=True), public_key: str = Body(..., embed=True)):
        """Accept public keys from other sites"""
        self.other_sites.add(client_id)
        if client_id not in self.other_public_keys:
            self.other_public_keys.update({client_id: public_key})
            with open(os.path.join(self.dpath, 'other_public.json'), 'w') as f:
                json.dump(self.other_public_keys, f, indent=4)
            logging.info("Client {} received public key from client {}".format(self.id, client_id))
        return {'result': 'success'}

    def online(self):
        """Rejoin the system"""
        grequests.map([
            grequests.get(
                'http://{}:{}/online'.format(*client_addresses[id]), params={'client_id': self.id},
                hooks={'response': self.update_dict_hook}
            ) for id in self.other_sites
        ])
        logging.info('Client {} rejoins the system'.format(self.id))

    @get('/online')
    async def add_online_client(self, client_id: int = Query(..., embed=True)):
        self.other_sites.add(client_id)
        return self.dict_meta

    def offline(self):
        """Shutdown the client and notify other clients among the system"""
        res_list = grequests.map([
            grequests.get(
                'http://{}:{}/offline'.format(*client_addresses[id]), params={'client_id': self.id}
            ) for id in self.other_sites
        ])
        logging.info('Client {} exits the system'.format(self.id))

    @get('/offline')
    async def remove_offline_client(self, client_id: int = Query(..., embed=True)):
        """Remove the client from the system"""
        self.other_sites.remove(client_id)
        return {'result': 'success'}

    ########################################################################################################
    # Main-body control
    ########################################################################################################
    def acquire_public_keys(self):
        """Acquiring public keys from other clients"""
        # get public keys from other clients
        grequests.map([
            grequests.get(
                'http://{}:{}/public-key'.format(*client_addresses[id]),
                hooks={'response': lambda r, *args, **kwargs: self.other_public_keys.update(
                    {r.json()['client_id']: r.json()['public_key']})}
            ) for id in self.other_sites
        ])
        logging.info("Client {} got public key from clients {}".format(self.id, self.other_sites))

        # post its public key to other clients
        grequests.map([
            grequests.post(
                'http://{}:{}/public-key'.format(*client_addresses[id]),
                json={'client_id': self.id, 'public_key': self.own_keys['public']}
            ) for id in self.other_sites
        ])
        logging.info("Client {} posted public key to clients {}".format(self.id, self.other_sites))

    def prompt(self):
        """Output command prompt information"""
        print('Commands:')
        print('\tall: show IDs of all dictionaries the client is a member of, and other related info')
        print('\tdict <dict_id>: show member clients of the dictionary and its content')
        print('\tcreate <client_id_1>, <client_id_2>, ...: create a new dictionary belonging to specific clients')
        print('\tput <dict_id> <key> <value>: put a key-value pair into a dictionary')
        print('\tget <dict_id> <key>: get the value of a key from a dictionary')
        print('\tfail <client_id>: fail the connection with another client')
        print('\tfix <client_id>: fix the connection with another client')
        print('\texit: exit the client')

    def interact(self):
        time.sleep(1)
        self.prompt()
        while True:
            cmd = input('>>> ').strip().lower()
            if re.match(r'(all|printall|print_all|print\s+all)$', cmd):
                print()
                print('Client ID: {}'.format(self.id))
                print('Current term: {}'.format(self.current_term))
                print('Status: {} (Leader={})'.format(self.status, self.leader_id))
                print('Timeout: {:.2f}'.format(self.timeout))
                print('Other sites connected: {}'.format(self.other_sites))
                print('Commit Index: {}, Last Applied: {}'.format(self.commit_index, self.last_applied))
                if self.status is IdentityStatus.Leader:
                    print('Next Indices: {}'.format(self.next_indices))
                    print('Match Indices: {}'.format(self.match_indices))
                print('All dictionaries owned by Client {}: {}'.format(self.id, self.owned_dictionaries))
            elif re.match(r'dict\s+\d+-\d+$', cmd):  # dict <dict_id>
                dict_id = cmd.split()[-1]
                if dict_id not in self.dict_meta:
                    print('<Invalid dict_id> Dictionary {} does not exist in this system'.format(dict_id))
                    continue
                if dict_id not in self.owned_dictionaries:
                    print('<Invalid dict_id> Dictionary {} is not owned by client {}'.format(dict_id, self.id))
                    continue
                members = self.dict_meta[dict_id]['members']
                dictionary = self.dict_content[dict_id]
                print()
                print('Member clients of dictionary {}: {}'.format(dict_id, members))
                print('Content of dictionary {}:'.format(dict_id))
                pprint.pprint(dictionary)
            elif re.match(r'create\s+((\d+\s+)*\d+)$', cmd):  # create <client_id_1>, <client_id_2> ...
                members = list(set([int(id) for id in cmd.split()[1:]]))
                invalid_members = [member for member in members if member < 0 or member >= NUM_CLIENTS]
                unconnected_members = [member for member in members if member not in self.other_sites and member not in invalid_members and member != self.id]
                if invalid_members:
                    print('<Invalid client_id> Client IDs {} are invalid'.format(invalid_members))
                    continue
                if unconnected_members:
                    print('<Unconnected client_id> Client IDs {} are not connected to this client'.format(unconnected_members))
                    continue
                if self.id not in members:
                    print('<Invalid client_id> Client {} should be a member of the dictionary'.format(self.id))
                    continue
                res = self.respond_to_user(Operation(client_id=self.id, cmd=CommandType.Create, members=members))
                print()
                pprint.pprint(res)
            elif re.match(r'put\s+\d+-\d+\s+\w+\s+\w+$', cmd):  # put <dict_id> <key> <value>
                dict_id, key, value = cmd.split()[1:]
                if dict_id not in self.dict_meta:
                    print('<Invalid dict_id> Dictionary {} does not exist in this system'.format(dict_id))
                    continue
                if dict_id not in self.owned_dictionaries:
                    print('<Invalid dict_id> Dictionary {} is not owned by client {}'.format(dict_id, self.id))
                    continue
                res = self.respond_to_user(Operation(client_id=self.id, cmd=CommandType.Put, dict_id=dict_id, key=key, value=value))
                print()
                pprint.pprint(res)
            elif re.match(r'get\s+\d+-\d+\s+\w+$', cmd):  # get <dict_id> <key>
                dict_id, key = cmd.split()[1:]
                if dict_id not in self.dict_meta:
                    print('<Invalid dict_id> Dictionary {} does not exist in this system'.format(dict_id))
                    continue
                if dict_id not in self.owned_dictionaries:
                    print('<Invalid dict_id> Dictionary {} is not owned by client {}'.format(dict_id, self.id))
                    continue
                res = self.respond_to_user(Operation(client_id=self.id, cmd=CommandType.Get, dict_id=dict_id, key=key))
                print()
                pprint.pprint(res)
            elif re.match(r'fail\s+((\d+\s+)*\d+)$', cmd):  # fail <client_id>
                clients = list(set([int(id) for id in cmd.split()[1:]]))
                if self.id in clients:
                    print('<Invalid client_id> Client {} cannot fail itself'.format(self.id))
                    continue
                print()
                self.fail_link(clients)
            elif re.match(r'fix\s+((\d+\s+)*\d+)$', cmd):  # fix <client_id>
                clients = list(set([int(id) for id in cmd.split()[1:]]))
                if self.id in clients:
                    print('<Invalid client_id> Client {} cannot fix itself'.format(self.id))
                    continue
                print()
                self.fix_link(clients)
            elif re.match(r'(exit|quit|q)$', cmd):  # exit the client
                print('Exiting...')
                self.offline()
                os._exit(0)
            elif cmd == 'log':
                print()
                self.show_logs_info()
            else:
                print('Invalid command')
            print()


def allocate_client_site() -> Tuple[Tuple[int, str, int], Set[int]]:
    """Allocate a new site for a client"""
    other_sites = set()
    for id, (host, port) in client_addresses.items():
        try:
            # attempt to connect to this site
            requests.get('http://{}:{}/'.format(host, port))
            other_sites.add(id)
        except requests.ConnectionError:
            # allocate this site to this client
            return (id, host, port), other_sites
    raise ConnectionError("No available client site for this system")


def create_client() -> Client:
    """Create a new client with a new site"""
    client = Client()
    (client.id, client.host, client.port), client.other_sites = allocate_client_site()

    client.dpath = os.path.join('../result', 'client-{}'.format(client.id))
    if not os.path.exists(client.dpath):
        os.makedirs(client.dpath)

    client.own_keys = utils.generate_key_pair(output='str')
    with open(os.path.join(client.dpath, 'private.pem'), 'w') as f:
        f.write(client.own_keys['private'])

    client.acquire_public_keys()
    with open(os.path.join(client.dpath, 'other_public.json'), 'w') as f:
        json.dump(client.other_public_keys, f, indent=4)

    client.online()
    client.reset_heartbeat_listener(first_time=True)

    return client


def recover_client(client_id: int) -> Client:
    """Recover process from disk"""
    client = Client()
    client.id = client_id
    client.host, client.port = client_addresses[client_id]
    client.dpath = os.path.join('../result', 'client-{}'.format(client.id))
    print('Recovering client {} from {}'.format(client_id, client.dpath))
    assert os.path.exists(client.dpath)
    client.recover()
    return client
