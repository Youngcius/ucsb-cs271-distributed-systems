"""
Data structures for the distributed dictionary system
"""
from enum import Enum
from typing import List, Dict
from pydantic import BaseModel
from utils import hash256


class IdentityStatus(str, Enum):
    Leader = 'Leader'
    Candidate = 'Candidate'
    Follower = 'Follower'


class CommandType(str, Enum):
    Put = 'put'
    Get = 'get'
    Create = 'create'


class Operation(BaseModel):
    """Put, Get or Create operation"""
    client_id: int  # issuing client id
    cmd: CommandType  # put, get, create
    dict_id: str = None
    key: str = None
    value: str = None
    members: List[int] = []


class LogEntry(BaseModel):
    term: int
    index: int
    client_id: int  # issuing client id
    dict_id: str
    pre_hash: str = hash256('')
    cmd: CommandType  # put, get, create
    key_value_enc: str = None  # only for Put operation
    key_enc: str = None  # only for Get operation
    members: List[int] = []  # only for Create operation
    pub_key: str = None  # only for Create operation
    priv_keys_enc: Dict[int, str] = []  # only for Create operation

    def __str__(self):
        if self.cmd is CommandType.Put:
            return 'Put(term={}, index={}, dict_id={}, client_id={})'.format(self.term, self.index, self.dict_id, self.client_id)
        if self.cmd is CommandType.Get:
            return 'Get(term={}, index={}, dict_id={}, client_id={})'.format(self.term, self.index, self.dict_id, self.client_id)
        if self.cmd is CommandType.Create:
            return 'Create(term={}, index={}, dict_id={}, client_id={})'.format(self.term, self.index, self.dict_id, self.client_id)


class AppendEntriesData(BaseModel):
    term: int
    leader_id: int
    prev_log_index: int
    prev_log_term: int
    entries: List[LogEntry]
    leader_commit: int


class HeartbeatData(BaseModel):
    term: int
    leader_id: int
    prev_log_index: int  # seemingly redundant
    prev_log_term: int  # seemingly redundant

