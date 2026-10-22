import json
import os

import yaml

import pytest

from api import balances
from api import chains as chains_module
from api.chains import CHAINS, ChainRegistry, ChainsFileError, Family, default_chains_file, follow, load_chains


def write(tmp_path, chains):
    path = tmp_path / "chains.yaml"
    path.write_text(yaml.safe_dump({"chains": chains}), encoding="utf-8")
    return path


def evm_chain(**changes):
    chain = {
        "key": "base", "family": "evm", "name": "Base", "chain_id": 8453,
        "native": {"symbol": "ETH", "decimals": 18}, "confirmations": 10,
        "explorer": "https://basescan.org/", "rpc_url": "https://mainnet.base.org",
        "tokens": [{"symbol": "USDC", "contract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6}],
    }
    return chain | changes


def test_repository_file_has_the_supported_chains():
    assert list(CHAINS) == ["ethereum", "sepolia", "bsc", "bsc-testnet", "tron", "tron-nile"]
    assert CHAINS["bsc"].tokens["USDT"].decimals == 18
    assert CHAINS["tron"].tokens["USDT"].contract == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    assert load_chains(default_chains_file().with_name("chains.example.yaml")) == CHAINS


def test_a_new_chain_and_token_is_just_a_file_entry(tmp_path):
    chains = load_chains(write(tmp_path, [evm_chain()]))
    base = chains["base"]
    assert (base.family, base.chain_id, base.assets()) == (Family.EVM, 8453, ["ETH", "USDC"])
    assert base.explorer == "https://basescan.org"          # trailing slash dropped
    assert base.explorer_address("0xabc") == "https://basescan.org/address/0xabc"


def test_rpc_url_comes_from_env_before_the_file(tmp_path, monkeypatch):
    base = load_chains(write(tmp_path, [evm_chain(key="base-sepolia")]))["base-sepolia"]
    monkeypatch.delenv("HDPAY_RPC_URL_BASE_SEPOLIA", raising=False)
    assert base.rpc_url == "https://mainnet.base.org"
    monkeypatch.setenv("HDPAY_RPC_URL_BASE_SEPOLIA", "https://my-node.example/abc")
    assert base.rpc_url == "https://my-node.example/abc"


@pytest.mark.parametrize("chains, problem", [
    ([evm_chain(chain_id=None)], "needs chain_id"),
    ([evm_chain(family="tron")], "has no chain_id"),
    ([evm_chain(tokens=[{"symbol": "USDC", "contract": "0x123", "decimals": 6}])], "not a valid evm contract address"),
    ([evm_chain(tokens=[{"symbol": "ETH", "contract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6}])], "unique on a chain"),
    ([evm_chain(), evm_chain()], "chain keys must be unique"),
    ([evm_chain(key="Base Mainnet")], "lower-case"),
    ([evm_chain(tokens=[{"symbol": "usdc", "contract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6}])], "capital letters"),
    ([evm_chain(colour="blue")], "Extra inputs are not permitted"),
    ([], "at least 1"),
])
def test_invalid_files_are_refused_with_the_reason(tmp_path, chains, problem):
    with pytest.raises(ChainsFileError, match=problem):
        load_chains(write(tmp_path, chains))


def test_missing_or_broken_file(tmp_path):
    with pytest.raises(ChainsFileError, match="not found"):
        load_chains(tmp_path / "nope.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("chains:\n  - key: base\n   family: evm\n", encoding="utf-8")
    with pytest.raises(ChainsFileError, match="not valid YAML"):
        load_chains(broken)
    empty = tmp_path / "empty.yaml"
    empty.write_text("# nothing yet\n", encoding="utf-8")
    with pytest.raises(ChainsFileError, match="is empty"):
        load_chains(empty)


def test_comments_are_fine_and_an_unquoted_contract_is_explained(tmp_path):
    path = tmp_path / "chains.yaml"
    text = """
chains:
  - key: base            # Coinbase L2
    family: evm
    name: Base
    chain_id: 8453
    native: { symbol: ETH, decimals: 18 }
    confirmations: 10
    explorer: https://basescan.org
    tokens:
      - { symbol: USDC, contract: CONTRACT, decimals: 6 }
"""
    path.write_text(text.replace("CONTRACT", '"0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"'), encoding="utf-8")
    assert load_chains(path)["base"].tokens["USDC"].contract == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    path.write_text(text.replace("CONTRACT", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"), encoding="utf-8")
    with pytest.raises(ChainsFileError, match="put the contract address in quotes"):
        load_chains(path)


def test_a_json_file_still_loads(tmp_path):
    path = tmp_path / "chains.json"
    path.write_text(json.dumps({"chains": [evm_chain()]}), encoding="utf-8")
    assert list(load_chains(path)) == ["base"]


def test_tron_contracts_must_be_base58(tmp_path):
    tron = {
        "key": "tron-x", "family": "tron", "name": "Tron X", "native": {"symbol": "TRX", "decimals": 6},
        "confirmations": 20, "explorer": "https://tronscan.org",
        "tokens": [{"symbol": "USDT", "contract": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6}],
    }
    with pytest.raises(ChainsFileError, match="not a valid tron contract address"):
        load_chains(write(tmp_path, [tron]))


def test_token_check_stops_on_wrong_decimals(tmp_path, monkeypatch):
    base = load_chains(write(tmp_path, [evm_chain()]))["base"]
    monkeypatch.setattr(balances, "token_metadata", lambda chain, token: (18, "USDC"))
    with pytest.raises(balances.TokenMismatch, match="the file says 6 decimals, the contract 18"):
        balances.verify_tokens(base)


def test_token_check_only_notes_a_different_symbol(tmp_path, monkeypatch):
    base = load_chains(write(tmp_path, [evm_chain()]))["base"]
    monkeypatch.setattr(balances, "token_metadata", lambda chain, token: (6, "USDbC"))
    assert balances.verify_tokens(base) == ["USDC at 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 calls itself 'USDbC'"]


def test_token_check_reports_an_unreachable_contract_without_its_url(tmp_path, monkeypatch):
    base = load_chains(write(tmp_path, [evm_chain()]))["base"]

    def unreachable(chain, token):
        raise ConnectionError("failed: https://node.example/SECRET-TOKEN/")

    monkeypatch.setattr(balances, "token_metadata", unreachable)
    with pytest.raises(balances.TokenMismatch) as error:
        balances.verify_tokens(base)
    assert "no answer from the contract (Connection not working)" in str(error.value)
    assert "SECRET-TOKEN" not in str(error.value)


def bump(path, chains):
    # a later mtime, as an editor saving the file gives
    before = path.stat().st_mtime_ns
    path.write_text(yaml.safe_dump({"chains": chains}), encoding="utf-8")
    os.utime(path, ns=(before + 10**9, before + 10**9))


def test_an_edit_to_the_file_is_picked_up(tmp_path):
    path = write(tmp_path, [evm_chain()])
    registry = ChainRegistry(path)
    assert registry.refresh(min_interval=0) is False
    bump(path, [evm_chain(), evm_chain(key="base-sepolia", name="Base Sepolia", chain_id=84532)])
    assert registry.refresh(min_interval=0) is True
    assert list(registry) == ["base", "base-sepolia"]


def test_an_invalid_edit_keeps_the_chains_loaded_before(tmp_path, caplog):
    path = write(tmp_path, [evm_chain()])
    registry = ChainRegistry(path)
    bump(path, [evm_chain(chain_id=None)])
    assert registry.refresh(min_interval=0) is False
    assert list(registry) == ["base"] and "needs chain_id" in caplog.text
    # fixed again: picked up even if the stamp happens to match
    path.write_text(yaml.safe_dump({"chains": [evm_chain(name="Base L2")]}), encoding="utf-8")
    assert registry.refresh(min_interval=0) is True
    assert registry["base"].name == "Base L2"


def test_refresh_looks_at_the_file_at_most_once_a_second(tmp_path):
    path = write(tmp_path, [evm_chain()])
    registry = ChainRegistry(path)
    bump(path, [evm_chain(name="Base L2")])
    assert registry.refresh() is False          # just loaded
    assert registry.refresh(min_interval=0) is True


class Worker:
    def __init__(self, chain):
        self.chain = chain


def test_a_running_worker_follows_its_chain(tmp_path, monkeypatch):
    path = write(tmp_path, [evm_chain()])
    registry = ChainRegistry(path)
    monkeypatch.setattr(chains_module, "CHAINS", registry)
    monkeypatch.setattr(balances, "verify_tokens", lambda chain: [])
    worker = Worker(registry["base"])
    bump(path, [evm_chain(confirmations=20)])
    registry._checked = 0
    worker = follow(worker, Worker)
    assert worker.chain.confirmations == 20


def test_a_worker_keeps_its_settings_when_the_new_tokens_fail_the_check(tmp_path, monkeypatch):
    path = write(tmp_path, [evm_chain()])
    registry = ChainRegistry(path)
    monkeypatch.setattr(chains_module, "CHAINS", registry)

    def mismatch(chain):
        raise balances.TokenMismatch("Base: USDC decimals differ")

    monkeypatch.setattr(balances, "verify_tokens", mismatch)
    worker = Worker(registry["base"])
    bump(path, [evm_chain(confirmations=20)])
    registry._checked = 0
    assert follow(worker, Worker) is worker and worker.chain.confirmations == 10


def test_a_chain_names_its_token_standard(tmp_path):
    from api.chains import ChainsFileError, load_chains

    chains = load_chains()
    assert (chains["ethereum"].token_standard, chains["bsc"].token_standard, chains["tron"].token_standard) == (
        "ERC20", "BEP20", "TRC20",
    )
    bad = tmp_path / "chains.yaml"
    bad.write_text("""chains:
  - key: bsc
    family: evm
    token_standard: bep 20
    name: BNB
    chain_id: 56
    native: { symbol: BNB, decimals: 18 }
    confirmations: 15
    explorer: https://bscscan.com
""", encoding="utf-8")
    with pytest.raises(ChainsFileError, match="token_standard"):
        load_chains(bad)
