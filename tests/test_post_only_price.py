from kalshi_bot.cli import _post_only_price
from kalshi_bot.models import OutcomeSide, TopOfBook


class FakeClient:
    def __init__(self, top):
        self.top = top

    def get_orderbook(self, ticker):
        return self.top


def test_post_only_yes_bid_improves_without_crossing():
    client = FakeClient(TopOfBook(yes_bid=0.40, yes_bid_size=1, no_bid=0.50, no_bid_size=1))
    assert _post_only_price(client, "T", OutcomeSide.YES, 0.50) == 0.41


def test_post_only_no_bid_improves_without_crossing():
    client = FakeClient(TopOfBook(yes_bid=0.89, yes_bid_size=1, no_bid=0.05, no_bid_size=1))
    # no_ask is 1 - yes_bid = 0.11; a 0.06 no bid maps to a 0.94 YES ask.
    assert _post_only_price(client, "T", OutcomeSide.NO, 0.11) == 0.06


def test_post_only_yes_joins_bid_when_spread_is_one_tick():
    client = FakeClient(TopOfBook(yes_bid=0.12, yes_bid_size=1, no_bid=0.87, no_bid_size=1))
    assert _post_only_price(client, "T", OutcomeSide.YES, 0.13) == 0.12
