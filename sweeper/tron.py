"""
Tron sweeper (mainnet, Nile).

TRX: send the balance minus a small bandwidth reserve to the treasury.
TRC-20: a deposit address that only received tokens is not activated and holds no TRX, so
the project's fee funder (m/{project}'/coin'/{year}) first sends it the TRX the transfer will burn for energy and bandwidth.
That transfer also activates the account. The next pass sends the tokens.
"""

import logging
from typing import Any

from tronpy.exceptions import AddressNotFound, TransactionNotFound
from tronpy.keys import PrivateKey, to_base58check_address, to_hex_address, to_tvm_address

from api.chains import Chain, Token, TxStatus, deposit_token
from api.keys import funder_private_key, private_key_for_path
from api.db import Deposit
from api.tron import make_client
from .base import Sweeper

log = logging.getLogger("hdpay.sweeper")
TRX_SWEEP_RESERVE_SUN = 300_000       # covers bandwidth (~0.27 TRX) once the free daily allowance is used
TOKEN_BANDWIDTH_SUN = 500_000         # bandwidth burned by a TRC-20 transfer (~345 bytes)
ENERGY_MARGIN_PERCENT = 20
FALLBACK_ENERGY = 130_000             # USDT transfer to a new holder; used if estimation returns nothing
TRANSFER_SELECTOR = "a9059cbb"        # transfer(address,uint256)


def _transfer_parameter(to: str, amount: int) -> str:
    return to_tvm_address(to).hex().rjust(64, "0") + f"{amount:064x}"


class TronSweeper(Sweeper):
    def __init__(self, chain: Chain):
        super().__init__(chain)
        self.client = make_client(chain)

    def treasury(self, project: dict[str, Any]) -> str | None:
        address = (project.get("treasury") or {}).get(self.chain.family)
        return to_base58check_address(address) if address else None

    def tx_status(self, tx_hash: str) -> TxStatus:
        try:
            info = self.client.get_solid_transaction_info(tx_hash)
        except TransactionNotFound:
            return TxStatus.UNKNOWN
        return TxStatus.FAILED if info.get("result") == "FAILED" else TxStatus.OK

    def _balance(self, address: str) -> int:
        try:
            return self.client.get_account(address).get("balance", 0)
        except AddressNotFound:
            return 0

    def _key(self, d: Deposit) -> PrivateKey:
        hex_key = private_key_for_path(self.chain.family, self.master_xprv, d.path)
        key = PrivateKey(bytes.fromhex(hex_key))
        if key.public_key.to_base58check_address() != d.address:
            raise RuntimeError(f"derivation mismatch at {d.path}")
        return key

    def sweep(self, d: Deposit, project: dict[str, Any], treasury: str) -> None:
        key = self._key(d)
        if self.chain.is_native(d.asset):
            self._sweep_native(d, key, treasury)
        else:
            self._sweep_token(d, key, deposit_token(self.chain, d), treasury, project["number"])

    def _sweep_native(self, d: Deposit, key: PrivateKey, treasury: str) -> None:
        balance = self._balance(d.address)
        value = balance - TRX_SWEEP_RESERVE_SUN
        if value <= 0:
            log.info("%s: balance %s sun cannot cover bandwidth, skipping", d.address, balance)
            return
        txn = self.client.trx.transfer(d.address, treasury, value).build().sign(key)
        d.sweep_tx_hash = txn.broadcast().txid

    def _token_balance(self, address: str, token: Token) -> int:
        result = self.client.trigger_const_smart_contract_function(
            address, token.contract, "balanceOf(address)", to_tvm_address(address).hex().rjust(64, "0")
        )
        return int(result or "0", 16)

    def _transfer_fee(self, owner: str, token: Token, amount: int, treasury: str) -> int:
        """TRX (in sun) the token transfer will burn, with a margin."""
        estimate = self.client.trigger_constant_contract(
            owner, token.contract, "transfer(address,uint256)", _transfer_parameter(treasury, amount)
        )
        energy = estimate.get("energy_used") or FALLBACK_ENERGY
        params = {p["key"]: p.get("value", 0) for p in self.client.get_chain_parameters()}
        energy_price = params["getEnergyFee"]
        return energy * energy_price * (100 + ENERGY_MARGIN_PERCENT) // 100 + TOKEN_BANDWIDTH_SUN

    def _sweep_token(self, d: Deposit, key: PrivateKey, token: Token, treasury: str, number: int) -> None:
        amount = self._token_balance(d.address, token)
        if amount == 0:
            log.info("%s: no %s to sweep", d.address, d.asset)
            return

        fee = self._transfer_fee(d.address, token, amount, treasury)
        shortfall = fee - self._balance(d.address)
        if shortfall > 0:
            self._top_up(d, number, shortfall)
            return

        txn = (
            self.client.trx._build_transaction(
                "TriggerSmartContract",
                {
                    "owner_address": key.public_key.to_hex_address(),
                    "contract_address": to_hex_address(token.contract),
                    "data": TRANSFER_SELECTOR + _transfer_parameter(treasury, amount),
                },
            )
            .fee_limit(fee)
            .build()
            .sign(key)
        )
        d.sweep_tx_hash = txn.broadcast().txid

    def _funder_key(self, number: int) -> PrivateKey:
        """The project's funder for this UTC year, looked up on use so a sweeper running over New Year switches."""
        return PrivateKey(bytes.fromhex(funder_private_key(self.chain.family, self.master_xprv, number)))

    def _top_up(self, d: Deposit, number: int, amount: int) -> None:
        funder_key = self._funder_key(number)
        funder = funder_key.public_key.to_base58check_address()
        available = self._balance(funder)
        # sending to a new account burns an extra ~1.1 TRX of the funder's balance to activate it
        needed = amount + 1_100_000 + TRX_SWEEP_RESERVE_SUN
        if available < needed:
            self.funder_short(d, funder, available, needed)
            return
        txn = self.client.trx.transfer(funder, d.address, amount).build().sign(funder_key)
        d.fee_tx_hash = txn.broadcast().txid
        log.info("topped up %s with %s sun for fees in %s", d.address, amount, d.fee_tx_hash)
