"""
Tron scanner (mainnet, Nile).

TRX: successful `TransferContract` transactions in the block. TRC-20: `Transfer` events from
`gettransactioninfobyblocknum`. A payment counts once its transaction is in a solidified block.
TRX sent by contracts (internal transfers) is not seen by the scan. Partial deposits also get a
balance check, which catches those and anything sent while the address was not watched.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import requests

from tronpy.exceptions import AddressNotFound, TransactionNotFound
from tronpy.keys import to_base58check_address, to_hex_address, to_tvm_address

from api.chains import Chain, TxStatus
from api.tron import make_client, tron_http_url
from .base import Payment, Scanner

log = logging.getLogger("hdpay.watcher")
# blocks fetched at once: each is one or two requests, and a watcher catching up reads many
SCAN_WORKERS = 8
# token transfers are asked for by range: this many blocks, and this many recipients, per request
LOG_RANGE_BLOCKS = 5_000
ADDRESSES_PER_LOG_QUERY = 500
TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _log_address(hex_address: str) -> str:
    """Event logs carry 20-byte addresses; drop the 0x41 prefix if a node includes it."""
    hex_address = hex_address.lower()
    return hex_address[2:] if len(hex_address) == 42 else hex_address


class TronScanner(Scanner):
    def __init__(self, chain: Chain):
        super().__init__(chain)
        self.client = make_client(chain)
        self.jsonrpc_url = tron_http_url(chain.rpc_url) + "jsonrpc"
        self.logs_by_range = True   # off for good once the node turns out not to serve eth_getLogs

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

    def scan(
        self, start: int, end: int, watched: set[str], assets: set[str], tokens: list | None = None
    ) -> list[Payment]:
        """`tokens`: the contracts to look at (the watched deposits' own); else the file's, for `assets`."""
        if tokens is None:
            tokens = [t for symbol, t in self.chain.tokens.items() if symbol in assets]
        tokens = {_log_address(to_hex_address(t.contract)): t for t in tokens}
        native = self.chain.native_symbol in assets
        payments = []
        by_block = False
        if tokens and self.logs_by_range:
            try:
                payments += self._token_logs(start, end, watched, tokens)
            except Exception as exc:   # a node without Tron's JSON-RPC: read the blocks one by one instead
                log.warning("%s: token transfers by range not served (%s); reading block by block", self.chain.key, exc)
                self.logs_by_range = False
                payments = []
        if tokens and not self.logs_by_range:
            by_block = True

        def one(n: int) -> list[Payment]:
            found = []
            if native:
                found += self._scan_native(n, watched)
            if by_block:
                found += self._scan_tokens(n, watched, tokens)
            return found

        if native or by_block:
            with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
                for found in pool.map(one, range(start, end + 1)):
                    payments += found
        return sorted(payments, key=lambda p: p.block_number)

    def _token_logs(self, start: int, end: int, watched: set[str], tokens: dict) -> list[Payment]:
        """Transfer events to watched addresses from start to end, a few requests per range, through
        Tron's Ethereum-style JSON-RPC (eth_getLogs) on the same node."""
        payments = []
        recipients = sorted(watched)
        contracts = ["0x" + c for c in tokens]
        for a in range(start, end + 1, LOG_RANGE_BLOCKS):
            b = min(a + LOG_RANGE_BLOCKS - 1, end)
            for i in range(0, len(recipients), ADDRESSES_PER_LOG_QUERY):
                chunk = recipients[i : i + ADDRESSES_PER_LOG_QUERY]
                topics = ["0x" + "0" * 24 + _log_address(to_hex_address(r)) for r in chunk]
                reply = requests.post(self.jsonrpc_url, timeout=30, json={
                    "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
                    "params": [{"fromBlock": hex(a), "toBlock": hex(b), "address": contracts,
                                "topics": ["0x" + TRANSFER_TOPIC, None, topics]}],
                })
                reply.raise_for_status()
                body = reply.json()
                if "error" in body or not isinstance(body.get("result"), list):
                    raise RuntimeError(str(body.get("error") or "no result")[:120])
                for entry in body["result"]:
                    entry_topics = entry.get("topics") or []
                    if entry.get("removed") or len(entry_topics) != 3:
                        continue
                    token = tokens.get(_log_address(entry["address"].removeprefix("0x")))
                    if token is None:
                        continue
                    to = to_base58check_address("41" + entry_topics[2][-40:])
                    amount = int(entry.get("data") or "0x0", 16)
                    if to in watched and amount > 0:
                        tx = entry["transactionHash"].removeprefix("0x")   # as the block-by-block scan names it
                        payments.append(Payment(to, token.symbol, amount, tx, int(entry["blockNumber"], 16)))
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

    def balance_covering(
        self, address: str, asset: str, block: int, contract: str | None = None
    ) -> tuple[int, int] | None:
        """Tron nodes only know the balance now. It is read, then the head: every block up to that
        head is taken as included, so the watcher skips their transfers instead of counting them
        twice. (One landing in that same head after the read is counted by the next balance check.)"""
        balance = self._balance_now(address, asset, contract)
        return balance, max(self.latest_block(), block)

    def _balance_now(self, address: str, asset: str, contract: str | None = None) -> int:
        if self.chain.is_native(asset):
            try:
                return self.client.get_account(address).get("balance", 0)
            except AddressNotFound:
                return 0   # never received anything, so not activated yet
        result = self.client.trigger_const_smart_contract_function(
            address, contract or self.chain.tokens[asset].contract, "balanceOf(address)",
            to_tvm_address(address).hex().rjust(64, "0"),
        )
        return int(result or "0", 16)

    def balance_at(self, address: str, asset: str, block: int, contract: str | None = None) -> int | None:
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
                address, contract or self.chain.tokens[asset].contract, "balanceOf(address)", to_tvm_address(address).hex().rjust(64, "0")
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
