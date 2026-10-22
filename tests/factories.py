"""Test data: the "abandon ... about" wallet, projects, users and API keys."""

from datetime import date

from hdwallet import HDWallet
from hdwallet.cryptocurrencies import Ethereum
from hdwallet.derivations import CustomDerivation
from hdwallet.hds import BIP32HD
from hdwallet.mnemonics import BIP39Mnemonic

PHRASE = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
MASTER = (
    HDWallet(cryptocurrency=Ethereum, hd=BIP32HD, network=Ethereum.NETWORKS.MAINNET)
    .from_mnemonic(mnemonic=BIP39Mnemonic(mnemonic=PHRASE))
    .root_xprivate_key()
)
NUMBER = 48213
PASSWORD = "correct horse battery"
EVM_TREASURY = "0xBC9b9a6D18435bf42a87D40265Eaf676638726F8"
TRON_TREASURY = "TR24tqeWeHmDamCZDvAaDHDUub6QAH8Uu2"
CHAINS = {
    "tron": ["TRX", "USDT"],
    "tron-nile": ["TRX", "USDT"],
    "sepolia": ["ETH", "USDC"],
    "bsc": ["BNB", "USDT", "USDC"],
}


def wallet_address(cryptocurrency, path: str) -> str:
    """An address straight from the mnemonic, to check derivation against."""
    return (
        HDWallet(cryptocurrency=cryptocurrency, hd=BIP32HD, network=cryptocurrency.NETWORKS.MAINNET)
        .from_mnemonic(mnemonic=BIP39Mnemonic(mnemonic=PHRASE))
        .from_derivation(derivation=CustomDerivation(path=path))
        .address()
    )


def make_project(db, number=NUMBER, name="Shop", products=None, chains=None, **fields):
    """An active project with its keys in place, inserted directly."""
    from api.chains import Family
    from api.db import utcnow
    from api.keys import project_xpub
    from api.projects import ProjectStatus

    doc = {
        "_id": f"{number:024x}",   # 24 hex characters, like a MongoDB ObjectId
        "name": name,
        "number": number,
        "status": ProjectStatus.ACTIVE,
        "xpubs": {f.value: project_xpub(MASTER, number, f) for f in Family},
        "treasury": {"evm": EVM_TREASURY, "tron": TRON_TREASURY},
        "chains": CHAINS if chains is None else chains,
        "products": products or [],
        "allowed_ips": [],
        "events_acked": 0,
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "created_by": None,
        **fields,
    }
    db.projects.insert_one(doc)
    return doc


def make_user(db, email="admin@example.com", role="admin", name="Ada"):
    from api.auth import new_user

    return new_user(db, email, name, PASSWORD, role)


def sign_in(client, email="admin@example.com"):
    """Authorization headers for a user created with make_user."""
    reply = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert reply.status_code == 200, reply.text
    return {"Authorization": f"Bearer {reply.json()['access_token']}"}


def key_headers(db, project, kind="secret"):
    from api.apikeys import new_key

    _, plain = new_key(db, project["_id"], kind, f"{kind} key", None)
    return {"Authorization": f"Bearer {plain}"}


def today() -> date:
    from api.db import utcnow

    return utcnow().date()
