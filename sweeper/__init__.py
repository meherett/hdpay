from api.chains import Chain, Family
from .base import Sweeper


def make_sweeper(chain: Chain) -> Sweeper:
    if chain.family == Family.EVM:
        from .evm import EvmSweeper

        return EvmSweeper(chain)
    if chain.family == Family.TRON:
        from .tron import TronSweeper

        return TronSweeper(chain)
    raise ValueError(f"no sweeper for family {chain.family}")


__all__ = ["Sweeper", "make_sweeper"]
