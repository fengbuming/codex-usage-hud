import pytest

from codex_usage_hud.speed_title_model import SPEED_TITLE_STEPS, SpeedTitleState, speed_title_color


@pytest.mark.parametrize("tier,step", enumerate(SPEED_TITLE_STEPS, 1))
def test_all_thirteen_shared_vehicle_thresholds_settle_after_800ms(tier, step):
    state = SpeedTitleState()
    assert state.update(step[2], now=0) == 0.8
    assert state.tier == 0
    state.update(step[2], now=0.5)
    assert state.advance(now=0.799) == 0.8
    assert state.tier == 0
    assert state.advance(now=0.8) is None
    assert state.tier == tier


def test_native_tiers_keep_renderer_hysteresis_idle_delay_and_invalid_reset():
    state = SpeedTitleState()
    state.update(40, now=0)
    state.advance(now=0.8)
    assert state.tier == 2
    assert state.update(36, now=1) is None
    assert state.tier == 2
    assert state.update(35.99, now=2) == 2.8
    state.advance(now=2.8)
    assert state.tier == 1
    assert state.update(0.4, now=3) == 6
    state.advance(now=5.99)
    assert state.tier == 1
    assert state.advance(now=6) is None
    assert state.tier == 0
    assert state.update(None, now=7) is None
    assert state.pending is None


def test_speed_colors_use_shared_linear_light_ramp():
    assert speed_title_color(0) == "#7c8798"
    assert speed_title_color(2000) == "#facc15"
    assert speed_title_color(4000) == speed_title_color(2000)
    assert speed_title_color(40) != speed_title_color(240)
