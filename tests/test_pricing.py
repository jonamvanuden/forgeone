import pytest

from forgeone.llm.pricing import call_cost, price, resolve_model


@pytest.mark.parametrize("raw,expected", [
    ("claude-haiku-5-5", "claude-haiku-5-5"),
    ("claude-sonnet-5-5-20260928", "claude-sonnet-5-5"),
    ("anthropic:claude-sonnet-5-5", "claude-sonnet-5-5"),
    ("Claude-Opus-5-5", "claude-opus-5-5"),
    ("gpt-unknown", None),
    (None, None),
])
def test_resolve(raw, expected):
    assert resolve_model(raw) == expected


def test_prices():
    assert price("claude-sonnet-5-5", False, 1_000_000, 0) == pytest.approx(2.0)
    assert price("claude-haiku-5-5", False, 0, 1_000_000) == pytest.approx(0.5)
    assert price("claude-haiku-5-5", True, 1_000_000, 0) == pytest.approx(0.5)   # long-prompt rate
    assert price("mystery", False, 10, 10) is None                               # unknown is NOT free


def test_call_cost_picks_long_rate_from_input_size():
    assert call_cost("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.1)        # 0.5 $/M
    assert call_cost("claude-haiku-5-5", 50_000, 0) == pytest.approx(0.005)       # 0.1 $/M
