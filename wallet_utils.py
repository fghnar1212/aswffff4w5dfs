# wallet_utils.py
from web3 import Web3
from mnemonic import Mnemonic

w3 = Web3()
mnemo = Mnemonic("english")

def private_key_to_address(private_key: str) -> str:
    try:
        if private_key.startswith("0x"):
            private_key = private_key[2:]
        return Web3.to_checksum_address(w3.eth.account.from_key(private_key).address)
    except:
        return None

def seed_to_address(seed_phrase: str) -> str:
    try:
        if not mnemo.check(seed_phrase):
            return None
        private_key = w3.sha3(text=seed_phrase).hex()[2:66]
        return private_key_to_address(private_key)
    except:
        return None
