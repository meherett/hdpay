# Connecting your project to HDPay

HDPay gives every payment its own crypto address, watches the chain, and tells your backend what happened.
Your backend opens a payment for a customer, shows the address, and credits the order when HDPay says it is
paid. The funds are swept to your project's treasury automatically.

- [Addresses](#addresses)
- [Getting access](#getting-access)
- [Authentication](#authentication)
- [Amounts and statuses](#amounts-and-statuses)
- [Live networks](#live-networks)
- [Opening a payment](#opening-a-payment)
- [Reading payments](#reading-payments)
- [Cancelling a payment](#cancelling-a-payment)
- [Reopening a payment in review](#reopening-a-payment-in-review)
- [Events](#events)
- [Payment flows](#payment-flows)
- [Discounts](#discounts)
- [Errors](#errors)
- [Go-live checklist](#go-live-checklist)

---

## Addresses

| Use | Address |
|---|---|
| Gateway API, for your backend | `https://api.hdpay.systems` |
| Same API, under the main domain | `https://hdpay.systems/api` |
| Event stream (WebSocket) | `wss://api.hdpay.systems/v1/events/stream` |
| Health check | `https://api.hdpay.systems/health` |
| A working shop built on HDPay, to try the flow | https://example.hdpay.systems |

Every example below uses `https://api.hdpay.systems`. HTTPS only.

---

## Getting access

The HDPay team sets your project up. You exchange the following once.

**You receive**

- A **secret key** (`hdp_sk_…`) for your backend. It is shown once; store it as a secret.
- A **publishable key** (`hdp_pk_…`), only if a browser page of yours opens payments itself.
- The **networks and coins** your project accepts, and your **products** with their USD prices.

**You send us**

- Your backend's **public IP addresses**. Your keys will only work from them.
- Your **products**: an ID, a display name, and the price in USD.
- Your **treasury addresses**: one EVM address (`0x…`) and one Tron address (`T…`) where funds are swept.
- A contact for payment issues (customers who underpaid, see [Payment flows](#payment-flows)).

---

## Authentication

Send the key on every request and when you open the event stream:

```http
Authorization: Bearer hdp_sk_your_secret_key
```

| Key | Can | Where |
|---|---|---|
| `hdp_sk_…` secret | Everything: networks, products, payments, payment list, events | Your server only |
| `hdp_pk_…` publishable | Networks, products, open a payment, read one payment | Safe in a browser checkout |

- Keys work only from your allowed IP addresses, and stop at once when revoked.
- To replace a key without downtime, ask for a new one, switch to it, then have the old one revoked.
- Bodies are JSON: `Content-Type: application/json`.

---

## Amounts and statuses

**Amounts are strings in the coin's smallest unit**, because 18-decimal amounts do not fit in a JavaScript
number. Divide by 10^decimals to display them. Each payment tells you its `decimals`.

| Coin | decimals | 100 of it is |
|---|---|---|
| USDT on Tron (TRC20) | 6 | `"100000000"` |
| USDT, USDC on BNB Smart Chain (BEP-20) | 18 | `"100000000000000000000"` |
| TRX | 6 | `"100000000"` |
| BNB | 18 | `"100000000000000000000"` |

Times are UTC, ISO 8601, ending in `Z`: `"2026-10-22T14:30:00Z"`.

### Statuses

A payment carries two: `internal_status` for your backend, and `status`, which you can show the customer.

| internal_status | status | Meaning |
|---|---|---|
| `pending` | `pending` | Address issued, waiting for payment. |
| `expired` | `expired` | The payment window ended with nothing received. Still watched: a late payment still counts (`late: true`), until the deposit is deleted (`deposit.deleted`, 15 days after it expired by default). |
| `cancelled` | `cancelled` | You cancelled it while it was pending (`POST /v1/deposits/{id}/cancel`). Still watched, like `expired`: a payment sent anyway still counts, and it is deleted the same way. |
| `detected` | `confirming` | A payment arrived and is waiting for confirmations. |
| `partial` | `partial` | Confirmed, but short. The rest can come to the same address until `expires_at`. |
| `confirmed` | `paid` | Paid in full. **Credit the order now.** |
| `swept` | `paid` | The funds reached your treasury. |
| `review` | `contact_support` | The rest of a short payment never came. The address is closed; support decides. |

> **Tolerance.** A payment counts as complete from 0.50% short: a 100 USDT payment is paid from 99.50 USDT.
> This absorbs exchange withdrawal fees. Anything above the price is accepted and marked `difference: "over"`.

Default windows: **30 minutes** to pay, and **24 hours** to send the rest of a short payment. Your project
may have its own.

---

## Live networks

These are watched right now. Ask `GET /v1/chains` for your project's exact list.

| Network | chain key | Coins | Confirmations | Explorer |
|---|---|---|---|---|
| BNB Smart Chain Testnet | `bsc-testnet` | BNB, USDT (BEP-20), USDC (BEP-20) | 15 | https://testnet.bscscan.com |
| Tron Nile Testnet | `tron-nile` | TRX, USDT (TRC20) | 20 | https://nile.tronscan.org |

> **Testnets only for now.** Use test coins from a faucet. Mainnet networks (Ethereum, BNB Smart Chain,
> Tron) are switched on per project when you go live.

---

## Opening a payment

`POST /v1/deposits` gives your customer a fresh address for one payment.

| Field | Required | Notes |
|---|---|---|
| `account_id` | yes | Your user's numeric ID, as a string, `"0"` to `"2147483647"`. |
| `chain` | yes | A chain key, e.g. `tron-nile`. |
| `asset` | yes | A coin your project accepts there, e.g. `USDT`. |
| `product` | if you have products | A product ID from `GET /v1/products`. Its USD price sets the amount. |
| `expected_amount` | no | Smallest unit. Leave it out for a priced product, or to accept any amount. |
| `discount_percent`, `discount_ends_at` | no | A discount for this payment only, both together. See [Discounts](#discounts). |

**Request**

```bash
curl -X POST https://api.hdpay.systems/v1/deposits \
  -H "Authorization: Bearer hdp_sk_your_secret_key" \
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

### What to show the customer

- The `address`, the amount (`expected_amount` ÷ 10^decimals, here **100 USDT**), the network (`chain_name`,
  `standard`) and the deadline (`expires_at`).
- A warning to send only that coin on that network: anything else sent to the address is lost.
- Keep the `id`. It is the payment's reference in every event and reply.

### Good to know

- The same customer, network, coin, product and discount get the **same open address** back while it is
  pending, not a new one on every click.
- A priced product **sets the amount**; an `expected_amount` that differs from the price is refused. A browser
  holding a publishable key cannot change what is charged.
- Priced products are paid in a USD stablecoin (USDT or USDC).

---

## Reading payments

| Route | Key | Returns |
|---|---|---|
| `GET /v1/chains` | either | Your networks and coins, with `decimals`, `contract`, `standard`, and `usd: true` for stablecoins. |
| `GET /v1/products` | either | Your products: `value`, `name`, `subname`, `price` (USD). |
| `GET /v1/deposits/{id}` | either | One payment, in the shape above. |
| `GET /v1/deposits` | secret | Your payments, newest first. Filters: `account_id`, `status` (an internal_status), `skip`, `limit` (max 100). |
| `GET /deposits/{id}` | none | The customer's view of a payment, for a public payment page. The ID is a random UUID, so it works as a private link. |

```bash
curl "https://api.hdpay.systems/v1/deposits?account_id=1006&status=confirmed&limit=10" \
  -H "Authorization: Bearer hdp_sk_your_secret_key"
```

---

## Cancelling a payment

When the customer gives up, or your order is cancelled, tell HDPay you no longer want the payment:

```
POST /v1/deposits/{id}/cancel          secret key only
```

```bash
curl -X POST "https://api.hdpay.systems/v1/deposits/2f0c…/cancel" \
  -H "Authorization: Bearer hdp_sk_your_secret_key"
```

It works **only on a `pending` payment that has received nothing**. The payment becomes `cancelled`
(`status` and `internal_status`), the reply is the payment in the usual shape with `cancelled_at` set, and
you get a `deposit.cancelled` event.

| The payment is… | Reply |
|---|---|
| `pending`, nothing received | `200`: now `cancelled`, with `cancelled_at` |
| cancelled already | `200`: the same payment again, nothing changes, so a retry is safe |
| anything else: `detected`, `partial`, `confirmed`, `review`, `swept`, or `expired` by its own timer | `409`: deposit is …: only a pending deposit can be cancelled |

**Good to know**

- **The address is still watched.** A customer may have sent the money a moment before you cancelled. If it
  arrives, HDPay counts it and you get `deposit.detected`, then `deposit.confirmed` (or `deposit.partial`):
  `late: false` if it was sent before you cancelled, `late: true` after. Deliver the order or refund it: the
  money is never lost.
- **Opening a payment again** for the same customer, network, coin and product gives a **new address**: a
  cancelled payment is never handed out again.
- A cancelled payment that receives nothing is deleted after 15 days, like an expired one
  (`deposit.deleted`). HDPay support can also reopen it (back to `pending`) if your customer asks.
- **Secret key only.** A publishable key, which is in browsers, gets `403`: cancel from your server.

---

## Reopening a payment in review

A payment in `review` was underpaid, and the rest never came in time: its address is closed. When the
customer asks for more time, you can reopen it yourself:

```
POST /v1/deposits/{id}/reopen          secret key only
```

```bash
curl -X POST "https://api.hdpay.systems/v1/deposits/2f0c…/reopen" \
  -H "Authorization: Bearer hdp_sk_your_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"expires_in_seconds": 3600}'
```

The payment goes back to **`partial`** on the **same address**, waiting for the rest until the new
`expires_at`, and you get a `deposit.reopened` event. What already arrived stays counted.

| Field | |
|---|---|
| `expires_in_seconds` | How long the customer has to send the rest, from now: 60 (a minute) to 604800 (a week). Optional: your project's remainder window when left out. |
| `product` | Optional. Another of your priced products, for a customer who changed their mind: the payment is for that product now, at its price, and what they sent counts toward it. If what they sent already pays it, the payment is confirmed at once. Left out, it keeps its product. |

| The payment is… | Reply |
|---|---|
| `review` | `200`: now `partial`, with the new `expires_at` (and the new `product` and `expected_amount`, if you gave one) |
| `review`, with a `product` that what was sent already pays | `200`: **paid**: `confirmed` at once, for that product, and you get `deposit.confirmed` (not `deposit.reopened`). Deliver the order |
| `cancelled` | `409`: deposit is cancelled: open a new deposit instead (POST /v1/deposits) |
| anything else | `409`: deposit is …: only a deposit in review can be reopened |

**Good to know**

- **Still to send** is `expected_amount − received_amount`. If a discount ended before the payment was
  complete, the full price is due: the reply's `expected_amount` already says so.
- **A returning customer:** before opening a new payment for someone, look for their unfinished ones with
  `GET /v1/deposits?account_id=…&status=review`, and offer to finish them (pay the rest, or use what they sent
  for another product). For a refund, send them to HDPay support.
- **You can't reopen a payment you cancelled.** Open a new payment instead (`POST /v1/deposits`): it gets a
  new address.
- To accept a short payment as it is, without waiting for the rest, ask HDPay support to **credit** it.
- **Secret key only.** A publishable key, which is in browsers, gets `403`: reopen from your server.

---

## Events

Everything that happens to your payments is an event, numbered in order (`seq`). Each event carries the
payment **as it is right after the change**, in the same shape as `GET /v1/deposits/{id}`. Do not poll
payments; follow events.

### The stream (recommended)

1. Connect to `wss://api.hdpay.systems/v1/events/stream` with the `Authorization` header.
2. HDPay sends `{"type": "ready", "after": 41}`, then every event after the last one you acknowledged, then
   each new one within about a second.
3. Handle the event (credit the order, notify the customer), then send `{"ack": 42}`.
4. If the connection drops, reconnect with a growing pause (1s, 2s, 4s… up to a minute). HDPay resumes after
   your last acknowledgement, so nothing is missed.

```
<- {"type": "ready", "after": 41}
<- {"id": "evt_…_42", "seq": 42, "type": "deposit.detected", "created_at": "…", "deposit": {…}}
-> {"ack": 42}
<- {"id": "evt_…_43", "seq": 43, "type": "deposit.confirmed", "created_at": "…", "deposit": {…}}
-> {"ack": 43}
```

> **Delivery is at least once.** After a reconnect an event can arrive again. Store the event `id`s you
> handled and skip repeats, so an order is never credited twice.

Close codes: `4401` missing or revoked key; `4403` secret key needed, IP not allowed, or project not active.

### Or poll

```bash
curl "https://api.hdpay.systems/v1/events?after=41&limit=100" -H "Authorization: Bearer hdp_sk_your_secret_key"
# -> {"events": [ … ], "last_seq": 43}

curl -X POST https://api.hdpay.systems/v1/events/ack \
  -H "Authorization: Bearer hdp_sk_your_secret_key" -H "Content-Type: application/json" \
  -d '{"seq": 43}'
```

### Event types

| Type | When | What to do |
|---|---|---|
| `deposit.created` | An address was issued. | Nothing: you already have the reply. |
| `deposit.detected` | A payment arrived; waiting for confirmations. | Tell the customer it is on its way. |
| `deposit.partial` | Confirmed, but short. | Ask for the rest by `expires_at`. |
| `deposit.confirmed` | Paid in full, or credited by support. | **Credit the order.** Thank the customer. |
| `deposit.expired` | The window ended with nothing received. | Tell the customer; they can start again. |
| `deposit.cancelled` | You cancelled it while it was pending (`POST /v1/deposits/{id}/cancel`). It is `cancelled` now, with `cancelled_at`. | Nothing: you asked for it. If `deposit.detected` follows, the customer had already paid. |
| `deposit.deleted` | It expired with nothing received and was deleted (by default 15 days after it expired). The address is no longer watched, and `GET /v1/deposits/{id}` returns 404. | Drop the order, or let the customer start again. |
| `deposit.review` | The rest of a short payment never came. | Ask the customer to contact support. |
| `deposit.reopened` | You (`POST /v1/deposits/{id}/reopen`) or HDPay support gave the customer more time: for the rest of a short payment (`partial`), or, by support only, for the whole payment after it expired (`pending` again, same address). | Ask for the payment, or the rest, by the new `expires_at`. |
| `deposit.discount_expired` | A discount ended before the payment was complete. | Ask for the full price less what came. |
| `deposit.swept` | The funds reached your treasury. | Bookkeeping only. |

---

## Payment flows

### Paid in full

1. Your backend opens the payment and shows the address: **send 100 USDT (TRC20) on Tron Nile by 14:30 UTC**.
2. The customer sends 100 USDT → `deposit.detected`.
3. 20 confirmations later → `deposit.confirmed` with `status: "paid"`. Credit the order.
4. The funds are swept to your treasury → `deposit.swept`.

### Underpaid, then reopened

The customer owes 100 USDT and sends 60.

1. `deposit.partial`: `received_amount` 60, `expected_amount` 100, and a deadline in `expires_at`. Ask for the
   remaining 40 to the same address.
2. If the rest never comes → `deposit.review` (`status: "contact_support"`). The address is closed. Ask the
   customer to contact you.
3. The customer writes in. **You reopen it** (`POST /v1/deposits/{id}/reopen`, see
   [Reopening a payment in review](#reopening-a-payment-in-review)) → `deposit.reopened`; or HDPay support
   **credits** it as it is (→ `deposit.confirmed`), or reopens it with a new amount and deadline.

On `deposit.reopened`, build the customer's message from three fields:

- **Still to send** = `expected_amount − received_amount`, in display units
- **Where** = `address` (the same one) on `chain_name`
- **By** = `expires_at`

```
Subject: You can complete your payment for $100,000

We received 60 of 100 USDT. Send the remaining 40 USDT (TRC20) to the same address:

    TCoMSPhQ6ftCtBLaMaf9M3sCtrg4LH2zyM

Before Oct 24, 2026 09:15 UTC.
```

The same three facts fit an SMS or chat message:

```
Send the remaining 40 USDT (TRC20, Tron Nile) to TCoMSPh…2zyM by Oct 24 09:15 UTC. Ref 6ad93594.
```

---

## Discounts

A discount belongs to **one payment**. Send `discount_percent` (1–99) and `discount_ends_at` when you open
it, for a product with a price.

```json
{"account_id": "1006", "chain": "tron-nile", "asset": "USDT", "product": "100k",
 "discount_percent": 20, "discount_ends_at": "2026-10-31T18:00:00Z"}
```

The reply expects the discounted price (`expected_amount: "80000000"`) and includes the discount: `percent`,
`ends_at`, `full_amount`, `amount` and `status`.

| discount.status | Meaning |
|---|---|
| `active` | Running; the discounted price is due. |
| `kept` | Paid in full before it ended: the discount holds. |
| `lapsed` | It ended before the payment was complete: the **full price** is due (`deposit.discount_expired`). |
| `honored` | It had ended, and support kept it anyway. |

Payments made before `ends_at` count toward the discounted price, even if confirmed after it.

---

## Errors

Errors are JSON: `{"detail": "…"}`. Show your own message to customers; log HDPay's for your team.

| Status | When | detail |
|---|---|---|
| 401 | No key, or not `Bearer hdp_…` | Send the project's API key: Authorization: Bearer hdp_... |
| 401 | Unknown or revoked key | Unknown or revoked API key |
| 403 | Publishable key on a secret route | This needs the project's secret key (hdp_sk_...) |
| 403 | Project disabled or not set up yet | This project is not active |
| 403 | Your IP is not allowed | Requests from 203.0.113.9 are not allowed for this project |
| 404 | Unknown payment, or another project's | deposit not found |
| 409 | Cancelling a payment that is not `pending`, or received something | deposit is detected: only a pending deposit can be cancelled |
| 409 | Reopening a payment that is not in `review` | deposit is partial: only a deposit in review can be reopened |
| 409 | Reopening a payment you cancelled | deposit is cancelled: open a new deposit instead (POST /v1/deposits) |
| 422 | `expires_in_seconds` under 60 or over a week | Input should be greater than or equal to 60 |
| 422 | Network not accepted | this project does not accept 'solana'; it accepts … |
| 422 | Coin not accepted there | this project accepts TRX, USDT on Tron Nile Testnet, not 'USDC' |
| 422 | Unknown product | product must be one of 10k, 100k |
| 422 | Priced product paid in a non-stablecoin | $100,000 costs $100: pay it in USDT or USDC |
| 422 | `expected_amount` differs from the price | … leave expected_amount out, it follows the price |
| 422 | Only one discount field, or one in the past | a discount needs both discount_percent and discount_ends_at |
| 422 | Bad `account_id` | account_id must be a whole number from 0 to 2147483647 |

---

## Go-live checklist

- [ ] The secret key lives only on your server, in a secret store, never in a browser or a repository.
- [ ] Your server's public IPs are on your project's allowed list.
- [ ] Your backend follows the event stream, acknowledges each event, and reconnects by itself.
- [ ] Orders are credited on `deposit.confirmed` only, and each event `id` is handled once.
- [ ] Amounts are kept as strings and divided by 10^decimals only for display.
- [ ] Customers are told the exact coin and network, and what to do on `deposit.partial`, `deposit.review`
      and `deposit.reopened`.
- [ ] A full test payment has gone through on a testnet: opened, paid, confirmed, credited, swept.
- [ ] You have asked for your mainnet networks to be switched on.

---

HDPay · Hierarchical Deterministic Pay. API base: `https://api.hdpay.systems`. For keys, IP changes,
networks and payment reviews, contact the HDPay team.
