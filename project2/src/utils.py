import uuid
from typing import List
from pydantic import BaseModel, validator


class LocalState(BaseModel):
    node: str  # client name
    label: str = None  # 'send' or 'recv'
    message: str = None

    @validator('label')
    def validate_label(cls, v):
        if v not in ['send', 'recv']:
            raise ValueError('Invalid label (should be "send" or "recv"))')
        return v


class ChannelState(BaseModel):
    src: str  # source client name
    dst: str  # destination client name
    message: str = None  # if this field is None, then the channel state is "empty"


class Snapshot(BaseModel):
    stamp: str  # which global snapshot activity the snapshot belongs to
    initiator: str
    owner: str  # which client creates the snapshot
    local_states: List[LocalState] = []
    channel_states: List[ChannelState] = []

    @validator('local_states')
    def sort_local_states(cls, v):
        return sorted(v, key=lambda x: x.node)

    @validator('channel_states')
    def sort_channel_states(cls, v):
        return sorted(v, key=lambda x: (x.src, x.dst))


class Marker(BaseModel):
    stamp: str  # which global snapshot activity the marker belongs to
    initiator: str
    src: str = None  # which incoming channel
    body: str = ''

    @validator('body', pre=True, always=True)
    def validate_body(cls, v):
        return v if v != '' else str(uuid.uuid4()).split('-')[0]

    def __str__(self):
        return f'Marker(stamp={self.stamp}, initiator={self.initiator}, src={self.src}, body={self.body})'


def generate_token(use_input: bool = False):
    if use_input:
        return input('Input token: ')
    return str(uuid.uuid4()).split('-')[0]
