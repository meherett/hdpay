"""A web3 client for an EVM chain, shared by the watchers, the sweepers and the balance reads."""

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from .chains import Chain


def web3_for(chain: Chain, timeout: float | None = None) -> Web3:
    """BNB Smart Chain is proof of authority: its block headers carry more than the 32 bytes of extraData
    web3 accepts by default, so reading a block fails without this middleware. It is harmless elsewhere."""
    w3 = Web3(Web3.HTTPProvider(chain.rpc_url, request_kwargs={"timeout": timeout} if timeout else None))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3
