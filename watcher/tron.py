"""
Tron scanner (mainnet, Nile).

TRX: successful `TransferContract` transactions in the block. TRC-20: `Transfer` events from
`gettransactioninfobyblocknum`. A payment counts once its transaction is in a solidified block.
TRX sent by contracts (internal transfers) is not seen by the scan. Partial deposits also get a
balance check, which catches those and anything sent while the address was not watched.
"""

from datetime import UTC, datetime

from tronpy.exceptions import AddressNotFound, TransactionNotFound
from tronpy.keys import to_base58check_address, to_hex_address, to_tvm_address

from api.chains import Chain, TxStatus
from api.tron import make_client
from .base import Payment, Scanner

TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _log_address(hex_address: str) -> str:
    """Event logs carry 20-byte addresses; drop the 0x41 prefix if a node includes it."""
    hex_address = hex_address.lower()
    return hex_address[2:] if len(hex_address) == 42 else hex_address


class TronScanner(Scanner):
    def __init__(self, chain: Chain):
        super().__init__(chain)
        self.client = make_client(chain)

    def normalize(self, address: str) -> str:
        return address   # base58check is case-sensitive

    def latest_block(self) -> int:
        # getnowblock rather than tronpy's getnodeinfo shortcut, which some public nodes do not serve
        return self.client.get_latest_block()["block_header"]["raw_data"]["number"]

    def block_time(self, block: int) -> datetime | None:
        millis = self.client.get_block(block)["block_header"]["raw_data"]["timestamp"]
        return datetime.fromtimestamp(millis / 1000, UTC).replace(tzinfo=None)

    def latest_solid_block(self) -> int:
        return self.client.get_latest_solid_block()["block_header"]["raw_data"]["number"]

    def scan(self, start: int, end: int, watched: set[str], assets: set[str]) -> list[Payment]:
        tokens = {
            _log_address(to_hex_address(t.contract)): t for symbol, t in self.chain.tokens.items() if symbol in assets
        }
        native = self.chain.native_symbol in assets
        payments = []
        for n in range(start, end + 1):
            if native:
                payments += self._scan_native(n, watched)
            if tokens:
                payments += self._scan_tokens(n, watched, tokens)
        return payments

    def _scan_native(self, n: int, watched: set[str]) -> list[Payment]:
        payments = []
        block = self.client.get_block(n)
        for tx in block.get("transactions", []):
            if tx.get("ret", [{}])[0].get("contractRet") != "SUCCESS":
                continue
            contract = tx["raw_data"]["contract"][0]
            if contract["type"] != "TransferContract":
                continue
            value = contract["parameter"]["value"]
            if value["to_address"] in watched and value["amount"] > 0:
                payments.append(Payment(value["to_address"], self.chain.native_symbol, value["amount"], tx["txID"], n))
        return payments

    def _scan_tokens(self, n: int, watched: set[str], tokens: dict) -> list[Payment]:
        payments = []
        infos = self.client.provider.make_request("wallet/gettransactioninfobyblocknum", {"num": n})
        if not isinstance(infos, list):   # an empty block comes back as {}
            return payments
        for info in infos:
            if info.get("receipt", {}).get("result") != "SUCCESS":
                continue
            for event in info.get("log", []):
                token = tokens.get(_log_address(event.get("address", "")))
                topics = event.get("topics", [])
                if token is None or len(topics) != 3 or topics[0] != TRANSFER_TOPIC:
                    continue
                to = to_base58check_address("41" + topics[2][-40:])
                amount = int(event.get("data") or "0", 16)
                if to in watched and amount > 0:
                    payments.append(Payment(to, token.symbol, amount, info["id"], n))
        return payments

    def balance_at(self, address: str, asset: str, block: int) -> int | None:
        # Tron nodes only serve the current balance, so it counts only while the head is still
        # `block`; a newer block could hold a transfer the scan will also see, counting it twice
        if self.latest_block() != block:
            return None
        if self.chain.is_native(asset):
            try:
                balance = self.client.get_account(address).get("balance", 0)
            except AddressNotFound:
                balance = 0   # never received anything, so not activated yet
        else:
            result = self.client.trigger_const_smart_contract_function(
                address, self.chain.tokens[asset].contract, "balanceOf(address)", to_tvm_address(address).hex().rjust(64, "0")
            )
            balance = int(result or "0", 16)
        return balance if self.latest_block() == block else None

    def tx_status(self, tx_hash: str, block_number: int) -> TxStatus:
        try:
            info = self.client.get_solid_transaction_info(tx_hash)
        except TransactionNotFound:
            # not solidified yet, or gone; give solidification a full confirmation window before deciding
            solid = self.latest_solid_block()
            return TxStatus.FAILED if solid > block_number + self.chain.confirmations else TxStatus.UNKNOWN
        return TxStatus.FAILED if info.get("result") == "FAILED" else TxStatus.OK
