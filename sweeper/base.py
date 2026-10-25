import logging
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

from api.chains import Chain, TxStatus
from api.config import settings
from api.db import Deposit
from api.keys import check_master_key

log = logging.getLogger("hdpay.sweeper")


class Sweeper(ABC):
    """Chain-specific half of the sweeper: moves a confirmed deposit's funds to its project's treasury."""

    def __init__(self, chain: Chain):
        self.chain = chain
        self.root_xprv = settings.root_xprv
        if not self.root_xprv:
            raise RuntimeError(f"HDPAY_ROOT_XPRV is required to sweep {chain.key}")
        check_master_key(self.root_xprv)

    def coins(self, units: int) -> str:
        """An amount of the chain's own coin, readable: 2106400000000 wei is '0.0000021064 BNB'."""
        value = Decimal(units).scaleb(-self.chain.native_decimals)
        text = f"{value:f}"
        text = text.rstrip("0").rstrip(".") if "." in text else text
        return f"{text} {self.chain.native_symbol}"

    def funder_short(self, d: Deposit, funder: str, available: int, needed: int) -> None:
        """The fee funder cannot pay for this deposit's sweep: say so plainly. The deposit waits and is
        tried again on the next pass, so funding the address is all it takes."""
        log.error(
            "fee funder %s on %s has %s but needs %s to sweep %s from deposit %s: send %s to it",
            funder, self.chain.name, self.coins(available), self.coins(needed), d.asset, d.id,
            self.coins(needed - available),
        )

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
