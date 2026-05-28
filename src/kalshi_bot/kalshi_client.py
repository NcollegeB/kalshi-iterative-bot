from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path
from socket import timeout as SocketTimeout
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .config import KalshiConfig
from .models import BookSide, Market, OutcomeSide, ProposedOrder, TopOfBook


class KalshiApiError(RuntimeError):
    pass


class KalshiClient:
    def __init__(self, config: KalshiConfig, timeout_seconds: int = 20) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self._private_key = None

    def get_exchange_status(self) -> dict[str, Any]:
        return self._request("GET", "/exchange/status", authenticated=False)

    def list_markets(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        status: str = "open",
        event_ticker: str | None = None,
        series_ticker: str | None = None,
        tickers: str | None = None,
        mve_filter: str | None = None,
        min_close_ts: int | None = None,
        max_close_ts: int | None = None,
    ) -> tuple[list[Market], str | None]:
        query: dict[str, Any] = {"limit": limit, "status": status}
        if cursor:
            query["cursor"] = cursor
        if event_ticker:
            query["event_ticker"] = event_ticker
        if series_ticker:
            query["series_ticker"] = series_ticker
        if tickers:
            query["tickers"] = tickers
        if mve_filter:
            query["mve_filter"] = mve_filter
        if min_close_ts:
            query["min_close_ts"] = min_close_ts
        if max_close_ts:
            query["max_close_ts"] = max_close_ts
        data = self._request("GET", f"/markets?{urlencode(query)}", authenticated=False)
        return [Market.from_api(raw) for raw in data.get("markets", [])], data.get("cursor")

    def get_orderbook(self, ticker: str) -> TopOfBook:
        data = self._request("GET", f"/markets/{ticker}/orderbook", authenticated=False)
        orderbook = data.get("orderbook_fp") or data.get("orderbook") or {}
        yes_levels = orderbook.get("yes_dollars") or orderbook.get("yes") or []
        no_levels = orderbook.get("no_dollars") or orderbook.get("no") or []
        yes_bid, yes_size = _best_bid(yes_levels)
        no_bid, no_size = _best_bid(no_levels)
        return TopOfBook(yes_bid=yes_bid, yes_bid_size=yes_size, no_bid=no_bid, no_bid_size=no_size)

    def get_market(self, ticker: str) -> Market:
        data = self._request("GET", f"/markets/{ticker}", authenticated=False)
        return Market.from_api(data.get("market", data))

    def get_balance(self) -> dict[str, Any]:
        return self._request("GET", "/portfolio/balance", authenticated=True)

    def get_order(self, order_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/portfolio/orders/{order_id}", authenticated=True)
        return dict(data.get("order", data))

    def list_settlements(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        ticker: str | None = None,
        event_ticker: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
        subaccount: int | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {"limit": limit}
        if cursor:
            query["cursor"] = cursor
        if ticker:
            query["ticker"] = ticker
        if event_ticker:
            query["event_ticker"] = event_ticker
        if min_ts:
            query["min_ts"] = min_ts
        if max_ts:
            query["max_ts"] = max_ts
        if subaccount is not None:
            query["subaccount"] = subaccount
        data = self._request("GET", f"/portfolio/settlements?{urlencode(query)}", authenticated=True)
        return list(data.get("settlements", [])), data.get("cursor")

    def create_event_order_v2(self, order: ProposedOrder) -> dict[str, Any]:
        payload = event_order_payload(order)
        return self._request("POST", "/portfolio/events/orders", body=payload, authenticated=True)

    def cancel_event_order_v2(self, order_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/portfolio/events/orders/{order_id}", authenticated=True)

    def make_client_order_id(self, prefix: str = "kb") -> str:
        return f"{prefix}-{uuid.uuid4()}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        authenticated: bool,
    ) -> dict[str, Any]:
        url = self.config.rest_url + path
        body_bytes = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers.update(self._auth_headers(method, path))

        request = Request(url, data=body_bytes, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise KalshiApiError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except (URLError, ConnectionError, TimeoutError, SocketTimeout) as exc:
            raise KalshiApiError(f"{method} {path} failed with network error: {exc}") from exc

        if not payload:
            return {}
        return json.loads(payload)

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if not self.config.has_credentials or not self.config.api_key_id:
            raise KalshiApiError("Authenticated Kalshi request needs KALSHI_API_KEY_ID and a private key.")
        timestamp = str(int(time.time() * 1000))
        sign_path = urlparse(self.config.rest_url + path).path
        sign_path = sign_path.split("?")[0]
        message = f"{timestamp}{method.upper()}{sign_path}".encode("utf-8")
        signature = self._sign(message)
        return {
            "KALSHI-ACCESS-KEY": self.config.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": signature,
        }

    def _sign(self, message: bytes) -> str:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        if self._private_key is None:
            if self.config.private_key_pem:
                pem = self.config.private_key_pem.encode("utf-8")
            elif self.config.private_key_path:
                pem = Path(self.config.private_key_path).expanduser().read_bytes()
            else:  # pragma: no cover - protected by has_credentials
                raise KalshiApiError("No private key configured.")
            self._private_key = serialization.load_pem_private_key(pem, password=None)

        signature = self._private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")


def event_order_payload(order: ProposedOrder) -> dict[str, Any]:
    if (
        (order.book_side == BookSide.ASK and order.outcome == OutcomeSide.NO and not order.reduce_only)
        or (order.book_side == BookSide.BID and order.outcome == OutcomeSide.NO and order.reduce_only)
    ):
        yes_book_price = round(1.0 - order.price, 4)
    else:
        yes_book_price = order.price
    time_in_force = "immediate_or_cancel" if order.reduce_only else order.time_in_force
    post_only = False if order.reduce_only else order.post_only
    return {
            "ticker": order.ticker,
            "client_order_id": order.client_order_id,
            "side": order.book_side.value,
            "count": f"{order.count:.2f}",
            "price": f"{yes_book_price:.4f}",
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": post_only,
            "cancel_order_on_pause": True,
            "reduce_only": order.reduce_only,
    }


def _best_bid(levels: list[list[Any]]) -> tuple[float | None, float]:
    best_price: float | None = None
    best_size = 0.0
    for raw_price, raw_size in levels:
        price = float(raw_price)
        size = float(raw_size)
        if best_price is None or price > best_price:
            best_price = price
            best_size = size
    return best_price, best_size


def book_side_for_outcome(outcome_price_side: str) -> BookSide:
    if outcome_price_side == "yes":
        return BookSide.BID
    if outcome_price_side == "no":
        return BookSide.ASK
    raise ValueError(f"Unsupported outcome side: {outcome_price_side}")
