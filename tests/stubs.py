from api.chains import CHAINS, TxStatus
from sweeper.base import Sweeper
from watcher.base import Payment, Scanner


class StubScanner(Scanner):
    """In-memory chain: payments per block, tx statuses, balances."""

    def __init__(self, chain_key="tron-nile", head=100):
        super().__init__(CHAINS[chain_key])
        self.head = head
        self.blocks: dict[int, list[Payment]] = {}
        self.statuses: dict[str, TxStatus] = {}
        self.balances: dict[tuple[str, str], int] = {}
        self.scanned: list[tuple[int, int]] = []
        self.times: dict = {}   # block -> datetime; unset blocks have no known time

    def pay(self, address, asset, amount, tx_hash, block=None):
        block = block or self.head
        self.blocks.setdefault(block, []).append(Payment(address, asset, amount, tx_hash, block))
        self.statuses[tx_hash] = TxStatus.OK

    def normalize(self, address):
        return address

    def latest_block(self):
        return self.head

    def scan(self, start, end, watched, assets):
        self.scanned.append((start, end))
        return [
            p for n in range(start, end + 1) for p in self.blocks.get(n, [])
            if p.address in watched and p.asset in assets
        ]

    def tx_status(self, tx_hash, block_number):
        return self.statuses.get(tx_hash, TxStatus.FAILED)

    def balance_at(self, address, asset, block):
        return self.balances.get((address, asset))

    def block_time(self, block):
        return self.times.get(block)


class StubSweeper(Sweeper):
    def __init__(self, chain_key="tron-nile", needs_fee=False):
        self.chain = CHAINS[chain_key]
        self.needs_fee = needs_fee
        self.statuses: dict[str, TxStatus] = {}
        self.sent: list[tuple[str, str]] = []

    def treasury(self, project):
        return (project.get("treasury") or {}).get(self.chain.family)

    def tx_status(self, tx_hash):
        return self.statuses.get(tx_hash, TxStatus.UNKNOWN)

    def sweep(self, d, project, treasury):
        self.treasuries = getattr(self, "treasuries", []) + [treasury]
        if self.needs_fee:
            self.needs_fee = False
            d.fee_tx_hash = f"fee-{len(self.sent)}"
            self.sent.append(("fee", d.fee_tx_hash))
        else:
            d.sweep_tx_hash = f"sweep-{len(self.sent)}"
            self.sent.append(("sweep", d.sweep_tx_hash))
