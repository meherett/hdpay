from api.chains import Chain, Family
from .base import Payment, Scanner


def make_scanner(chain: Chain) -> Scanner:
    if chain.family == Family.EVM:
        from .evm import EvmScanner

        return EvmScanner(chain)
    if chain.family == Family.TRON:
        from .tron import TronScanner

        return TronScanner(chain)
    raise ValueError(f"no scanner for family {chain.family}")


__all__ = ["Payment", "Scanner", "make_scanner"]
