# HDPay

Hierarchical Deterministic (HD) Pay: a payment gateway for EVM chains and Tron, serving many projects
from one installation. Every deposit gets its own address, derived from the project's branch of an HD
wallet, so incoming payments are matched to customers without memos or shared addresses. Accepts
native coins and stablecoins (USDT, USDC).

Built on [Python-HDWallet](https://github.com/hdwallet-io/python-hdwallet)

## Supported chains and assets

| Chain key     | Network                 | Native | Tokens            | Confirmations |
|---------------|-------------------------|--------|-------------------|---------------|
| `ethereum`    | Ethereum mainnet        | ETH    | USDT, USDC        | 12            |
| `sepolia`     | Sepolia testnet         | ETH    | USDC (Circle)     | 6             |
| `bsc`         | BNB Smart Chain         | BNB    | USDT, USDC        | 15            |
| `bsc-testnet` | BNB Smart Chain testnet | BNB    | —                 | 15            |
| `tron`        | Tron mainnet            | TRX    | USDT (TRC-20)     | 20            |
| `tron-nile`   | Tron Nile testnet       | TRX    | USDT (TRC-20)     | 20            |

These come from **`chains.yaml`** (or the file named by `HDPAY_CHAINS_FILE`), not from code. Each chain
lists its key, family (`evm` or `tron`), chain id (EVM), native coin, confirmations, explorer, a public
default RPC and its tokens. It is YAML so it can carry comments:

```yaml
chains:
  - key: sepolia
    family: evm
    name: Sepolia
    chain_id: 11155111
    native:
      symbol: ETH
      decimals: 18
    confirmations: 6              # blocks before a payment is credited
    explorer: https://sepolia.etherscan.io
    rpc_url: https://ethereum-sepolia-rpc.publicnode.com
    tokens:
      - symbol: USDC
        contract: "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"   # quoted, see below
        decimals: 6
```

- **Add an ERC-20 or TRC-20** by adding it to a chain's `tokens`; **add a chain** of a known family with
  a new entry. No code changes, and no restart (see below).
- **`token_standard`** names a chain's tokens where they are shown and filtered: ERC20 on EVM chains and
  TRC20 on Tron by default, `BEP-20` on BNB Smart Chain. The admin's deposit list filters by Native or by
  standard (`GET /admin/deposits?type=BEP-20`).
- **Quote every `0x…` contract address.** YAML reads an unquoted one as a number; the loader refuses
  it and says so. Tron addresses (`T…`) need no quotes.
- `chains.yaml` is git-ignored, so its `rpc_url`s can be private endpoints with access tokens. The
  committed template is **`chains.example.yaml`** (public RPCs, every field explained in comments):
  `cp chains.example.yaml chains.yaml`, then put your endpoints in. Without a `chains.yaml`, HDPay runs
  on the template. `HDPAY_RPC_URL_<KEY>` in `envs/common.env` (dashes become underscores, e.g.
  `HDPAY_RPC_URL_TRON_NILE`) overrides any file's `rpc_url`.
- The file is checked on load: unique chain keys and symbols, valid contract addresses for the family,
  a chain id for EVM chains, no unknown fields. Errors name the field.
- Edits apply without a restart. The API reads the file again within a second of a save, and open
  admin and example pages ask for the chains every 3 seconds, so their network and asset lists follow
  (a choice that no longer exists is cleared). Watchers and sweepers pick up their chain's changes
  between passes, after checking its tokens against their contracts. An invalid save is logged and
  ignored: everything keeps the last valid chains until the file is fixed.
- Each watcher and sweeper reads every token's `decimals()` and `symbol()` from its contract at start,
  and **refuses to start if the decimals differ** from the file, since that would credit wrong amounts.

Note that USDT and USDC on BNB Smart Chain use **18 decimals**, not 6. Circle no longer issues
USDC on Tron, so it is not listed there.

## How it works

HDPay is one service for many **projects**: each app that takes payments is a project, created in the
admin, with its own products, accepted networks, treasury, API keys and branch of the HD wallet. A
project's backend opens deposits through the gateway API with its key, and follows what happens to them
over a WebSocket. HDPay sends no email: the project tells its own customers.

### Derivation

Every project has a random number, chosen when it is created, that starts all of its paths:

```
m/{project}'/60'      evm: 0x... addresses, identical on Ethereum, Sepolia and BNB Smart Chain
m/{project}'/195'     tron: T... addresses, identical on Tron mainnet and Nile

  m/{project}'/{coin}'/{account}/{seconds}/{microseconds}   deposit, e.g. m/48213'/195'/42/25707922/123456
  m/{project}'/{coin}'                                      the project's fee funder (the coin node itself)
```

- **Project:** a hardened level, so one project's keys reveal nothing about another's.
- **Account ID:** the project's own number for its customer, 0 to 2,147,483,647 (a path level cannot
  hold text). Account 42 in one project has nothing to do with account 42 in another.
