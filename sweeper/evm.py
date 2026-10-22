"""
EVM sweeper (Ethereum, Sepolia, BNB Smart Chain).

Native: send (balance - gas) to the treasury. Dust that cannot cover gas is left alone.
ERC-20: the deposit address holds no ETH/BNB, so the project's fee funder (m/{project}'/coin'/{year}) first sends it
exactly the gas the token transfer needs; the next pass sends the tokens.

Gas is always estimated, never assumed: sending ETH to an address that has never been used costs
far more than 21,000 gas on chains with state-growth pricing (about 207,000 on Sepolia in 2026),
and a fresh deposit address is exactly that.
"""

import logging
from typing import Any

from eth_account import Account
from web3 import Web3
from web3.exceptions import TransactionNotFound

from api.chains import Chain, TxStatus
from api.keys import funder_private_key, private_key_for_path
from api.db import Deposit
from .base import Sweeper

log = logging.getLogger("hdpay.sweeper")
GAS_MARGIN_PERCENT = 20
ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}], "outputs": []},
]


class EvmSweeper(Sweeper):
    def __init__(self, chain: Chain):
        super().__init__(chain)
        self.w3 = Web3(Web3.HTTPProvider(chain.rpc_url))
        if self.w3.eth.chain_id != chain.chain_id:
            raise RuntimeError(f"RPC for {chain.key} reports chain id {self.w3.eth.chain_id}, expected {chain.chain_id}")

    def treasury(self, project: dict[str, Any]) -> str | None:
        address = (project.get("treasury") or {}).get(self.chain.family)
        return Web3.to_checksum_address(address) if address else None

    def tx_status(self, tx_hash: str) -> TxStatus:
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return TxStatus.UNKNOWN
        return TxStatus.OK if receipt["status"] == 1 else TxStatus.FAILED

    def _fee_fields(self) -> tuple[dict, int]:
        """Transaction fee fields and the worst-case price per gas."""
        base_fee = self.w3.eth.get_block("latest").get("baseFeePerGas")
        if base_fee is not None:                      # EIP-1559 chain
            tip = self.w3.eth.max_priority_fee
            max_fee = base_fee * 2 + tip
            return {"type": 2, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip}, max_fee
        gas_price = self.w3.eth.gas_price             # legacy gas price chain
        return {"gasPrice": gas_price}, gas_price

    def _gas_for(self, sender: str, to: str, value: int) -> int:
        """Estimated gas for a plain ETH/BNB transfer, with a margin."""
        estimate = self.w3.eth.estimate_gas({"from": sender, "to": to, "value": value})
        return estimate * (100 + GAS_MARGIN_PERCENT) // 100

    def _send(self, account, tx: dict) -> str:
        tx = {"chainId": self.chain.chain_id, "nonce": self.w3.eth.get_transaction_count(account.address, "pending"), **tx}
        signed = account.sign_transaction(tx)
        return Web3.to_hex(self.w3.eth.send_raw_transaction(signed.raw_transaction))

    def _account(self, d: Deposit):
        key = private_key_for_path(self.chain.family, self.master_xprv, d.path)
        account = Account.from_key(key)
        if account.address.lower() != d.address.lower():
            raise RuntimeError(f"derivation mismatch at {d.path}")
        return account

    def sweep(self, d: Deposit, project: dict[str, Any], treasury: str) -> None:
        account = self._account(d)
        if self.chain.is_native(d.asset):
            self._sweep_native(d, account, treasury)
        else:
            self._sweep_token(d, account, treasury, project["number"])

    def _sweep_native(self, d: Deposit, account, treasury: str) -> None:
        balance = self.w3.eth.get_balance(account.address)
        if balance == 0:
            log.info("%s: nothing to sweep", account.address)
            return
        gas = self._gas_for(account.address, treasury, 1)
        fee_fields, gas_price = self._fee_fields()
        value = balance - gas * gas_price
        if value <= 0:
            log.info("%s: balance %s cannot cover gas, skipping", account.address, balance)
            return
        d.sweep_tx_hash = self._send(account, {"to": treasury, "value": value, "gas": gas, **fee_fields})

    def _sweep_token(self, d: Deposit, account, treasury: str, number: int) -> None:
        token = self.chain.tokens[d.asset]
        contract = self.w3.eth.contract(address=Web3.to_checksum_address(token.contract), abi=ERC20_ABI)
        amount = contract.functions.balanceOf(account.address).call()
        if amount == 0:
            log.info("%s: no %s to sweep", account.address, d.asset)
            return

        transfer = contract.functions.transfer(treasury, amount)
        gas = transfer.estimate_gas({"from": account.address}) * (100 + GAS_MARGIN_PERCENT) // 100
        fee_fields, gas_price = self._fee_fields()
        shortfall = gas * gas_price - self.w3.eth.get_balance(account.address)
        if shortfall > 0:
            self._top_up(d, number, account.address, shortfall, fee_fields, gas_price)
            return
        # chainId and nonce are passed so build_transaction does not fill them with extra RPC calls
        tx = transfer.build_transaction(
            {"from": account.address, "gas": gas, "chainId": self.chain.chain_id, "nonce": 0, **fee_fields}
        )
        tx.pop("from", None)
        tx.pop("nonce")
        d.sweep_tx_hash = self._send(account, tx)

    def _funder(self, number: int):
        """The project's funder for this UTC year, looked up on use so a sweeper running over New Year switches."""
        return Account.from_key(funder_private_key(self.chain.family, self.master_xprv, number))

    def _top_up(self, d: Deposit, number: int, to: str, amount: int, fee_fields: dict, gas_price: int) -> None:
        funder = self._funder(number)
        gas = self._gas_for(funder.address, to, amount)
        needed = amount + gas * gas_price
        available = self.w3.eth.get_balance(funder.address)
        if available < needed:
            log.error(
                "fee funder %s has %s, needs %s to sweep %s from %s — fund it",
                funder.address, available, needed, d.asset, to,
            )
            return
        d.fee_tx_hash = self._send(funder, {"to": to, "value": amount, "gas": gas, **fee_fields})
        log.info("topped up %s with %s wei for gas (top-up gas limit %s) in %s", to, amount, gas, d.fee_tx_hash)
