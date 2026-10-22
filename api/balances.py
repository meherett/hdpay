"""Balances (native coins and tokens), read straight from each chain's RPC for the admin app."""

import logging
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3

from .chains import Chain, Family

log = logging.getLogger("hdpay.balances")
RPC_TIMEOUT_SECONDS = 8
_BALANCE_OF_ABI = [{
    "name": "balanceOf", "type": "function", "stateMutability": "view",
    "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}],
}]


def _web3(chain: Chain) -> Web3:
    return Web3(Web3.HTTPProvider(chain.rpc_url, request_kwargs={"timeout": RPC_TIMEOUT_SECONDS}))


def balance(chain: Chain, asset: str, address: str) -> int:
    """Balance of `asset` (the native symbol or a token symbol) in its smallest unit."""
    if chain.family == Family.EVM:
        w3 = _web3(chain)
        owner = Web3.to_checksum_address(address)
        if chain.is_native(asset):
            return w3.eth.get_balance(owner)
        token = w3.eth.contract(address=Web3.to_checksum_address(chain.tokens[asset].contract), abi=_BALANCE_OF_ABI)
        return token.functions.balanceOf(owner).call()

    if chain.family == Family.TRON:
        from tronpy.exceptions import AddressNotFound
        from tronpy.keys import to_base58check_address, to_tvm_address

        from .tron import make_client

        client = make_client(chain, timeout=RPC_TIMEOUT_SECONDS)
        owner = to_base58check_address(address)
        if chain.is_native(asset):
            try:
                return client.get_account(owner).get("balance", 0)
            except AddressNotFound:
                return 0   # never received anything, so not activated yet
        result = client.trigger_const_smart_contract_function(
            owner, chain.tokens[asset].contract, "balanceOf(address)", to_tvm_address(owner).hex().rjust(64, "0")
        )
        return int(result or "0", 16)

    raise ValueError(f"no balance lookup for family {chain.family}")


def native_balance(chain: Chain, address: str) -> int:
    """Native coin balance (wei, sun)."""
    return balance(chain, chain.native_symbol, address)


def rpc_problem(exc: BaseException) -> str:
    """A short, plain reason an RPC call failed, safe to show anywhere.

    Never the exception's own text: RPC URLs carry access tokens, and error messages repeat the URL
    in pieces (host here, path there) that no pattern reliably removes.
    """
    kinds = {cls.__name__ for cls in type(exc).__mro__}
    text = str(exc).lower()
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403) or any(word in text for word in ("401", "403", "unauthorized", "forbidden")):
        return "Access refused: check the RPC URL"
    if status == 429 or "429" in text or "too many requests" in text or "rate limit" in text:
        return "Rate limited: try again shortly"
    if kinds & {"Timeout", "ReadTimeout", "ConnectTimeout", "TimeoutException", "TimeoutError"}:
        return "No answer in time"
    if kinds & {"ConnectionError", "ConnectError", "NetworkError", "OSError"}:
        return "Connection not working"
    if isinstance(status, int):
        return f"RPC error (HTTP {status})"
    return "Could not read"


def _safe(fetch, *args) -> tuple[int | None, str | None]:
    try:
        return fetch(*args), None
    except Exception as exc:
        problem = rpc_problem(exc)
        log.warning("balance read failed: %s (%s)", problem, type(exc).__name__)
        return None, problem


def _parallel(jobs: list) -> list[tuple[int | None, str | None]]:
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        return list(pool.map(lambda job: _safe(*job), jobs))


def native_balances(lookups: list[tuple[Chain, str]]) -> list[tuple[int | None, str | None]]:
    """(balance, error) per (chain, address), fetched in parallel; one slow or failing RPC only affects its row."""
    # looked up through the module so tests can replace native_balance
    return _parallel([(lambda c, a: native_balance(c, a), chain, address) for chain, address in lookups])


def asset_balances(lookups: list[tuple[Chain, str, str]]) -> list[tuple[int | None, str | None]]:
    """(balance, error) per (chain, asset, address), fetched in parallel."""
    return _parallel([(lambda c, s, a: balance(c, s, a), chain, asset, address) for chain, asset, address in lookups])


_METADATA_ABI = [
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "string"}]},
]


def token_metadata(chain: Chain, token) -> tuple[int, str]:
    """(decimals, symbol) as the token contract itself reports them."""
    if chain.family == Family.EVM:
        contract = _web3(chain).eth.contract(address=Web3.to_checksum_address(token.contract), abi=_METADATA_ABI)
        return contract.functions.decimals().call(), contract.functions.symbol().call()

    if chain.family == Family.TRON:
        from eth_abi import decode

        from .tron import make_client

        client = make_client(chain, timeout=RPC_TIMEOUT_SECONDS)
        decimals = int(client.trigger_const_smart_contract_function(token.contract, token.contract, "decimals()", "") or "0", 16)
        raw = client.trigger_const_smart_contract_function(token.contract, token.contract, "symbol()", "")
        return decimals, decode(["string"], bytes.fromhex(raw))[0] if raw else ""

    raise ValueError(f"no token lookup for family {chain.family}")


class TokenMismatch(RuntimeError):
    """A token in the chains file does not match its contract on chain."""


def verify_tokens(chain: Chain) -> list[str]:
    """Check every token of the chain against its contract before a watcher or sweeper starts.

    Wrong decimals would credit wrong amounts, so they stop the process; a different symbol is
    only reported, since some tokens name themselves differently (e.g. USDT0, USD₮).
    """
    notes, problems = [], []
    for token in chain.tokens.values():
        try:
            decimals, symbol = token_metadata(chain, token)
        except Exception as exc:
            problems.append(f"{token.symbol} at {token.contract}: no answer from the contract ({rpc_problem(exc)})")
            continue
        if decimals != token.decimals:
            problems.append(f"{token.symbol} at {token.contract}: the file says {token.decimals} decimals, the contract {decimals}")
        if symbol.upper() != token.symbol.upper():
            notes.append(f"{token.symbol} at {token.contract} calls itself {symbol!r}")
        else:
            notes.append(f"{token.symbol} at {token.contract}: {decimals} decimals, ok")
    if problems:
        raise TokenMismatch(f"{chain.name}: " + "; ".join(problems) + ". Fix the chains file.")
    return notes
