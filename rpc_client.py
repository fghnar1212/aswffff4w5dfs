# rpc_client.py
from web3 import Web3

ETH_RPC = "https://rpc.ankr.com/eth/8ce0dd039fed69285b7b4c243d2ba88dad2b133969e45a7eb7207d8b085646a8"
BSC_RPC = "https://rpc.ankr.com/bsc/8ce0dd039fed69285b7b4c243d2ba88dad2b133969e45a7eb7207d8b085646a8"

w3_eth = Web3(Web3.HTTPProvider(ETH_RPC))
w3_bsc = Web3(Web3.HTTPProvider(BSC_RPC))

def is_valid_address(addr: str) -> bool:
    try:
        return Web3.is_address(addr)
    except:
        return False

async def has_erc20_or_bep20_activity(address: str) -> bool:
    if not is_valid_address(address):
        return False

    for w3 in [w3_eth, w3_bsc]:
        try:
            tx_count = w3.eth.get_transaction_count(address, 'latest')
            if tx_count > 0:
                return True
        except Exception as e:
            print(f"RPC error: {e}")
            continue
    return False
