from kalshi_bot.models import TopOfBook


def test_yes_and_no_asks_are_complements():
    top = TopOfBook(yes_bid=0.42, yes_bid_size=10, no_bid=0.53, no_bid_size=8)
    assert top.yes_ask == 0.47
    assert top.no_ask == 0.58
    assert top.yes_spread == 0.05

