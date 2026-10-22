import pytest
from fastapi.testclient import TestClient

from source.config import Settings
from source.main import create_app
from source.store import Store
from source.users import add, hash_password, main, seed, verify_password


def test_passwords_are_salted_hashes():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert stored != hash_password("correct horse")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "not a hash")


def test_seed_adds_the_sample_users_once(tmp_path):
    store = Store(tmp_path / "shop.db")
    added = seed(store)
    assert [u[0] for u in added] == ["1001", "1002", "1003", "1004", "1005"]
    account_id, name, email, password = added[0]
    user = store.user(account_id)
    assert (user["name"], user["email"]) == (name, email)
    assert verify_password(password, user["password_hash"])
    assert seed(store) == []   # already there: nothing changes


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, hdpay_secret_key="hdp_sk_test", database=tmp_path / "shop.db")
    app = create_app(settings, follow_events=False)
    with TestClient(app) as c:
        yield c


def test_sign_in_and_out(client):
    store = client.app.state.shop.store
    store.add_user("1001", "Ada Lovelace", "ada@example.com", hash_password("analytical"))

    assert client.post("/auth/login", json={"email": "ada@example.com", "password": "nope"}).status_code == 401
    assert client.post("/auth/login", json={"email": "who@example.com", "password": "analytical"}).status_code == 401

    response = client.post("/auth/login", json={"email": "ADA@example.com", "password": "analytical"})
    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {"account_id": "1001", "name": "Ada Lovelace", "email": "ada@example.com"}
    headers = {"Authorization": f"Bearer {body['token']}"}

    assert client.get("/auth/me", headers=headers).json()["account_id"] == "1001"
    assert client.get("/auth/me").status_code == 401
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_a_new_password_signs_the_user_out(client):
    store = client.app.state.shop.store
    store.add_user("1001", "Ada Lovelace", "ada@example.com", hash_password("analytical"))
    token = client.post("/auth/login", json={"email": "ada@example.com", "password": "analytical"}).json()["token"]
    store.set_password("1001", hash_password("difference"))
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_the_command_lists_users(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE", str(tmp_path / "shop.db"))
    assert main(["seed"]) == 0
    assert "password:" in capsys.readouterr().out
    assert main(["list"]) == 0
    assert "ada@example.com" in capsys.readouterr().out


def test_add_takes_the_next_account_id_and_refuses_a_taken_email(tmp_path):
    store = Store(tmp_path / "shop.db")
    seed(store)
    account_id, password = add(store, "new@example.com", "New User")
    assert account_id == "1006" and verify_password(password, store.user("1006")["password_hash"])
    with pytest.raises(ValueError, match="already has an account"):
        add(store, "NEW@example.com", "Again")
    with pytest.raises(ValueError, match="is taken"):
        add(store, "other@example.com", "Other", "1001")
