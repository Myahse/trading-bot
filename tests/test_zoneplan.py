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
    from tradebot.zoneplan import same_place, shown_plans
    for an, plans in _views(market):
        shown = shown_plans(plans, "all", an.price, an.atr)
        expect = []
        for p in sorted((p for p in plans if p.tradable), key=lambda p: abs(an.price - p.entry)):
            if len(expect) < 2 and not any(same_place(p.zone, q.zone, an.atr) for q in expect):
                expect.append(p)
        assert shown == [p for p in plans if p in expect]
        assert shown_plans(plans, "best", an.price, an.atr) == ([best_plan(plans)] if best_plan(plans) else [])


def test_stacked_zones_are_shown_as_one_place():
    from tradebot.structure import Zone
    from tradebot.zoneplan import ZonePlan, shown_plans

    def plan(lo, hi):
        return ZonePlan(Zone(lo, hi, 2), "short", False, True, lo, hi + 0.5, lo - 3, True, "")
    a, b, c = plan(101.0, 101.3), plan(101.35, 101.6), plan(103.0, 103.3)   # a and b touch: one place
    assert shown_plans([a, b, c], "all", price=100.0, atr=1.0) == [a, c]
    assert shown_plans([a, c], "all", price=100.0, atr=1.0) == [a, c]


@pytest.mark.parametrize("mode", ["scalp", "swing"])
def test_every_candle_shows_few_distinct_zones(mode):
    """Replays the bot candle by candle: a chart never draws two zones at the same place, never
    more than 2 (scalp) or 1 (swing), and only zones it would trade."""
    from tradebot.zoneplan import SAME_PLACE_ATR, shown_plans
    m = Market(data.synthetic(2500, seed=21), mode_config(mode, htf=12))
    limit = 2 if mode == "scalp" else 1
    drawn = 0
    for t in range(300, len(m.c)):
        an = m.analyze(t)
        if an.atr != an.atr:
            continue
        shown = shown_plans(plan_zones(m, an), m.cfg.zone_view, an.price, an.atr)
        drawn += len(shown)
        assert len(shown) <= limit and all(p.tradable for p in shown)
        for i, p in enumerate(shown):
            for q in shown[i + 1:]:
                assert max(p.zone.low, q.zone.low) - min(p.zone.high, q.zone.high) >= SAME_PLACE_ATR * an.atr
    assert drawn > 500
