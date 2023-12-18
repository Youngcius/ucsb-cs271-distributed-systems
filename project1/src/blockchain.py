"""Necessary data structures for the blockchain"""
import json
import hashlib
from pydantic import BaseModel, Field, validator
from utils import get_current_time
from typing import List


class Block:
    def __init__(self, transaction, previous_hash=None):
        self.transaction: Transaction = transaction
        self.previous_hash = previous_hash if previous_hash else hashlib.sha256(''.encode()).hexdigest()
        self.next_block: Block = None

    def __repr__(self):
        return 'Block(transaction={}, timestamp={})'.format(self.transaction.to_tuple(), self.transaction.timestamp)

    def hash(self):
        """Returns the hash of the block (SHA256)"""
        # 64 hex digits in form of string (256 bits)
        return hashlib.sha256(str(self).encode()).hexdigest()


class BlockChain:
    def __init__(self) -> None:
        self.chain: List[Block] = []

    def __repr__(self):
        blocks_str = ''
        for block in self.chain:
            blocks_str += str(block) + ',\n\t'
        return """BlockChain([\n\t{}\n])""".format(blocks_str.strip())

    def display(self):
        """Display the blockchain in a readable format"""
        print('-' * 20)
        print('Total blocks: {}'.format(len(self.chain)))
        print('-' * 10)
        for i, block in enumerate(self.chain):
            print('Block {}:'.format(i))
            print('  transaction: {}'.format(block.transaction))
            print('  previous_hash: {}'.format(block.previous_hash))
            print('  hash: {}'.format(block.hash()))
            print('  next_block: {}'.format(block.next_block))
            print()

    def add_transaction(self, transaction):
        self.chain.append(Block(transaction))
        self.resort_blocks()

    def resort_blocks(self):
        """Recompute the hash of the blockchain"""
        self.chain.sort(key=lambda b: (b.transaction.sender_logic_clock, b.transaction.sender_id))
        if self.chain:
            self.chain[0].previous_hash = hashlib.sha256(''.encode()).hexdigest()
        for i, block in enumerate(self.chain[:-1]):
            block.next_block = self.chain[i + 1]
            block.next_block.previous_hash = block.hash()
        if self.chain:
            self.chain[-1].next_block = None


class Transaction(BaseModel):
    sender_id: int = Field(example=1)
    recipient_id: int = Field(example=2)
    amount: float = Field(example=1.0)
    sender_logic_clock: int = Field(example=1)  # Lamport logic clock
    timestamp: str = None
    status: str = Field(default='PENDING')  # PENDING, SUCCESS, ABORT
    num_replies: int = Field(default=0)

    def __eq__(self, other) -> bool:
        return self.sender_id == other.sender_id and self.recipient_id == other.recipient_id and self.amount == other.amount and self.sender_logic_clock == other.sender_logic_clock and self.timestamp == other.timestamp

    @validator('timestamp', pre=True, always=True)
    def set_create_time_now(cls, v):
        return v or get_current_time()

    def to_json(self):
        return json.dumps(self.__dict__, sort_keys=True)

    def to_tuple(self):
        return self.sender_id, self.recipient_id, self.amount


class Account(BaseModel):
    id: int = Field(example=1)
    balance: float = Field(default=10.0)
    recent_access_time: str = None

    @validator('recent_access_time', pre=True, always=True)
    def set_create_time_now(cls, v):
        return v or get_current_time()

    class Config:
        schema_extra = {
            "example": {
                'id': 1,
                'balance': 10.0,
                'recent_access_time': '2023-01-26T15:54'
            }
        }
