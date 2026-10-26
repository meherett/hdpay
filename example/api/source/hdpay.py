"""
HDPay's gateway, called with the project's secret key.

Only this server holds the key: the shop's web page calls this API, never HDPay.
"""

from typing import Any

import httpx


class HDPayError(Exception):
    """HDPay refused or could not be reached; `status` and `detail` are what the shop answers with."""

    def __init__(self, status: int, detail: Any):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class HDPay:
    def __init__(self, url: str, secret_key: str, transport: httpx.BaseTransport | None = None):
        self.url = url.rstrip("/")
        self.secret_key = secret_key
        self.client = httpx.Client(
            base_url=f"{self.url}/v1",
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=20,
            transport=transport,
        )

    @property
    def events_url(self) -> str:
        """The event stream, over ws:// or wss:// like the API's own scheme."""
        return "ws" + self.url.removeprefix("http") + "/v1/events/stream"

    def chains(self) -> list[dict[str, Any]]:
        return self._call("GET", "/chains")

    def products(self) -> list[dict[str, Any]]:
        return self._call("GET", "/products")

    def open_deposit(self, body: dict[str, Any]) -> dict[str, Any]:
        """A new address for the account, or its open one for the same network, asset and product."""
        return self._call("POST", "/deposits", json=body)

    def deposit(self, deposit_id: str) -> dict[str, Any]:
        return self._call("GET", f"/deposits/{deposit_id}")

    def cancel(self, deposit_id: str) -> dict[str, Any]:
        """Cancel a pending deposit (409 once anything arrived); its address stays watched by HDPay."""
        return self._call("POST", f"/deposits/{deposit_id}/cancel")

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.secret_key:
            raise HDPayError(503, "The shop is not set up yet: put the project's secret key in HDPAY_SECRET_KEY.")
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise HDPayError(502, "The payment service cannot be reached. Try again in a moment.") from None
        if response.status_code in (401, 403):
            # the shop's key, not the customer's fault
            raise HDPayError(502, "The payment service did not accept the shop's key.")
        if response.is_error:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            status = response.status_code if response.status_code < 500 else 502
            raise HDPayError(status, detail or "The payment service failed. Try again in a moment.")
        return response.json()