- **Seconds and microseconds:** the moment the address was made, so every address is for that one
  moment only. `{seconds}` counts from 2026-01-01 00:00 UTC (a path level holds up to 2,147,483,647, which
  lasts until 2094-01-19); `{microseconds}` is the part of that second, 0 to 999,999. The example above is
  account 42 at 2026-10-25 13:05:22.123456 UTC. A path is **never issued twice**: addresses are unique in
  the database, and in the all but impossible case of the same account in the same microsecond, the next
  microsecond is taken.
- **Before 0.3.0** paths were `m/{project}'/{coin}'/{account}/{year}/{month}/{day}/{index}` (the UTC day
  and the account's counter for it). Every deposit keeps the path it was made with, and the sweeper signs
  with that stored path, so both kinds are watched and swept the same way.
- **Back up MongoDB.** A path made from a moment cannot be guessed, so the deposits in the database are the
  record of which addresses hold money: without them the master key alone would not find them.
- **Keys:** the API holds only each project's xpubs (`m/{project}'/60'`, `m/{project}'/195'`): it derives
  addresses but cannot spend. The sweepers hold the master key (`HDPAY_MASTER_XPRV`, the root): they are
  the only processes that can move funds. Because the project level is hardened, a project's xpubs can
  only be made from the master key, so they are exported once, on the sweeper host:

  ```bash
  python -m api.keys project 48213     # prints the project's two xpubs, to paste into the admin
  ```

- These paths are not BIP44, so wallets like MetaMask will not find the addresses from the mnemonic.
  Each deposit stores its full path; with the mnemonic, any address can be re-derived from it, and the
  sweeper signs with the key at the stored path.

### Processes

One database (MongoDB), one API, and one watcher and one sweeper **per chain** for every project, so a
slow node on one chain never holds up another:

| Process                             | Role                                                             |
|-------------------------------------|------------------------------------------------------------------|
| `python -m api`                         | Gateway for projects, the admin API, admin sign-in, event stream |
| `python -m watcher --chain <key>`       | Scans new blocks for payments, confirms them after N blocks      |
| `python -m sweeper --chain <key>`       | Moves confirmed deposits to their project's treasury             |

### Deposit lifecycle

```
pending -> expired -> detected -> confirmed -> swept
                          ^   \
                          |    -> partial  (underpaid: the rest may come until the window ends)
                          |          |  \
                          +----------+   -> review  (window ended: closed, customer contacts support)
                        the rest arrives
```

| Status         | Meaning                                                    | Public status       | Event              |
|----------------|------------------------------------------------------------|---------------------|--------------------|
| `pending`      | Address issued, inside the project's payment window        | `pending`           | `deposit.created`  |
| `expired`      | Window passed with nothing received — **still watched**    | `expired`           | `deposit.expired`  |
| `detected`     | Payment seen, waiting for confirmations                    | `confirming`        | `deposit.detected` |
| `partial`      | Underpaid; **watched** until the remainder window ends     | `partial`           | `deposit.partial`  |
| `confirmed`    | Paid: credit the account                                   | `paid`              | `deposit.confirmed`|
| `swept`        | Funds moved to the project's treasury                      | `paid`              | `deposit.swept`    |
| `review` | The rest never came — **not watched**; support decides     | `contact_support`   | `deposit.review`   |

- A late payment of the right amount to an expired address is credited automatically (`late = true`).
- An expired deposit that never received anything is **deleted** `HDPAY_EXPIRED_DELETE_AFTER_HOURS` after
  it expired (360, 15 days, by default; `0` keeps them all). Its chain's watcher reads the address's balance one last
  time first: if anything arrived after all, it is counted and the deposit kept. After that a payment to
  the address is no longer watched, and the project gets `deposit.deleted`.
- Support can **reopen** an `expired` deposit from the admin, before it is deleted: back to `pending` on the
  same address, with a new payment window (the project's, or one support sets), so a payment in it is on
  time; the project gets `deposit.reopened`. Search the admin's deposits by the address the customer paid to.
- An underpayment becomes `partial`: the same address waits for the rest for the project's remainder
  window, counted from the short payment. If the rest arrives in time the deposit is credited and swept.
  A reorg of that later payment rolls back only that payment, never the part already confirmed.
- If the window ends first, the deposit goes to `review` and its address is **no longer watched**.
  Support either **reopens** it (back to `partial` with a fresh window and, if agreed, a different amount
  or product; the project gets `deposit.reopened`) or **credits** it as it is. A `partial` deposit also
  gets a balance check about once a minute, tokens included, so anything sent while the address was
  closed is counted.
- Each account has at most one open deposit per network, asset and product; asking again returns it.

### Discounts

A project can give **one customer a discount on one address**: it opens the deposit with
`discount_percent` (1 to 99) and `discount_ends_at` (in the future), on a product that has a price.
Products themselves carry no discount.

```json
POST /v1/deposits
{ "account_id": "42", "product": "100k", "chain": "tron", "asset": "USDT",
  "discount_percent": 20, "discount_ends_at": "2026-10-31T18:00:00Z" }
```

- The address expects the discounted price (rounded half up to the cent) and keeps the discount with it:
  percent, end, full and discounted amounts, and its state. The same account asking again with the same
  discount gets the same open address; a different discount gets its own.
- Payments **made on the chain** before the end count toward the discount, even if HDPay sees or
  confirms them a little later.
- Paid in full in time: the discount is **kept**. Still short when it ends: it **lapses** and the address
  expects the full price, so the customer owes the full price less what they sent. The project gets
  `deposit.discount_expired`.
- Nothing received when it ends: the address keeps its discounted amount and shows the discount as
  ended. A payment that arrives after the end lapses it, so a late payer owes the full price.
- An address already in review when its discount ends: **reopen** asks for the rest of the full price,
  unless support ticks *keep the discount* (state **honored**). The admin shows each address's discount,
  its state and what is owed now, and tags discounted addresses in the deposit list.
- Every deposit carries its `discount` (`percent`, `ends_at`, `full_amount`, `amount`, `status`), or null.

### Watcher

- **Native coins:** EVM transactions whose `to` is a deposit address; Tron `TransferContract` transactions.
- **Tokens:** ERC-20 / TRC-20 `Transfer` events from the listed token contracts.
- **Reorg guard:** after the chain's confirmations the transaction is re-checked. On Tron it must be in a
  solidified block. If it vanished or failed, the deposit goes back to what was already confirmed.
- **Balance check:** about once a minute, once caught up, for internal ETH/BNB transfers and for
  partial deposits of any asset.
- **Wrong asset:** e.g. TRX sent to a USDT deposit address is logged and not credited.
- **Tolerance:** an underpayment within the project's tolerance (50 basis points = 0.50% by default) is paid.

### Sweeper

- **Native coins:** sends `balance - fee` to the project's treasury. Dust that cannot cover the fee stays.
- **Tokens:** a deposit address that only received tokens cannot pay for the transfer (and on Tron is not
  even activated). The sweeper estimates the fee, the **project's fee funder** sends the shortfall, and
  the next pass sends the tokens. Keep each project's funders topped up: roughly 8–15 TRX per Tron USDT
  sweep, about 65k gas per ERC-20 sweep. The admin shows their balances on the project's page.
- A project without a treasury for a family leaves those deposits waiting; nothing is guessed.
- A deposit is `swept` only once the sweep has succeeded on chain. A top-up or sweep that fails on chain
  puts the deposit **on hold** until support releases it; nothing that failed is resent in a loop.

## Setup

Requires Python 3.14+ and MongoDB (local, Docker or Atlas).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp envs/common.env.example envs/common.env     # HDPAY_MONGODB_URI, HDPAY_JWT_SECRET, ...
cp chains.example.yaml chains.yaml             # then put your private RPC URLs in it
```

**The master key**, once, ideally offline, on the host that will run the sweepers:

```bash
python -m api.keys master --save
```

It asks for your recovery phrase (hidden; leave it empty to create a new wallet, whose phrase is shown
once) and writes only `HDPAY_MASTER_XPRV=` to `envs/sweeper.env`, in any shell. Keep the phrase offline:
the services never need it, and `envs/sweeper.env` never goes on the API host.

**The first user**, on the API host, as the owner:

```bash
python -m api.manage create-admin --email you@example.com --name "Your Name" --owner
python -m api.manage set-role --email someone@example.com --role owner   # owner, admin or viewer
python -m api.manage migrate-project-ids     # once, for projects from before MongoDB ids (prj_...)
```

Then sign in to the admin app, add the other users, and create the projects.

### A new project

1. **Admin → Projects → New project** (owners only): name, products (with an optional USD price),
   accepted networks and assets, treasury addresses, timing, allowed IPs. It gets its random number.
2. **Keys:** a running sweeper gives the project its two xpubs from the master key within about 30
   seconds, and the project becomes active; the master key never leaves the sweeper's host. With no
   sweeper running, run `python -m api.keys project <number>` there and paste them on the Keys tab.
3. **API keys:** create a secret key (`hdp_sk_…`) for the project's backend, and a publishable key
   (`hdp_pk_…`) if it has a checkout page in the browser. Each key is shown once.
4. **Fee funders:** the project's Balances tab shows its funder addresses; send them ETH/BNB and TRX.
   A new funder takes over every 1 January: top it up, and move what is left on the old one by hand.

## Configuration

All settings are environment variables with the `HDPAY_` prefix, kept in `envs/` (git-ignored; each has a
committed `.example` template):

| File                  | Read by                        | Holds                                                    |
|-----------------------|--------------------------------|----------------------------------------------------------|
| `envs/common.env`     | api, watchers, sweepers        | MongoDB, chains and RPC overrides, project defaults, and the API's sign-in secret, port and browser origins |
| `envs/sweeper.env`    | sweepers (and `api.keys`)      | `HDPAY_MASTER_XPRV`, the master key, and nothing else    |

Run locally, every process reads both; in Docker only the sweepers get `sweeper.env`, so the API never
holds the master key. A variable set in the environment wins over the files.

| Variable                         | Default                     | Notes                                      |
|----------------------------------|-----------------------------|--------------------------------------------|
| `HDPAY_MONGODB_URI`              | `mongodb://localhost:27017` | Shared by the API, watchers and sweepers   |
| `HDPAY_MONGODB_DB`               | `hdpay`                     |                                            |
| `HDPAY_JWT_SECRET`               |                             | API host. Admin sign-in is off while empty |
| `HDPAY_ACCESS_TOKEN_MINUTES`     | `15`                        | How long an admin access token lasts       |
| `HDPAY_REFRESH_TOKEN_DAYS`       | `30`                        | How long an admin stays signed in          |
| `HDPAY_MAX_LOGIN_ATTEMPTS`       | `5`                         | Wrong passwords per email and IP before a pause |
| `HDPAY_DEPOSIT_TTL_MINUTES`      | `30`                        | Default payment window of a new project    |
| `HDPAY_REMAINDER_WINDOW_MINUTES` | `1440`                      | Default time to send the rest of an underpayment |
| `HDPAY_AMOUNT_TOLERANCE_BPS`     | `50`                        | Default accepted underpayment, in basis points |
| `HDPAY_EVENT_RETENTION_DAYS`     | `30`                        | How long projects can catch up on missed events |
| `HDPAY_EXPIRED_DELETE_AFTER_HOURS` | `360` (15 days)           | Hours after an expired, never-paid deposit is deleted; `0` keeps them |
| `HDPAY_POLL_INTERVAL_SECONDS`    | `5`                         | Watcher loop interval                      |
| `HDPAY_PORT`                     | `8090`                      | API port                                   |
| `HDPAY_RELOAD`                   | `false`                     | Development: restart the API when `api/` code changes |
| `HDPAY_CHAIN`                    |                             | The watcher's or sweeper's chain, instead of `--chain` (for their images) |
| `HDPAY_FORWARDED_ALLOW_IPS`      | `127.0.0.1`                 | Proxies trusted to pass on the caller's IP (`X-Forwarded-For`), comma separated |
| `HDPAY_CORS_ORIGINS`             | the admin dev server        | JSON list of browser origins allowed to call the API |
| `HDPAY_CHAINS_FILE`              | `chains.yaml`               | Chains and tokens (see above)              |
| `HDPAY_RPC_URL_<KEY>`            | the file's `rpc_url`        | e.g. `HDPAY_RPC_URL_BSC`, `HDPAY_RPC_URL_TRON_NILE` |
| `HDPAY_MASTER_XPRV`              |                             | **Sweepers only**, in `envs/sweeper.env`: the root key |

## Running

```bash
python -m api                          # http://localhost:8090, docs at /docs
python -m watcher --chain tron         # one per chain any project accepts
python -m watcher --chain bsc
python -m sweeper --chain tron         # on the host that holds HDPAY_MASTER_XPRV
python -m sweeper --chain bsc
```

Ports are chosen to run next to other local projects: API `8090`, web (the admin) `4500`, and the example shop's
API `8400` and web `4400`.

## Docker

Each part has its own Dockerfile:

| Dockerfile | Image | Runs |
|---|---|---|
| `Dockerfile`, target `api` | `ghcr.io/meherett/hdpay-api` | the api |
| `Dockerfile`, target `watcher` | `ghcr.io/meherett/hdpay-watcher` | a watcher, for the chain in `HDPAY_CHAIN` (or `--chain`) |
| `Dockerfile`, target `sweeper` | `ghcr.io/meherett/hdpay-sweeper` | a sweeper, for the chain in `HDPAY_CHAIN` (or `--chain`), with the master key |
| `web/Dockerfile` | `ghcr.io/meherett/hdpay-web` | the web app (the admin), served by nginx, which passes `/api/` on to the api |
| `example/api/Dockerfile` | `ghcr.io/meherett/hdpay-example-api` | the example shop's API; its SQLite database is in the `/data` volume |
| `example/web/Dockerfile` | `ghcr.io/meherett/hdpay-example-web` | the example checkout, served by nginx, which passes `/api/` on to the shop's API |

`vm/docker-compose.yaml` (the server's files live in `vm/`, as in qnode) starts the api, the web app
(http://localhost:4500), one watcher and one sweeper per chain in `chains.yaml`, and optionally MongoDB and the
example shop. Run `docker compose` from `vm/`, or from the root with `-f vm/docker-compose.yaml`. The web apps reach their API through their own `/api/`, so no CORS is needed.

The images are published, private, on GitHub's registry. After `docker login ghcr.io` (a classic token with
`read:packages` to pull, `write:packages` to push), a server pulls them instead of building:

```bash
cd vm
docker compose --profile example pull && docker compose --profile example up -d   # a server
docker compose --profile example build && docker compose --profile example push   # publish new ones
```

```bash
cp envs/common.env.example envs/common.env     # HDPAY_MONGODB_URI (Atlas for now), HDPAY_JWT_SECRET, ...
python -m api.keys master --save              # envs/sweeper.env: the sweepers get it, the api never does
cp chains.example.yaml chains.yaml   # mounted read-only; edits apply without a rebuild
cd vm
docker compose up -d --build
docker compose ps
docker compose logs -f watcher-tron-nile
```

`python -m api.keys` and `python -m api.manage` are command-line tools, not services. Run them in a
throwaway container:

```bash
cd vm
docker compose run --rm api python -m api.manage create-admin --email you@example.com --name "Your Name"
docker compose run --rm sweeper-tron-nile python -m api.keys project <number>    # needs the master key
```

To run MongoDB in Docker instead of Atlas, set `HDPAY_MONGODB_URI=mongodb://mongo:27017` in `envs/common.env` and
start with `docker compose --profile mongo up -d`; its data lives in the `mongo-data` volume.

### On the VM: hdpay.systems

nginx runs in Docker too (the `nginx` service, profile `nginx`): it takes 80 and 443 and sends each domain
to a service by name, as `vm/nginx/sites-available/hdpay-systems.conf` says. The other ports listen on
`127.0.0.1` only, so everything goes through nginx (and HTTPS). Cloudflare sits in front (SSL mode Full
(strict)), with one certificate for `hdpay.systems` and `*.hdpay.systems`.

| Address | Service |
|---|---|
| `hdpay.systems` | web |
| `hdpay.systems/api/...` and `api.hdpay.systems` | api (the `/api` prefix is dropped) |
| `example.hdpay.systems` | example-web |
| `example.hdpay.systems/api/...` | example-api |

```bash
git clone https://github.com/meherett/hdpay.git && cd hdpay
# copy from your PC (never committed): envs/common.env, envs/sweeper.env (chmod 600), envs/example.env, chains.yaml
cp vm/.env.example vm/.env       # COMPOSE_PROFILES=example,nginx and EXAMPLE_WEB_URL=https://example.hdpay.systems
# the certificate and key (never committed), with these names:
mkdir -p vm/ssl/certs/hdpay-systems vm/ssl/private/hdpay-systems
#   vm/ssl/certs/hdpay-systems/origin.crt     (fullchain.pem from certbot, or a Cloudflare origin certificate)
#   vm/ssl/private/hdpay-systems/private.key  (privkey.pem)
chmod 600 vm/ssl/private/hdpay-systems/private.key
cd vm
docker login ghcr.io -u meherett                         # a token with read:packages
docker compose pull
docker compose up -d --no-build --remove-orphans          # COMPOSE_PROFILES in vm/.env adds the shop and nginx
docker compose ps
```

After renewing the certificate, replace the two files and run `docker compose restart nginx`.

nginx trusts `CF-Connecting-IP` from Cloudflare's addresses only and passes the visitor's IP on; the api
believes it from nginx and the web app's nginx only (their fixed addresses on the compose network), so
API-key allowed IPs and the sign-in limit see the real visitor.

The example shop (its API on 8400, its checkout on http://localhost:4400):

```bash
cp envs/example.env.example envs/example.env   # HDPAY_SECRET_KEY of the project it sells for, SMTP_...
cd vm
docker compose --profile example up -d --build
docker compose run --rm example-api python -m source.users seed
```

## Gateway API (for projects)

Every `/v1` route needs the project's API key: `Authorization: Bearer hdp_sk_...`. Over HTTPS only in
production. If the project lists allowed IPs, keys work only from those.

| Method | Path                     | Key                    | Description                                         |
|--------|--------------------------|------------------------|-----------------------------------------------------|
| GET    | `/v1/chains`             | secret or publishable  | The networks and assets the project accepts         |
| GET    | `/v1/products`           | secret or publishable  | The project's products                              |
| POST   | `/v1/deposits`           | secret or publishable  | Open a deposit, or get the account's open one       |
| GET    | `/v1/deposits/{id}`      | secret or publishable  | One of the project's deposits                       |
| GET    | `/v1/deposits`           | secret                 | The project's deposits (`account_id`, `status`, paging) |
| GET    | `/v1/events?after=<seq>` | secret                 | Events after a sequence; by default after the last acknowledged |
| POST   | `/v1/events/ack`         | secret                 | `{"seq": n}`: everything up to n is handled         |
| WS     | `/v1/events/stream`      | secret                 | Events as they happen (below)                       |

```json
POST /v1/deposits
{
  "account_id": "42",
  "product": "100k",
  "chain": "tron",
  "asset": "USDT",
  "expected_amount": 25000000
}
```

- `product` is a `value` from the project's products (required when it has any, absent otherwise); a
  different product gets its own address.
- A product with a `price` (USD, set in the admin) is paid in a USD stablecoin, USDT or USDC, one to
  one: the deposit's amount follows the price, less the deposit's discount if given (see Discounts), so leave
  `expected_amount` out. Any other asset is
  refused. The example shop's web shows priced products as cards and opens the address as soon as the
  account, network and coin are chosen.
- `expected_amount` is in the asset's smallest unit (25 USDT on Tron = `25000000`); omit it to accept
  any amount. Amounts in responses are strings: 18-decimal amounts do not fit in a JavaScript number.
- The reply is the deposit as the project sees it: address, explorer link, status, amounts, product name.
  Send the customer to a checkout page with the address, or to the example shop's `/pay/<deposit id>`.

### Events over WebSocket

The project's backend keeps one connection open, `wss://<hdpay>/v1/events/stream` with its secret key in
the `Authorization` header. HDPay sends `{"type": "ready", "after": n}`, then every event after the last
one acknowledged (or after `?after=<seq>`), then each new one as it happens:

```json
{
  "id": "evt_9f3a…_1042",
  "seq": 1042,
  "type": "deposit.partial",
  "created_at": "2026-10-21T09:30:00Z",
  "deposit": { "id": "…", "account_id": "42", "product": "100k", "product_name": "$100,000",
               "chain": "tron", "asset": "USDT", "address": "T…", "status": "partial",
               "internal_status": "partial", "expected_amount": "25000000", "received_amount": "10000000",
               "difference": "under", "expires_at": "…", "tx_hash": "…", "tx_url": "…" }
}
```

Reply `{"ack": 1042}` once handled. After a disconnect, reconnect: HDPay resumes after the last ack, so
nothing is missed; an event can arrive twice, so ignore ids already handled. Events are kept for
`HDPAY_EVENT_RETENTION_DAYS`, and `GET /v1/events` reads the same history over plain HTTP. Every event
carries the whole deposit, so the project can email its customer from it. A minimal client:

```python
import asyncio, json, websockets

async def follow(key: str):
    async for ws in websockets.connect("wss://hdpay.example/v1/events/stream",
                                       additional_headers={"Authorization": f"Bearer {key}"}):
        try:
            async for message in ws:
                event = json.loads(message)
                if event.get("type", "").startswith("deposit."):
                    handle(event)                       # idempotent: skip event["id"] if already seen
                    await ws.send(json.dumps({"ack": event["seq"]}))
        except websockets.ConnectionClosed:
            continue                                    # reconnects, and resumes after the last ack
```

The connection is closed with code `4401` for a missing or unknown key and `4403` for a publishable key,
a disabled project or a disallowed IP.

### Public

| Method | Path               | Description                                                              |
|--------|--------------------|--------------------------------------------------------------------------|
| GET    | `/deposits/{id}`   | A deposit's public view for checkout pages; its random id is the permission |
| GET    | `/health`          | `{"status": "ok"}`                                                       |

## Admin

Admins sign in with an email and a password (`POST /auth/login`): a 15-minute access token and a 30-day
refresh token that is replaced at every refresh. There is no email: an owner sets other users' passwords,
and `python -m api.manage set-password --email …` does it from the server. Roles:

- **owner:** everything an admin does, and: users (`/admin/users`), the sweeper page (`/admin/sweeper`),
  deleting a deposit address (`DELETE /admin/deposits/{id}`) or a whole project with its API keys,
  addresses and events (`DELETE /admin/projects/{id}?confirm=<name>`). Deleted addresses are no longer
  watched or swept; coins on them stay on chain, reachable with the master key. The last owner cannot be
  demoted, disabled or deleted.
- **admin:** projects, API keys and support actions on deposits (credit, reopen, release).
- **viewer:** reads everything else, changes nothing.

Each admin and viewer sees only the projects an owner gives them on the Users page: **all projects**
(the default, including new ones) or **specific projects**. The project list still shows them every
project, but one that is not theirs is marked **Locked** with only its name and number; everything
else about it (its page, keys and events, its deposits, and its share of the overview) answers 404.
`POST /admin/users` and `PATCH /admin/users/{id}` take `projects`: `null` / `"all"`, or a list of
project ids. Owners always see every project.

The admin app does not open deposits: projects do, through the gateway (`POST /v1/deposits`).

| Method | Path                                    | Description                                                |
|--------|-----------------------------------------|------------------------------------------------------------|
| POST   | `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/logout-all` | Sessions                  |
| GET    | `/auth/me`; POST `/auth/change-password` | The signed-in user                                        |
| GET/POST/PATCH/DELETE | `/admin/users…`          | Users; `POST /admin/users/{id}/password` sets a password   |
| GET/POST/PATCH | `/admin/projects…`              | Projects; `POST …/keys` adds xpubs; `POST …/status?status=` enables or disables |
| GET/POST/DELETE | `/admin/projects/{id}/api-keys…` | API keys; the key is in the create reply only            |
| GET    | `/admin/projects/{id}/treasury`, `…/funders`, `…/events` | Live balances, recent events          |
| GET    | `/admin/stats?project_id=`               | Counts by status, credited totals, watcher progress       |
| GET    | `/admin/deposits`                       | List (`search`, `project_id`, `chain`, `type`, `asset`, `status`, `difference`, paging) |
| POST   | `/admin/deposits`                       | Open a deposit by hand for a project                       |
| GET    | `/admin/deposits/{id}`                  | Everything about one deposit                               |
| POST   | `/admin/deposits/{id}/credit`, `…/reopen`, `…/release` | Support actions                         |
| GET    | `/admin/sweeper`, `/admin/chains`, `/admin/defaults` | Sweeper state; every chain; project defaults |

## Admin app

Angular 22 with ng-zorro-antd, on port `4500`: support and operations.

```bash
cd web && npm ci && npm run web
```

The admin shows: the overview by status and project; projects with their keys, API keys, settings, live
treasury and fee funder balances, and recent events; deposits with filters, search, reopen and credit;
the watchers and sweepers of every chain; and users.

## Example shop

`example/` is a project integrated the way a real one would be: the browser talks only to the shop's
backend, and only the backend talks to HDPay, with the project's secret key.

```
example/web (4400)  ->  example/api (8400)  ->  HDPay /v1 (8090)
                                ^
                                '-- WS /v1/events/stream: emails the customer
```

| Part           | Does |
|----------------|------|
| `example/api/` | keeps the shop's users (account ID, name, email, password); sells the project's products that have a price; places orders (`POST /orders` with a user, product, network, coin and optionally the customer's discount opens the deposit at HDPay, at the product's price less that discount), shows an order's payment, renews an expired one; follows HDPay's events and emails the user: the address, payment seen, part paid, paid, needs a look, expired |
| `example/web/` | the checkout in three steps: the user; the product (cards with prices), an optional discount for the customer (percent and end, shown live on the cards) and how to pay (a card per coin and network); then review and get the payment address. `/pay/:id` counts down, follows the payment to paid and asks for the rest of an underpayment |

```bash
cd example/api
pip install -r requirements.txt
cp .env.example .env          # HDPAY_SECRET_KEY: admin > Projects > the project > API keys (secret)
python -m source.users seed   # sample users 1001-1005, with their passwords (shown once)
python -m source.users add you@example.com --name "Your Name"   # one more user, next account ID
python -m source              # http://localhost:8400; python -m pytest for its tests

cd example/web && npm ci && npm run example    # http://localhost:4400
```

Without `SMTP_HOST` the emails are written to the API's log instead of sent. The shop keeps its users,
their sessions, its orders (which email each deposit belongs to) and the events it handled in SQLite
(`example.db`). A user's account ID is the one HDPay derives their addresses from, so it never changes.
`python -m source.users list` lists the users; `python -m source.users password <email>` sets a new password.

## Project layout

```
web/                support and operations: the admin (Angular)
api/                the API, and the code the watchers and sweepers share
  main.py           the FastAPI app: routers, the public deposit view; python -m api runs it
  gateway.py        /v1 routes for projects, and the event WebSocket
  admin.py          /admin routes for the admin app
  auth.py           admin users, sign-in, sessions and roles
  apikeys.py        project API keys
  projects.py       projects: products, chains, treasury, settings
  events.py         project events, with a sequence per project
  discounts.py      a deposit's discount: the discounted price, kept or lapsed at its end
  deposits.py       opening deposits and recording their events
  db.py             MongoDB: connection, indexes, counters, the deposit record
  schemas.py        request and response models
  chains.py         loads and checks chains.yaml; followed.py reloads it on change
  balances.py       live balances from each chain's RPC; tron.py is the Tron client
  keys.py           m/{project}'/{coin}'/… derivation, the master key, fee funders; python -m api.keys
  manage.py         python -m api.manage: first admin, passwords
  config.py         settings from HDPAY_* environment variables
sweeper/            python -m sweeper --chain <key>: chain-independent loop, EVM and Tron sweepers
watcher/            python -m watcher --chain <key>: chain-independent state machine, EVM and Tron scanners
example/            a sample shop on HDPay
  api/              its backend (FastAPI): holds the secret key, opens deposits, emails customers
  web/              its checkout (Angular), which talks only to example/api
tests/              pytest suite (in-memory MongoDB, stub chains, no network)
vm/                 the server: docker-compose.yaml, which runs the published images
```

Amounts are stored as strings (they overflow 64-bit integers). Timestamps are naive UTC.

To add a chain family, implement `watcher.base.Scanner` and `sweeper.base.Sweeper` and register them in
`make_scanner` / `make_sweeper`.

## Tests

```bash
pip install pytest httpx mongomock
python -m pytest                                          # in-memory MongoDB, a few seconds
HDPAY_TEST_MONGODB_URI=mongodb://… python -m pytest       # a real MongoDB, throwaway databases
```

## Status

Verified:

- Derivation from each project's xpubs matches derivation from the mnemonic for both families, and
  sweep and funder keys from the master key match the addresses (`tests/test_keys.py`)
- Gateway, events and the WebSocket stream, admin sign-in and roles, projects and API keys, the
  watcher state machine and the sweeper loop (`tests/`), in memory and against MongoDB Atlas
- EVM and Tron scanners read-only against live blocks, plus the sweepers' balance and fee estimates

Not yet done: a real sweep. Signing and broadcasting need testnet funds.

## Roadmap

1. End-to-end on testnets: Nile (TRX, USDT) and Sepolia (ETH, USDC) — pay, confirm, top up, sweep.
2. Integrate the first project, the one that accepts transactions manually today.
3. Tron energy delegation from a staked treasury instead of burning TRX, once volume justifies it.
