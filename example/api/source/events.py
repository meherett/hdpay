"""
Follows HDPay's event stream (WS /v1/events/stream) for as long as the API runs.

Each event is handled, then acknowledged with {"ack": seq}; after a disconnect HDPay resumes after
the last acknowledged one, so nothing is missed while this API is down.
"""

import asyncio
import json
import logging

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .shop import Shop

log = logging.getLogger("example.events")

MAX_BACKOFF_SECONDS = 60


async def follow(shop: Shop) -> None:
    hdpay = shop.hdpay
    backoff = 1
    while True:
        try:
            async with connect(hdpay.events_url, additional_headers={"Authorization": f"Bearer {hdpay.secret_key}"}) as ws:
                async for raw in ws:
                    message = json.loads(raw)
                    if message.get("type") == "ready":
                        log.info("Following HDPay events after #%s", message["after"])
                        backoff = 1
                        continue
                    if message.get("type") == "error":
                        log.warning("HDPay: %s", message.get("message"))
                        continue
                    await asyncio.to_thread(shop.handle, message)
                    await ws.send(json.dumps({"ack": message["seq"]}))
        except asyncio.CancelledError:
            raise
        except InvalidStatus as exc:
            log.error("HDPay refused the event stream (HTTP %s): check HDPAY_SECRET_KEY", exc.response.status_code)
            backoff = MAX_BACKOFF_SECONDS
        except ConnectionClosed as exc:
            log.warning("HDPay event stream closed: %s", exc.rcvd.reason if exc.rcvd else exc)
        except OSError as exc:
            log.warning("HDPay is unreachable: %s", exc)
        except Exception:
            # an email that failed: the event was not acknowledged, so it comes again on reconnect
            log.exception("Could not handle an HDPay event")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
