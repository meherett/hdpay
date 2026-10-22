"""Tron HTTP API client, shared by the watchers, the sweepers and the API's balance reads."""

from tronpy import Tron
from tronpy.providers import HTTPProvider

from .chains import Chain


def tron_http_url(url: str) -> str:
    """Base URL of Tron's HTTP API (/wallet/...), ending in "/".

    Providers such as QuickNode also list a /jsonrpc URL, which only serves Ethereum-style
    calls, so that suffix is dropped. The trailing slash matters: tronpy joins paths like a
    browser, so without it the last segment (often the provider's access token) is replaced.
    """
    return url.rstrip("/").removesuffix("/jsonrpc") + "/"


def make_client(chain: Chain, timeout: float = 10.0) -> Tron:
    return Tron(provider=HTTPProvider(tron_http_url(chain.rpc_url), timeout=timeout))
