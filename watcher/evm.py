"""
EVM scanner (Ethereum, Sepolia, BNB Smart Chain).

Native coins: `tx.to` of every transaction in the block, plus the balance fallback for
internal transfers. Tokens: ERC-20 `Transfer` logs of the listed contracts, filtered by recipient.
"""

from datetime import UTC, datetime

from web3 import Web3
from web3.exceptions import TransactionNotFound

from api.chains import Chain, TxStatus
from api.evm import web3_for
from .base import Payment, Scanner

TRANSFER_TOPIC = Web3.to_hex(Web3.keccak(text="Transfer(address,address,uint256)"))
_BALANCE_OF_ABI = [{
    "name": "balanceOf", "type": "function", "stateMutability": "view",
    "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}],
}]
ADDRESSES_PER_LOG_QUERY = 500


def _topic(address: str) -> str:
    return "0x" + address[2:].lower().rjust(64, "0")


class EvmScanner(Scanner):
    def __init__(self, chain: Chain):
        super().__init__(chain)
        self.w3 = web3_for(chain)
        if self.w3.eth.chain_id != chain.chain_id:
            raise RuntimeError(f"RPC for {chain.key} reports chain id {self.w3.eth.chain_id}, expected {chain.chain_id}")

    def normalize(self, address: str) -> str:
        return address.lower()

    def latest_block(self) -> int:
        return self.w3.eth.block_number

    def block_time(self, block: int) -> datetime | None:
        return datetime.fromtimestamp(self.w3.eth.get_block(block)["timestamp"], UTC).replace(tzinfo=None)

    def scan(self, start: int, end: int, watched: set[str], assets: set[str]) -> list[Payment]:
        payments = []
        if self.chain.native_symbol in assets:
            payments += self._scan_native(start, end, watched)
        tokens = {t.contract.lower(): t for symbol, t in self.chain.tokens.items() if symbol in assets}
        if tokens:
            payments += self._scan_tokens(start, end, watched, tokens)
        return payments

    def _scan_native(self, start: int, end: int, watched: set[str]) -> list[Payment]:
        payments = []
        for n in range(start, end + 1):
            block = self.w3.eth.get_block(n, full_transactions=True)
            for tx in block["transactions"]:
                to = (tx.get("to") or "").lower()
                if to in watched and tx["value"] > 0:
                    payments.append(Payment(to, self.chain.native_symbol, tx["value"], Web3.to_hex(tx["hash"]), n))
        return payments

    def _scan_tokens(self, start: int, end: int, watched: set[str], tokens: dict) -> list[Payment]:
        payments = []
        recipients = sorted(watched)
        contracts = [Web3.to_checksum_address(c) for c in tokens]
        for i in range(0, len(recipients), ADDRESSES_PER_LOG_QUERY):
            chunk = recipients[i : i + ADDRESSES_PER_LOG_QUERY]
            logs = self.w3.eth.get_logs({
                "fromBlock": start,
                "toBlock": end,
                "address": contracts,
                "topics": [TRANSFER_TOPIC, None, [_topic(a) for a in chunk]],
            })
            for entry in logs:
                if entry.get("removed") or len(entry["topics"]) != 3:
                    continue
                token = tokens[entry["address"].lower()]
                to = "0x" + Web3.to_hex(entry["topics"][2])[-40:]
                amount = int(Web3.to_hex(entry["data"]), 16)
                if to in watched and amount > 0:
                    payments.append(Payment(to, token.symbol, amount, Web3.to_hex(entry["transactionHash"]), entry["blockNumber"]))
        return payments

    def tx_status(self, tx_hash: str, block_number: int) -> TxStatus:
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return TxStatus.FAILED
        return TxStatus.OK if receipt["status"] == 1 else TxStatus.FAILED

    def balance_at(self, address: str, asset: str, block: int) -> int | None:
        owner = Web3.to_checksum_address(address)
        if self.chain.is_native(asset):
            return self.w3.eth.get_balance(owner, block_identifier=block)
        token = self.w3.eth.contract(address=Web3.to_checksum_address(self.chain.tokens[asset].contract), abi=_BALANCE_OF_ABI)
        return token.functions.balanceOf(owner).call(block_identifier=block)
