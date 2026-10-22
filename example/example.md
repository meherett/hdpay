# HDPay API, by example

How a shop takes crypto payments through HDPay: every request and reply of the gateway API, every event
it sends back, and what the example shop in this folder does with each one, down to the emails its
customers get.

- [The pieces](#the-pieces)
- [Keys and requests](#keys-and-requests)
- [Amounts, times and statuses](#amounts-times-and-statuses)
- [Gateway routes](#gateway-routes)
  - [GET /v1/chains](#get-v1chains)
  - [GET /v1/products](#get-v1products)
  - [POST /v1/deposits: a payment address](#post-v1deposits-a-payment-address)
  - [GET /v1/deposits/{id}](#get-v1depositsid)
  - [GET /v1/deposits](#get-v1deposits)
  - [GET /v1/events and POST /v1/events/ack](#get-v1events-and-post-v1eventsack)
  - [WS /v1/events/stream](#ws-v1eventsstream)
  - [GET /deposits/{id}: the public view](#get-depositsid-the-public-view)
- [Events](#events)
- [A payment, start to finish](#a-payment-start-to-finish)
- [Underpaid, review and reopen](#underpaid-review-and-reopen)
- [Discounts](#discounts)
- [The example shop's own API](#the-example-shops-own-api)
- [Errors](#errors)

---

## The pieces

```
 customer's browser ──> example/web (4400) ──> example/api (8400) ──> HDPay API (8090)
                                                     ^                     │
                                                     └──── events (WS) ────┘
                                                     │
                                                     └──> email to the customer
```

- **HDPay API** (`api/`, port 8090): issues a fresh address per payment, watches the chains, and tells the
  project what happened as **events**.
- **The project's backend**, here **example/api** (port 8400): the only thing that holds the project's
  **secret key**. It opens deposits for its users, follows the event stream, credits orders and emails
  customers.
- **The checkout page**, here **example/web** (port 4400): talks to the shop's backend, never to HDPay with
  a secret key.

Everything below uses `https://hdpay.example.com` for HDPay. Locally it is `http://localhost:8090`.

---

## Keys and requests

Every gateway request carries the project's API key, created on the project's **API keys** tab in the
admin (by an owner). A key is shown once.

```http
Authorization: Bearer hdp_sk_4f1c0b...
```

| Key | Prefix | Can |
|---|---|---|
| Secret | `hdp_sk_` | everything: chains, products, deposits, the deposit list and the events. **Server side only.** |
| Publishable | `hdp_pk_` | chains, products, open a deposit and read one. Safe in a browser checkout. |

- A project that lists **allowed IPs** accepts its keys only from those addresses.
- A key stops working at once when it is revoked, or while the project is disabled.
- Bodies are JSON: `Content-Type: application/json`.

---

## Amounts, times and statuses

**Amounts are strings in the asset's smallest unit**, because 18-decimal amounts do not fit in a
JavaScript number. Divide by `10^decimals` to show them:

| Asset | decimals | `"100000000"` is |
|---|---|---|
| USDT on Tron | 6 | 100 USDT |
| USDC on Ethereum, Sepolia | 6 | 100 USDC |
| USDT on BNB Smart Chain | 18 | `"100000000000000000000"` is 100 USDT |
| ETH, BNB | 18 | 1 ETH is `"1000000000000000000"` |
| TRX | 6 | 1 TRX is `"1000000"` |

**Times** are UTC, ISO 8601, ending in `Z`: `"2026-10-22T14:30:00Z"`.

**Statuses.** A deposit carries two:

| `internal_status` (backend) | `status` (customer) | Meaning |
|---|---|---|
| `pending` | `pending` | Address issued; inside its payment window. |
| `expired` | `expired` | The window ended with nothing received. **Still watched**: a late payment is still counted (`late: true`). |
| `detected` | `confirming` | A payment arrived and is waiting for confirmations. |
| `partial` | `partial` | Confirmed, but short. The rest can still come to the same address until `expires_at`. |
| `confirmed` | `paid` | Paid in full (within the project's tolerance). **Credit the order now.** |
| `swept` | `paid` | The funds reached the project's treasury. |
| `review` | `contact_support` | The rest never came. The address is closed; support decides. |

**Tolerance.** A payment counts as complete at `expected − expected × tolerance_bps ÷ 10000`. With the
default 50 bps, a 100 USDT deposit is paid from 99.50 USDT up.

---

## Gateway routes

| Method | Path | Key | What |
|---|---|---|---|
| GET | `/v1/chains` | secret or publishable | networks and assets the project accepts |
| GET | `/v1/products` | secret or publishable | the project's products |
| POST | `/v1/deposits` | secret or publishable | a payment address (or the account's open one) |
| GET | `/v1/deposits/{id}` | secret or publishable | one of the project's deposits |
| GET | `/v1/deposits` | secret | the project's deposits, newest first |
| GET | `/v1/events` | secret | events after a sequence number |
| POST | `/v1/events/ack` | secret | mark events handled |
| WS | `/v1/events/stream` | secret | events as they happen |
| GET | `/deposits/{id}` | none | the public view of a deposit, for a payment page |

### GET /v1/chains

The networks and assets the project accepts, in the order set on its Settings tab.

```bash
curl https://hdpay.example.com/v1/chains -H "Authorization: Bearer hdp_sk_..."
```

```json
[
  {
    "key": "tron-nile",
    "name": "Tron Nile Testnet",
    "family": "tron",
    "confirmations": 20,
    "assets": [
      { "symbol": "TRX",  "decimals": 6, "contract": null, "standard": null, "usd": false },
      { "symbol": "USDT", "decimals": 6, "contract": "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf", "standard": "TRC20", "usd": true }
    ]
  },
  {
    "key": "sepolia",
    "name": "Sepolia",
    "family": "evm",
    "confirmations": 6,
    "assets": [
      { "symbol": "ETH",  "decimals": 18, "contract": null, "standard": null, "usd": false },
      { "symbol": "USDC", "decimals": 6,  "contract": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238", "standard": "ERC20", "usd": true }
    ]
  }
]
```

- `contract` and `standard` are `null` for the chain's own coin.
- `standard` is ERC20 on Ethereum, BEP-20 on BNB Smart Chain, TRC20 on Tron.
- `usd: true` marks a USD stablecoin (USDT, USDC): the only assets a **priced** product can be paid in.

Chain keys: `ethereum`, `sepolia`, `bsc`, `bsc-testnet`, `tron`, `tron-nile`.

### GET /v1/products

```json
[
  { "value": "10k",  "name": "$10,000",  "subname": null,             "price": "10" },
  { "value": "100k", "name": "$100,000", "subname": "(Most popular)", "price": "100" },
  { "value": "custom", "name": "Custom top-up", "subname": null,      "price": null }
]
```

- `value` is what a deposit stores and what you send as `product`.
- `price` is in **USD**, paid in USDT or USDC, and sets the deposit's amount. `null` means the amount is up
  to you (`expected_amount`), or any amount.

### POST /v1/deposits: a payment address

Opens a deposit for one of your accounts: a **fresh address** that belongs to this payment only.

**Request**

| Field | Type | Required | Notes |
|---|---|---|---|
| `account_id` | string of digits | yes | Your user's numeric id, `0` to `2147483647`. `"042"` and `"42"` are the same account. |
| `chain` | string | yes | A chain key from `/v1/chains`. |
| `asset` | string | yes | A symbol the project accepts on that chain. |
| `product` | string | if the project has products | A `value` from `/v1/products`. Leave it out when the project has none. |
| `expected_amount` | integer | no | Smallest unit. **Leave it out for a priced product**: the price sets it. Leave it out for "any amount". |
| `discount_percent` | integer 1–99 | no | A discount for this deposit only, on a priced product. Needs `discount_ends_at`. |
| `discount_ends_at` | ISO time | no | When the discount ends; must be in the future. |

```bash
curl -X POST https://hdpay.example.com/v1/deposits \
  -H "Authorization: Bearer hdp_sk_..." \
  -H "Content-Type: application/json" \
  -d '{"account_id": "1006", "chain": "tron-nile", "asset": "USDT", "product": "100k"}'
```

**Reply** `200`

```json
{
  "id": "6ad93594-e803-4326-9bc5-f7a0c2e1b3d4",
  "account_id": "1006",
  "product": "100k",
  "product_name": "$100,000",
  "chain": "tron-nile",
  "chain_name": "Tron Nile Testnet",
  "asset": "USDT",
  "standard": "TRC20",
  "decimals": 6,
  "address": "TCoMSPhQ6ftCtBLaMaf9M3sCtrg4LH2zyM",
  "address_url": "https://nile.tronscan.org/#/address/TCoMSPhQ6ftCtBLaMaf9M3sCtrg4LH2zyM",
  "status": "pending",
  "expected_amount": "100000000",
  "received_amount": "0",
  "tx_hash": null,
  "tx_url": null,
  "confirmations": 20,
  "created_at": "2026-10-22T14:00:00Z",
  "expires_at": "2026-10-22T14:30:00Z",
  "expires_in_seconds": 1800,
  "discount": null,
  "internal_status": "pending",
  "difference": null,
  "late": false,
  "confirmed_at": null,
  "swept_at": null,
  "sweep_tx_hash": null
}
```

What to do with it:

- Show the customer `address`, the amount (`expected_amount` ÷ 10^`decimals`, here **100 USDT**), the
  network (`chain_name`, `standard`) and the deadline (`expires_at`).
- Keep `id`: it is the payment's reference in every event and reply.

Rules worth knowing:

- **The same account, chain, asset, product and discount get the same open address back** while it is
  `pending`, instead of a new one on every click. Once it expired, the next call issues a new address.
- **A priced product sets the amount.** Send `expected_amount` for one and it must match the price, or the
  request is refused. So a checkout page holding a publishable key cannot change what is charged.
- The payment window is the project's **Payment window** (30 minutes by default).

**With a discount**: 20% off until the end of the month.

```json
{
  "account_id": "1006", "chain": "tron-nile", "asset": "USDT", "product": "100k",
  "discount_percent": 20, "discount_ends_at": "2026-10-31T18:00:00Z"
}
```

The reply expects the discounted price and keeps the discount with the deposit:

```json
{
  "expected_amount": "80000000",
  "discount": {
    "percent": 20,
    "ends_at": "2026-10-31T18:00:00Z",
    "full_amount": "100000000",
    "amount": "80000000",
    "status": "active"
  }
}
```

See [Discounts](#discounts) for what happens when it ends.

### GET /v1/deposits/{id}

One of the project's deposits, in the same shape as the reply above. A deposit of another project is `404`.

```bash
curl https://hdpay.example.com/v1/deposits/6ad93594-e803-4326-9bc5-f7a0c2e1b3d4 -H "Authorization: Bearer hdp_sk_..."
```

### GET /v1/deposits

The project's deposits, newest first. Secret key only.

| Query | Default | |
|---|---|---|
| `account_id` | | only this account's |
| `status` | | an `internal_status`, e.g. `confirmed` |
| `skip` | 0 | |
| `limit` | 20 | at most 100 |

```bash
curl "https://hdpay.example.com/v1/deposits?account_id=1006&status=confirmed&limit=10" -H "Authorization: Bearer hdp_sk_..."
```

Replies with a list of deposits.

### GET /v1/events and POST /v1/events/ack

Everything that happens to the project's deposits is an **event** with a per-project sequence number
`seq`. Polling is the simple way to read them; the [stream](#ws-v1eventsstream) is the fast one.

```bash
curl "https://hdpay.example.com/v1/events?after=41&limit=100" -H "Authorization: Bearer hdp_sk_..."
```

- `after`: the last `seq` you handled. Left out, it is the last one you **acknowledged**.
- `limit`: 1 to 500, default 100.

```json
{
  "events": [
    {
      "id": "evt_6ad93594e803326319bc5f7a_42",
      "seq": 42,
      "type": "deposit.confirmed",
      "created_at": "2026-10-22T14:12:09Z",
      "deposit": { "id": "6ad93594-...", "status": "paid", "internal_status": "confirmed", "...": "..." }
    }
  ],
  "last_seq": 42
}
```

Once handled, acknowledge, so the next call and a reconnecting stream start after it:

```bash
curl -X POST https://hdpay.example.com/v1/events/ack \
  -H "Authorization: Bearer hdp_sk_..." -H "Content-Type: application/json" \
  -d '{"seq": 42}'
```

`204`. The acknowledged sequence never moves back.

### WS /v1/events/stream

Events as they happen, over a WebSocket. Send the secret key in the `Authorization` header when connecting.

```
wss://hdpay.example.com/v1/events/stream            resume after the last acknowledged event
wss://hdpay.example.com/v1/events/stream?after=41   or after a sequence of your choice
```

1. HDPay sends `{"type": "ready", "after": 41}`.
2. Then every event after that, oldest first, then each new one within about a second.
3. After handling one, send `{"ack": 42}`.

```
<- {"type": "ready", "after": 41}
<- {"id": "evt_..._42", "seq": 42, "type": "deposit.detected", "created_at": "...", "deposit": {...}}
-> {"ack": 42}
<- {"id": "evt_..._43", "seq": 43, "type": "deposit.confirmed", "created_at": "...", "deposit": {...}}
-> {"ack": 43}
```

- **Delivery is at least once.** After a reconnect an event can come again: remember the `id`s you handled
  and skip repeats.
- A malformed message gets `{"type": "error", "message": "send {\"ack\": <seq>}"}`.
- Close codes: `4401` bad or revoked key, `4403` secret key needed, IP not allowed, or the project is
  not active (also sent when it is disabled while connected).
- Reconnect with a growing pause (1 s, 2 s, 4 s … up to a minute), as `example/api/source/events.py` does.

### GET /deposits/{id}: the public view

No key. For a payment page that only knows the deposit's id, which is a random UUID and so works as a
private link. It returns the customer's fields only: everything in the deposit reply **up to `discount`**,
without `internal_status`, `difference`, `late`, `confirmed_at`, `swept_at` or `sweep_tx_hash`.

---

## Events

Every event carries the deposit **as it is right after the change**, in the same shape as
`GET /v1/deposits/{id}`.

| Type | When | Deposit `status` / `internal_status` | What the shop does |
|---|---|---|---|
| `deposit.created` | An address was issued. | `pending` / `pending` | Nothing more: it already has the reply. |
| `deposit.detected` | A payment arrived; waiting for confirmations. | `confirming` / `detected` | Tell the customer it is on its way. |
| `deposit.partial` | Confirmed, but short. The rest can come to the same address until `expires_at`. | `partial` / `partial` | Ask for the rest. |
| `deposit.confirmed` | Paid in full, or credited by support. | `paid` / `confirmed` | **Credit the order.** Thank the customer. |
| `deposit.expired` | The payment window ended with nothing received. | `expired` / `expired` | Tell the customer; they can start again. |
| `deposit.review` | The rest of a short payment never came. | `contact_support` / `review` | Ask the customer to contact support. |
| `deposit.reopened` | Support gave a deposit in review more time for the rest. | `partial` / `partial` | Ask for the rest, by the new deadline. |
| `deposit.discount_expired` | A deposit's discount ended before it was paid in full: the full price is due. | usually `partial` / `partial` | Ask for the full price less what came. |
| `deposit.swept` | The funds reached the project's treasury. | `paid` / `swept` | Bookkeeping only. |

Fields that matter in events:

- `received_amount`: the total that arrived so far.
- `expected_amount`: what is due now. It changes on `deposit.reopened` and `deposit.discount_expired`.
- `expires_at`: the deadline for the rest on `deposit.partial` and `deposit.reopened` (and on
  `deposit.discount_expired`, which keeps the deadline the deposit already had).
- `difference`: `over`, `under` or `exact` against the expected amount.
- `late: true`: paid after the payment window. It still counts.

---

## A payment, start to finish

A customer buys **$100,000** (price 100 USD) with USDT on Tron Nile.

**1. The shop opens the deposit**

```http
POST /v1/deposits
{"account_id": "1006", "chain": "tron-nile", "asset": "USDT", "product": "100k"}
```

→ address `TCoMSPhQ...`, `expected_amount: "100000000"`, `expires_at: 14:30`.
The example shop emails the address straight away:

```
Subject: Your payment address for $100,000

Send exactly 100 USDT (TRC20) on Tron Nile Testnet to:

    TCoMSPhQ6ftCtBLaMaf9M3sCtrg4LH2zyM

The address is reserved for you until Oct 22, 2026 14:30 UTC.
Follow your payment here: http://localhost:4400/pay/6ad93594-...

Only send USDT on Tron Nile Testnet: anything else sent to this address is lost.

Example Shop
```

**2. The customer sends 100 USDT** → `deposit.detected`

```json
{ "type": "deposit.detected",
  "deposit": { "status": "confirming", "internal_status": "detected",
               "received_amount": "100000000", "tx_hash": "a3f9...", "tx_url": "https://nile.tronscan.org/#/transaction/a3f9..." } }
```

```
Subject: We see your payment for $100,000

100 USDT arrived on Tron Nile Testnet. We are waiting for the network to confirm it;
you will get another email once it is done.
```

**3. 20 confirmations later** → `deposit.confirmed`

```json
{ "type": "deposit.confirmed",
  "deposit": { "status": "paid", "internal_status": "confirmed", "difference": "exact",
               "confirmed_at": "2026-10-22T14:12:09Z" } }
```

The shop credits the order (the example sets `paid_at`), then:

```
Subject: Payment received for $100,000

Thank you! We received 100 USDT on Tron Nile Testnet and your order is paid.
Transaction: https://nile.tronscan.org/#/transaction/a3f9...
```

**4. The sweeper moves it to the treasury** → `deposit.swept` (`internal_status: "swept"`,
`sweep_tx_hash`, `swept_at`). No email.

**If nothing is sent in time** → `deposit.expired`:

```
Subject: Your payment address for $100,000 expired

Nothing arrived at TCoMSPhQ... in time, so the address is no longer reserved for you.
Start again any time: http://localhost:4400

If you did send a payment, reply with the reference 6ad93594-...: late payments are still counted.
```

A payment that arrives after that is still detected and confirmed, with `late: true`.

---

## Underpaid, review and reopen

The customer owes 100 USDT and sends 60.

**1. Short payment** → `deposit.partial`. The **remainder window** starts (the project's setting, e.g. 9
minutes or a day) and `expires_at` becomes its end.

```json
{ "type": "deposit.partial",
  "deposit": { "status": "partial", "internal_status": "partial", "difference": "under",
               "expected_amount": "100000000", "received_amount": "60000000",
               "expires_at": "2026-10-22T15:00:00Z" } }
```

```
Subject: Part of your payment for $100,000 arrived

We received 60 of 100 USDT. Send the remaining 40 USDT (TRC20) to the same address:

    TCoMSPhQ6ftCtBLaMaf9M3sCtrg4LH2zyM

Before Oct 22, 2026 15:00 UTC. Follow it here: http://localhost:4400/pay/6ad93594-...
```

If the 40 arrives in time → `deposit.detected`, then `deposit.confirmed`, as above.

**2. The rest never comes** → `deposit.review`. The address is no longer watched.

```json
{ "type": "deposit.review",
  "deposit": { "status": "contact_support", "internal_status": "review",
               "expected_amount": "100000000", "received_amount": "60000000" } }
```

```
Subject: Your payment for $100,000 needs a look

We received 60 USDT, less than the 100 expected, and the rest did not arrive in time.
Reply to this email with your payment reference 6ad93594-... and we will sort it out.
```

**3. The customer writes in; support decides in the admin** (Deposits → the deposit):

**Credit**: accept what came as paid. HDPay sends `deposit.confirmed` (with `received_amount: "60000000"`,
`difference: "under"`). The shop credits the order and sends its "Payment received" email.

**Reopen**: give the customer more time for the rest. In the dialog support can set:

| In the dialog | Default | Effect |
|---|---|---|
| Product | the current one | A different **priced** product brings its price (less the deposit's discount, while that counts). |
| Remaining | the current shortfall | What the customer still has to send. The new `expected_amount` is received + remaining. |
| Keep the discount (goodwill) | ticked and locked while the discount still runs; unticked once it ended | Ended discount: unticked, the rest is the **full price** less what came; ticked, the discounted price. |
| Countdown | the project's remainder window | 30 min, 1 hour, 12 hours, 1 day, 1 week, or custom minutes. |

HDPay puts the deposit back to `partial`, watches its address again, counts anything sent while it was
closed (from the address balance, within about a minute), and sends **`deposit.reopened`**:

```json
{
  "id": "evt_6ad93594e803326319bc5f7a_57",
  "seq": 57,
  "type": "deposit.reopened",
  "created_at": "2026-10-23T09:15:00Z",
  "deposit": {
    "id": "6ad93594-e803-4326-9bc5-f7a0c2e1b3d4",
    "account_id": "1006",
    "product": "100k",
    "product_name": "$100,000",
    "chain": "tron-nile",
    "chain_name": "Tron Nile Testnet",
    "asset": "USDT",
    "standard": "TRC20",
    "decimals": 6,
    "address": "TCoMSPhQ6ftCtBLaMaf9M3sCtrg4LH2zyM",
    "address_url": "https://nile.tronscan.org/#/address/TCoMSPhQ6ftCtBLaMaf9M3sCtrg4LH2zyM",
    "status": "partial",
    "expected_amount": "100000000",
    "received_amount": "60000000",
    "tx_hash": "a3f9...",
    "tx_url": "https://nile.tronscan.org/#/transaction/a3f9...",
    "confirmations": 20,
    "created_at": "2026-10-22T14:00:00Z",
    "expires_at": "2026-10-24T09:15:00Z",
    "expires_in_seconds": 86400,
    "discount": null,
    "internal_status": "partial",
    "difference": "under",
    "late": false,
    "confirmed_at": null,
    "swept_at": null,
    "sweep_tx_hash": null
  }
}
```

The customer's message comes from three fields:

- **still to send** = `expected_amount − received_amount` = 40 USDT
- **where** = `address` (the same one) on `chain_name`
- **by** = `expires_at`

The example shop sends:

```
Subject: You can complete your payment for $100,000

We received 60 of 100 USDT. Send the remaining 40 USDT (TRC20) to the same address:

    TCoMSPhQ6ftCtBLaMaf9M3sCtrg4LH2zyM

Before Oct 24, 2026 09:15 UTC. Follow it here: http://localhost:4400/pay/6ad93594-...

Example Shop
```

A shop that also texts its customers can send the same three facts by SMS or chat:

```
Example Shop: send the remaining 40 USDT (TRC20, Tron Nile) to TCoMSPhQ6ftCtBLaMaf9M3sCtrg4LH2zyM
by Oct 24 09:15 UTC. Ref 6ad93594.
```

From here it is the same as before: the rest arrives → `deposit.confirmed`; it does not → `deposit.review` again.

**Reopen with another product.** The customer would rather take **$50,000** (price 70) than send the
rest. Support picks it in the dialog: `expected_amount` becomes 70 USDT, so 10 USDT is left to send, and
`deposit.reopened` carries the new product and amount. If what already arrived covers the new price,
**Credit** the deposit instead: there is nothing left to wait for.

---

## Discounts

A discount belongs to **one deposit**, given by the project when it opens it (`discount_percent`,
`discount_ends_at`), on a product with a price.

| `discount.status` | Meaning |
|---|---|
| `active` | Running; `expected_amount` is the discounted `amount`. |
| `kept` | Paid in full before it ended: the discount holds. |
| `lapsed` | It ended before the deposit was paid, so the **full price** is due. |
| `honored` | It had ended, and support kept it anyway on reopen (goodwill). |

**Ended before it was paid.** 20% off 100 USDT (80 due); the customer sent 50, then the discount ended.
HDPay sends **`deposit.discount_expired`** with `expected_amount: "100000000"` (the full price),
`received_amount: "50000000"` and `discount.status: "lapsed"`. `expires_at` stays the deadline the
short payment already started:

```
Subject: The discount on $100,000 ended

The 20% discount ended before your payment was complete, so the full price of 100 USDT is due.
You sent 50 USDT, so send 50 USDT (TRC20) to the same address:

    TCoMSPhQ6ftCtBLaMaf9M3sCtrg4LH2zyM

Before Oct 31, 2026 19:00 UTC. Follow it here: http://localhost:4400/pay/6ad93594-...
```

- Payments made **before** `ends_at` count toward the discounted price, even if confirmed after it.
- A deposit that received **nothing** keeps its discounted amount after the end. It lapses only if a
  payment then arrives, since that payment came too late.

---

## The example shop's own API

`example/api` (port 8400) is what `example/web` calls. It holds the secret key, so the browser never does.

| Method | Path | Body / reply |
|---|---|---|
| GET | `/users` | the shop's users, to pick who is buying (a sample: a real shop signs them in) |
| GET | `/products` | HDPay's products **that have a price** |
| GET | `/chains` | HDPay's `/v1/chains` |
| POST | `/orders` | `{account_id, product, chain, asset, discount_percent?, discount_ends_at?}` → the deposit's public fields |
| GET | `/orders/{id}` | the order's deposit, for the payment page (only the shop's own orders) |
| POST | `/orders/{id}/renew` | a fresh address after the old one expired: same account, product, network, asset, and the discount while it still runs |
| POST | `/auth/login` | `{email, password}` → `{token, user}` |
| GET | `/auth/me` | the signed-in user (`Authorization: Bearer <token>`) |
| POST | `/auth/logout` | ends the session |

`POST /orders`:

```json
{ "account_id": "1006", "product": "100k", "chain": "tron-nile", "asset": "USDT",
  "discount_percent": 20, "discount_ends_at": "2026-10-31T18:00:00Z" }
```

It calls `POST /v1/deposits`, stores the order (deposit id, email, account), emails the address when the
deposit is new, and replies with the customer's fields only (as in
[the public view](#get-depositsid-the-public-view)).

**Following events** (`source/events.py`, `Shop.handle`):

1. Connect to `/v1/events/stream` with the secret key.
2. For each event: skip it if its `id` was handled; find the order by `deposit.id`; on
   `deposit.confirmed` mark it paid; send the email for the event type (table below); remember the `id`.
3. Then `{"ack": seq}`. If an email fails, the event is **not** acknowledged and comes again on reconnect.

| Event | Email subject |
|---|---|
| (order placed) | Your payment address for {product} |
| `deposit.detected` | We see your payment for {product} |
| `deposit.partial` | Part of your payment for {product} arrived |
| `deposit.reopened` | You can complete your payment for {product} |
| `deposit.confirmed` | Payment received for {product} |
| `deposit.review` | Your payment for {product} needs a look |
| `deposit.expired` | Your payment address for {product} expired |
| `deposit.discount_expired` | The discount on {product} ended |
| `deposit.created`, `deposit.swept` | none |

Settings (`example/api/.env`): `HDPAY_URL`, `HDPAY_SECRET_KEY`, `WEB_URL`, `SHOP_NAME`, `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`. Without `SMTP_HOST` the emails
are only logged.

---

## Errors

Errors are JSON: `{"detail": "..."}`. Validation errors (`422`) from a bad field list each problem.

| Status | When | `detail` |
|---|---|---|
| 401 | no key, or not `Bearer hdp_...` | `Send the project's API key: Authorization: Bearer hdp_...` |
| 401 | unknown or revoked key | `Unknown or revoked API key` |
| 403 | a publishable key on a secret route | `This needs the project's secret key (hdp_sk_...)` |
| 403 | the project is disabled or waiting for keys | `This project is not active` |
| 403 | the caller's IP is not allowed | `Requests from 203.0.113.9 are not allowed for this project` |
| 404 | an unknown deposit, or another project's | `deposit not found` |
| 422 | a network the project does not accept | `this project does not accept 'solana'; it accepts tron-nile, sepolia` |
| 422 | an asset it does not accept there | `this project accepts TRX, USDT on Tron Nile Testnet, not 'USDC'` |
| 422 | an unknown product | `product must be one of 10k, 100k` |
| 422 | a product on a project without products | `this project has no products, so leave product out` |
| 422 | a priced product paid in a coin | `$100,000 costs $100: pay it in USDT or USDC` |
| 422 | `expected_amount` that differs from the price | `$100,000 costs $100: leave expected_amount out, it follows the price` |
| 422 | a discount without a priced product | `a discount is a percent off a product's price: give a product that has a price` |
| 422 | only one of the discount fields | `a discount needs both discount_percent and discount_ends_at` |
| 422 | a discount that already ended | `discount_ends_at must be in the future` |
| 422 | a bad `account_id` | `account_id must be a whole number from 0 to 2147483647` |

The example shop passes HDPay's `detail` on to its page, except for key problems, which are the shop's
fault and not the customer's: `502 The payment service did not accept the shop's key.`
