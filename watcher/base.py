from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from api.chains import Chain, TxStatus


@dataclass(frozen=True)
class Payment:
    address: str        # recipient, in the scanner's normalised form
    asset: str
    amount: int         # smallest unit
    tx_hash: str
    block_number: int


class Scanner(ABC):
    """Chain-specific half of the watcher: turns blocks into payments to watched addresses."""

    def __init__(self, chain: Chain):
        self.chain = chain

    @abstractmethod
    def normalize(self, address: str) -> str:
        """Canonical form used to match payment recipients against deposit addresses."""

    @abstractmethod
    def latest_block(self) -> int: ...

    @abstractmethod
    def scan(
        self, start: int, end: int, watched: set[str], assets: set[str], tokens: list | None = None
    ) -> list[Payment]:
        """Payments of `assets` to `watched` addresses in blocks start..end inclusive."""

    @abstractmethod
    def tx_status(self, tx_hash: str, block_number: int) -> TxStatus:
        """Re-check a payment's transaction after its confirmations (reorg guard)."""

    def block_time(self, block: int) -> datetime | None:
        """When the block was made, naive UTC: a payment's time, for product discounts. None = unknown."""
        return None

    def balance_at(self, address: str, asset: str, block: int, contract: str | None = None) -> int | None:
        """Balance at `block`, for catching transfers that `scan` cannot see; a token at `contract` (else
        the chains file's). None = unsupported."""
        return None

    def balance_covering(
        self, address: str, asset: str, block: int, contract: str | None = None
    ) -> tuple[int, int] | None:
        """The balance and the last block it includes: every transfer up to that block is in it. At
        `block` itself where the chain can say; a chain that only knows the balance now (Tron)
        returns that and the head. None = unknown."""
        balance = self.balance_at(address, asset, block, contract)
        return None if balance is None else (balance, block)
