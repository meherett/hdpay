import os

# settings are read at import time, so configure before anything imports hdpay
os.environ["HDPAY_MONGODB_URI"] = "mongodb://in-memory.test"     # replaced by mongomock below
os.environ["HDPAY_MONGODB_DB"] = "hdpay_test"
os.environ["HDPAY_JWT_SECRET"] = "test-secret-" + "0123456789abcdef" * 3
# the committed chains, not the local chains.yaml with private RPCs
os.environ["HDPAY_CHAINS_FILE"] = os.path.join(os.path.dirname(os.path.dirname(__file__)), "chains.example.yaml")
os.environ["HDPAY_REMAINDER_WINDOW_MINUTES"] = "1440"
os.environ["HDPAY_DEPOSIT_TTL_MINUTES"] = "30"
os.environ["HDPAY_AMOUNT_TOLERANCE_BPS"] = "50"

from factories import MASTER  # noqa: E402  (needs the settings above)

os.environ["HDPAY_ROOT_XPRV"] = MASTER

import mongomock  # noqa: E402
import pytest  # noqa: E402

from api import db as hdb  # noqa: E402


# HDPAY_TEST_MONGODB_URI runs the tests against a real MongoDB instead, in a throwaway database that
# is dropped after each test: slower, but it checks the real indexes and queries.
REAL_MONGODB = os.environ.get("HDPAY_TEST_MONGODB_URI")


@pytest.fixture(autouse=True)
def mongo():
    """A fresh MongoDB for every test: in memory, or a throwaway database on HDPAY_TEST_MONGODB_URI."""
    if REAL_MONGODB:
        import uuid

        from pymongo import MongoClient

        client = MongoClient(REAL_MONGODB, serverSelectionTimeoutMS=15_000)
        name = f"hdpay_test_{uuid.uuid4().hex[:10]}"
        os.environ["HDPAY_MONGODB_DB"] = name
        hdb.settings.mongodb_db = name
    else:
        client = mongomock.MongoClient()
    hdb.use_client(client)
    database = hdb.get_db()
    hdb.create_indexes(database)
    yield database
    if REAL_MONGODB:
        client.drop_database(database.name)
        client.close()
    hdb.use_client(None)


@pytest.fixture(autouse=True)
def no_chain_reads(monkeypatch):
    """The API's start checks new tokens against their contracts: in tests, every contract agrees with the file."""
    monkeypatch.setattr("api.chain_sync._verify", lambda chain, token: token.decimals)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as c:
        yield c
