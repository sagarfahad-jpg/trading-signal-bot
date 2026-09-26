"""اختبارات select_entry_zone — «الأضيق يفوز، والأوسع يبقى سياقاً» (منطقة دخول بوابة الظل)."""
from htf_zones import HTFZone, select_entry_zone


def Z(low, high, tf, direction="demand", zone_type="ob"):
    strength = {"1h": 1.0, "4h": 2.0, "daily": 3.0}[tf]
    return HTFZone(low=low, high=high, zone_type=zone_type, direction=direction,
                   timeframe=tf, strength=strength)


DAILY = Z(90.0, 110.0, "daily", zone_type="fvg")           # سياق يومي عرضه 20، السعر 100 داخله


def test_narrowest_across_4h_and_1h_wins():
    h4 = Z(96.0, 104.0, "4h")                                # عرض 8
    h1 = Z(98.0, 101.0, "1h", zone_type="fvg")               # عرض 3 ← الأضيق
    assert select_entry_zone([DAILY, h4, h1], 100.0, "call", DAILY) == {
        "low": 98.0, "high": 101.0, "tf": "1h", "type": "fvg", "source": "htf_1h"}
    h4_narrow = Z(99.0, 101.0, "4h")                         # 4h أضيق (2) من 1h (3)
    assert select_entry_zone([DAILY, h4_narrow, h1], 100.0, "call", DAILY)["source"] == "htf_4h"


def test_equal_width_tie_goes_to_higher_timeframe():
    ez = select_entry_zone([DAILY, Z(99.0, 101.0, "1h"), Z(98.5, 100.5, "4h")], 100.0, "call", DAILY)
    assert ez["tf"] == "4h" and ez["low"] == 98.5


def test_candidates_must_align_and_contain_price():
    opposite = Z(99.0, 101.0, "1h", direction="supply")
    below = Z(95.0, 97.0, "1h")
    assert select_entry_zone([DAILY, opposite, below], 100.0, "call", DAILY)["source"] == "daily_edge"


def test_candidate_must_be_narrower_than_and_intersect_the_context():
    ctx = Z(99.0, 103.0, "daily")                            # السعر 98.5 خارج السياق (nearest_zone)
    wider = Z(97.0, 104.0, "4h")                             # يحتوي السعر لكنه أعرض من السياق
    apart = Z(98.0, 98.9, "1h")                              # يحتوي السعر ولا يتقاطع مع السياق
    assert select_entry_zone([ctx, wider, apart], 98.5, "call", ctx)["source"] == "daily_edge"


def test_4h_context_looks_at_1h_only_and_otherwise_keeps_itself():
    ctx = Z(97.0, 103.0, "4h")
    h1 = Z(99.5, 100.5, "1h")
    same_tf = Z(99.8, 100.2, "4h")                           # نفس فريم السياق: ليس مرشّحاً
    assert select_entry_zone([ctx, h1, same_tf], 100.0, "call", ctx)["source"] == "htf_1h"
    assert select_entry_zone([ctx, same_tf], 100.0, "call", ctx) == {
        "low": 97.0, "high": 103.0, "tf": "4h", "type": "ob", "source": "htf_4h"}


def test_1h_context_is_its_own_entry_zone():
    ctx = Z(99.0, 101.0, "1h", zone_type="inv_fvg")
    assert select_entry_zone([ctx], 100.0, "call", ctx) == {
        "low": 99.0, "high": 101.0, "tf": "1h", "type": "inv_fvg", "source": "htf_1h"}


def test_daily_edge_is_the_far_half():
    assert select_entry_zone([DAILY], 100.0, "call", DAILY) == {
        "low": 90.0, "high": 100.0, "tf": "daily", "type": "fvg", "source": "daily_edge"}
    supply = Z(90.0, 110.0, "daily", direction="supply")
    put = select_entry_zone([supply], 100.0, "put", supply)
    assert (put["low"], put["high"], put["source"]) == (100.0, 110.0, "daily_edge")


def test_daily_edge_does_not_depend_on_where_price_sits():
    for price in (111.0, 108.0, 95.0):                       # خارج المنطقة، أعلاها، عميقاً فيها
        ez = select_entry_zone([DAILY], price, "call", DAILY)
        assert (ez["low"], ez["high"]) == (90.0, 100.0)


def test_put_picks_narrowest_supply():
    supply = Z(90.0, 110.0, "daily", direction="supply")
    h1 = Z(99.0, 101.0, "1h", direction="supply")
    demand = Z(99.5, 100.5, "1h", direction="demand")        # أضيق لكنه معاكس
    assert select_entry_zone([supply, h1, demand], 100.0, "put", supply)["low"] == 99.0
