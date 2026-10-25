"""chains.yaml kept in the database at every API start, each new or changed token checked on its chain."""

from dataclasses import replace
from datetime import timedelta

from factories import key_headers, make_project
from api.chain_sync import chain_for, confirmed_tokens, refused, sync_chains
from api.chains import CHAINS, Token, deposit_token
from api.db import Deposit, utcnow

TRON = CHAINS["tron"]
USDT = TRON.tokens["USDT"]
OTHER_CONTRACT = "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"
PRODUCTS = [{"value": "100k", "name": "$100,000", "subname": None}]


def agree(chain, token):
    return token.decimals


def says(decimals):
    return lambda chain, token: decimals


def down(chain, token):
    raise ConnectionError("https://secret-token@node.example/ is down")


def with_usdt(**changes):
    return {"tron": replace(TRON, tokens={"USDT": replace(USDT, **changes)})}


def stored_usdt(mongo):
    return mongo.chains.find_one({"_id": "tron"})["tokens"]["USDT"]


def test_a_new_token_the_chain_confirms_is_applied(mongo):
    lines = sync_chains(mongo, {"tron": TRON}, agree)
    token = stored_usdt(mongo)
    assert (token["contract"], token["decimals"], token["supported"], token["refused"]) == (USDT.contract, 6, True, None)
    assert any("new network" in line for _, line in lines)
    assert sync_chains(mongo, {"tron": TRON}, down) == []   # nothing changed: the chain is not asked again


def test_changed_decimals_the_chain_confirms_are_applied_and_the_old_kept(mongo):
    sync_chains(mongo, {"tron": TRON}, agree)
    sync_chains(mongo, with_usdt(decimals=18), agree)
    token = stored_usdt(mongo)
    assert token["decimals"] == 18 and [h["decimals"] for h in token["history"]] == [6]


def test_changed_decimals_the_chain_contradicts_are_refused(client, mongo):
    sync_chains(mongo, {"tron": TRON}, agree)
    lines = sync_chains(mongo, with_usdt(decimals=18), says(6))
    token = stored_usdt(mongo)
    assert token["decimals"] == 6 and token["refused"]["on_chain"] == 6     # the previous values stay
    assert [level for level, _ in lines] == ["error"]
    assert "18 decimals" in refused(mongo, "tron", "USDT")
    # and no deposit opens in it until chains.yaml is fixed
    project = make_project(mongo, products=PRODUCTS)
    reply = client.post("/v1/deposits", headers=key_headers(mongo, project),
                        json={"account_id": "42", "chain": "tron", "asset": "USDT", "product": "100k"})
    assert reply.status_code == 503 and "chains.yaml" in reply.text
    # fixed and restarted: open again
    sync_chains(mongo, {"tron": TRON}, agree)
    assert refused(mongo, "tron", "USDT") is None


def test_a_new_contract_is_applied_and_older_deposits_keep_theirs(mongo):
    sync_chains(mongo, {"tron": TRON}, agree)
    old = Deposit.new(project_id="p", chain="tron", family="tron", asset="USDT", address="TOld",
                      created_at=utcnow() - timedelta(days=1))
    mongo.deposits.insert_one(old.document())
    sync_chains(mongo, with_usdt(contract=OTHER_CONTRACT), agree)
    token = stored_usdt(mongo)
    assert token["contract"] == OTHER_CONTRACT and token["history"][0]["contract"] == USDT.contract
    got = Deposit(mongo.deposits.find_one({"_id": old.id}))
    assert (got.contract, got.decimals) == (USDT.contract, 6)
    # the watcher and sweeper use the deposit's own contract, not the file's new one
    assert deposit_token(replace(TRON, tokens={"USDT": Token("USDT", OTHER_CONTRACT, 6)}), got).contract == USDT.contract


def test_a_new_deposit_keeps_its_contract(client, mongo):
    project = make_project(mongo, products=PRODUCTS)
    reply = client.post("/v1/deposits", headers=key_headers(mongo, project),
                        json={"account_id": "42", "chain": "tron", "asset": "USDT", "product": "100k"})
    stored = mongo.deposits.find_one({"_id": reply.json()["id"]})
    assert (stored["contract"], stored["decimals"]) == (USDT.contract, 6)


