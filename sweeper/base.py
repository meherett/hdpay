from abc import ABC, abstractmethod
from typing import Any

from api.chains import Chain, TxStatus
from api.config import settings
from api.db import Deposit
from api.keys import check_master_key


class Sweeper(ABC):
    """Chain-specific half of the sweeper: moves a confirmed deposit's funds to its project's treasury."""

    def __init__(self, chain: Chain):
        self.chain = chain
        self.master_xprv = settings.master_xprv
        if not self.master_xprv:
            raise RuntimeError(f"HDPAY_MASTER_XPRV is required to sweep {chain.key}")
        check_master_key(self.master_xprv)

    @abstractmethod
    def treasury(self, project: dict[str, Any]) -> str | None:
        """The project's treasury for this chain, in the chain's own form; None until it is set."""

    @abstractmethod
    def tx_status(self, tx_hash: str) -> TxStatus:
        """Whether a sweep or fee top-up transaction has landed. UNKNOWN while still pending."""

    @abstractmethod
    def sweep(self, deposit: Deposit, project: dict[str, Any], treasury: str) -> None:
        """Take the next step for a confirmed deposit.

        Native coins: send the balance minus fees to the treasury and set `sweep_tx_hash`.
        Tokens: if the address cannot pay the transfer fee, send the shortfall from the project's fee
        funder and set `fee_tx_hash`; otherwise send the tokens and set `sweep_tx_hash`.
        Leave both unset when there is nothing worth sweeping.
        """
