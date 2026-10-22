# Decision: underpaid and overpaid deposits

Status: **agreed, partly built, with one change**. Date: 2026-10-20.

Update 2026-10-21: HDPay no longer sends email. Wherever this document says the customer is emailed,
the project now gets an event instead (`deposit.partial`, `deposit.review`, `deposit.reopened`, …) over
its WebSocket, and tells its customer itself. Remainder windows are set per project.

Built: the `partial` status with its "send the remaining X" page and email, and a reorg that rolls
back only the newest payment (to the deposit's `confirmed_amount`).

**Changed from decision 3 below:** the remainder window is `HDPAY_REMAINDER_WINDOW_MINUTES` (a day by
default), counted once from the first short payment, not restarted by each payment. When it ends
the deposit goes to `review` and its address is **no longer watched**: the page tells the
customer the address has expired and to contact support, who resolve it with the credit action.

Not built yet: the payments table, the overpaid tag.

## Problem

A deposit asks for an exact amount, but customers do not always send it. An exchange withdrawal fee
or a typo turns 5 USDC into 4.5. Today such a deposit waits for its confirmations, goes to
`review`, and its address stops being watched, so if the customer sends the missing 0.5 it
is never added.

## Decisions

### 1. Underpaid: ask for the rest on the same address

A new status, `partial`, between `detected` and `confirmed`:

```
pending ─▶ detected ─▶ partial ─▶ (rest arrives) ─▶ confirmed ─▶ swept
                          │
                          └─▶ window ends unpaid ─▶ review (still watched)
```

- As soon as a short payment is **seen**, the deposit page tells the customer how much is still
  missing and to send it to **the same address**: *"Received 4.5 USDC. Send the remaining 0.5 USDC
  to this same address."* With the address, QR code and a "copy 0.5" button.
- The deposit is paid when the **confirmed** total reaches the expected amount, within the existing
  `HDPAY_AMOUNT_TOLERANCE_BPS` (0.50%). Each payment waits for its own confirmations.
- Deposits without an expected amount ("any amount") never become `partial`.

### 2. Waiting for the rest: restart the window after each partial payment

Each partial payment restarts the payment window (`HDPAY_DEPOSIT_TTL_MINUTES`, 30 minutes) from the
moment it arrives, so a first part sent at minute 29 still leaves the customer a full window.

**Why:** the deadline exists to stop an address being shown as "open" forever, not to punish a
customer who is already paying.

### 3. Rest never comes: credit what arrived, decided by support

When the window ends without the rest, the deposit goes to `review`. Its address stays
watched, so a late remainder still completes it automatically. In the admin, the first action is
**Credit the received amount** (for example 4.5 USDC) to the account.

**Why not refund on chain:** the sending address is often an exchange's shared hot wallet, so a
refund can be lost or land with nobody; refunds also cost gas. Every account has a balance, so
crediting what arrived is fair and simple, and the customer can top up or use it.

### 4. Overpaid: credit everything that arrived

5.5 USDC sent for 5 is credited as 5.5. The admin shows an **overpaid** tag so it is visible, but
nobody has to act on it. Same reasoning as 3: no on-chain refunds.

This also holds if HDPay is later used for fixed-price purchases: a shortfall or excess still goes
to the account balance rather than back on chain.

## Needed to build this safely: one record per payment

A deposit currently keeps one running total and one transaction hash. With several payments that
breaks: if the block of the second payment is reorganised away, the reorg guard resets the
**whole** total to 0.

Add a `hdpay_payments` table with one row per transfer (deposit, transaction hash, log index,
amount, block, status). Each payment is confirmed on its own; the deposit's received amount is the
sum of its confirmed payments. The admin deposit page lists every payment with its explorer link.

## Other changes that come with it

- Status machine: `partial` is watched like `pending`, `expired` and `detected`; `review` also
  becomes watched, so a late remainder completes it.
- Sweeper: leaves `partial` deposits alone until they are paid, so one deposit never pays gas twice.
- Admin: `partial` and `overpaid` in the overview counts and filters; credit-received action.
- Web: the progress steps gain "Send the remaining X" while a deposit is `partial`.
- Email: a "payment received, X still missing" message (see the notification emails).

## Not decided here

- Whether support can also mark a `review` deposit as refunded by hand (for a refund made
  outside HDPay).