def test_an_unreadable_contract_is_not_applied_yet(mongo):
    sync_chains(mongo, {"tron": TRON}, agree)
    [(level, line)] = sync_chains(mongo, with_usdt(decimals=18), down)
    assert level == "warning" and "next start" in line and "secret" not in line
    assert stored_usdt(mongo)["decimals"] == 6 and refused(mongo, "tron", "USDT") is None


def test_a_token_taken_out_of_the_file_is_kept(mongo):
    sync_chains(mongo, {"tron": TRON}, agree)
    sync_chains(mongo, {"tron": replace(TRON, tokens={})}, agree)
    token = stored_usdt(mongo)
    assert token["supported"] is False and token["supported_until"] is not None and token["contract"] == USDT.contract


def test_a_network_taken_out_of_the_file_still_reads(mongo):
    sync_chains(mongo, {"gone": replace(TRON, key="gone", name="Gone Chain")}, agree)
    sync_chains(mongo, {"tron": TRON}, agree)
    assert mongo.chains.find_one({"_id": "gone"})["supported"] is False
    chain = chain_for("gone")
    assert chain.name == "Gone Chain" and chain.decimals("USDT") == 6
    assert chain_for("never-seen", "evm").name == "never-seen"   # a stand-in, never a crash


def test_a_starting_watcher_asks_the_chain_only_about_new_or_changed_tokens(mongo, monkeypatch):
    from api import balances

    asked = []
    monkeypatch.setattr(balances, "token_metadata", lambda chain, token: (asked.append(token.symbol), (token.decimals, token.symbol))[1])
    sync_chains(mongo, {"tron": TRON}, agree)
    assert balances.verify_tokens(TRON, confirmed_tokens(TRON, mongo)) and asked == []    # confirmed before
    usdc = Token("USDC", OTHER_CONTRACT, 6)
    added = replace(TRON, tokens={"USDT": USDT, "USDC": usdc})
    balances.verify_tokens(added, confirmed_tokens(added, mongo))
    assert asked == ["USDC"]                                                              # only the new one
    changed = replace(TRON, tokens={"USDT": replace(USDT, decimals=18)})
    assert confirmed_tokens(changed, mongo) == set()                                      # a change is asked again


def test_a_token_back_in_the_file_is_supported_again(mongo):
    sync_chains(mongo, {"tron": TRON}, agree)
    sync_chains(mongo, {"tron": replace(TRON, tokens={})}, agree)
    lines = sync_chains(mongo, {"tron": TRON}, down)       # same contract and decimals: not asked again
    token = stored_usdt(mongo)
    assert (token["supported"], token["supported_until"]) == (True, None)
    assert [line for _, line in lines] == ["tron USDT: back in chains.yaml, supported again"]


def test_an_earlier_listed_flag_becomes_supported(mongo):
    sync_chains(mongo, {"tron": TRON}, agree)
    mongo.chains.update_one({"_id": "tron"}, {"$set": {"listed": True, "tokens.USDT.listed": True},
                                               "$unset": {"supported": "", "tokens.USDT.supported": ""}})
    sync_chains(mongo, {"tron": TRON}, down)
    doc = mongo.chains.find_one({"_id": "tron"})
    assert "listed" not in doc and "listed" not in doc["tokens"]["USDT"]
    assert doc["supported"] is True and doc["tokens"]["USDT"]["supported"] is True


def test_the_admin_lists_networks_and_coins_supported_now_and_before(client, mongo, monkeypatch):
    from factories import make_user, sign_in

    make_user(mongo, role="viewer")
    bsc = CHAINS["bsc"]
    old = Token("DAI", "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3", 18)   # accepted once, not in the file now
    sync_chains(mongo, {**CHAINS, "bsc": replace(bsc, tokens={**bsc.tokens, "DAI": old})}, agree)
    mongo.deposits.insert_one(Deposit.new(project_id="p", chain="bsc", family="evm", asset="DAI",
                                          address="0xold").document())
    sync_chains(mongo, dict(CHAINS), agree)                  # DAI taken out of chains.yaml for bsc
    listed = {n["key"]: n for n in client.get("/admin/networks", headers=sign_in(client)).json()}
    coins = {a["symbol"]: a for a in listed["bsc"]["assets"]}
    assert (coins["DAI"]["status"], coins["DAI"]["supported"], coins["DAI"]["deposits"]) == ("not supported", False, 1)
    assert coins["DAI"]["supported_until"] and coins["DAI"]["contract"] == old.contract
    assert coins["USDT"]["status"] == "supported" and coins["BNB"]["native"] is True
    assert listed["bsc"]["supported"] is True
