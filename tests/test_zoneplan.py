import pytest

from tradebot import data
from tradebot.strategy import Market, mode_config
from tradebot.zoneplan import best_plan, plan_zones, scenarios


@pytest.fixture(scope="module")
def market():
    return Market(data.synthetic(3000, seed=5), mode_config("scalp", htf=12))


def _views(market, step=50):
    for t in range(300, len(market.c), step):
        an = market.analyze(t)
        yield an, plan_zones(market, an)


def test_plans_follow_the_bot_rules(market):
    cfg = market.cfg
    seen = 0
    for an, plans in _views(market):
        for p in plans:
            seen += 1
            long = p.side == "long"
            assert p.entry == (p.zone.high if long else p.zone.low)            # order at the near edge
            assert (p.stop < p.zone.low) if long else (p.stop > p.zone.high)   # stop beyond the zone
            assert abs(p.entry - p.stop) >= cfg.min_stop_atr * an.atr - 1e-9
            assert (p.target > p.entry) if long else (p.target < p.entry)
            against = an.direction == ("down" if long else "up")
            assert p.tradable == (not against and p.rr >= cfg.min_rr)
            assert p.tradable == (p.why_not == "")
            assert abs(an.price - p.entry) <= 8 * an.atr
    assert seen > 50


def test_best_plan_is_tradable_htf_backed_then_nearest(market):
    for an, plans in _views(market):
        best = best_plan(plans)
        tradable = [p for p in plans if p.tradable]
        assert (best is None) == (not tradable)
        if best is None:
            continue
        if any(p.htf_backed for p in tradable):
            assert best.htf_backed
        same = [p for p in tradable if p.htf_backed == best.htf_backed]
        assert abs(an.price - best.entry) == min(abs(an.price - p.entry) for p in same)


def test_scenarios_name_the_best_zone_and_where_it_fails(market):
    checked = 0
    for an, plans in _views(market):
        main, alt = scenarios(an, plans, "H1")
        best = best_plan(plans)
        assert main and alt
        if an.direction in ("up", "down") and best is not None:
            assert f"H1 {an.direction}trend" in main
            assert ("pullback" if an.direction == "up" else "rally") in main
            checked += 1
    assert checked > 0


def test_swing_shows_the_best_zone_scalp_every_one():
    assert mode_config("scalp").zone_view == "all"
    assert mode_config("swing").zone_view == "best"


def test_charts_show_the_nearest_tradable_zones_only(market):
    from tradebot.zoneplan import shown_plans
    for an, plans in _views(market):
        shown = shown_plans(plans, "all", an.price, an.atr)
        tradable = sorted((p for p in plans if p.tradable), key=lambda p: abs(an.price - p.entry))
        assert [p for p in shown if p.tradable] == [p for p in plans if p in tradable[:3]]
        assert all(abs(an.price - p.entry) <= 2 * an.atr for p in shown if not p.tradable)
        assert shown_plans(plans, "best", an.price, an.atr) == ([best_plan(plans)] if best_plan(plans) else [])
