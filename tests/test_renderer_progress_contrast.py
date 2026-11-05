"""Measure rendered gradient/ink contrast independently of the implementation."""

import json
import math
import re
import subprocess

import pytest

from codex_usage_hud.renderer_assets.budget import TEXT
from codex_usage_hud.renderer_assets.layout_style import TEXT as STYLE


def rendered_rails():
    start = TEXT.index("  function normalizeProgressRatio(")
    end = TEXT.index("  function renderProgressList(", start)
    script = r'''
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const document = {createElement() {return {
  dataset: {}, style: {setProperty(name, value) {this[name] = value;}}, children: [],
  appendChild(child) {this.children.push(child);}, append(...children) {this.children.push(...children);},
  setAttribute(name, value) {this[name] = value;},
};}};
''' + TEXT[start:end] + r'''
const themes = [
  {progressTrack:'#262c33'},
  {progressTrack:'#eeeeef',progressDay:'#339cff',progressDayEnd:'#5d8bff',progressWeek:'#6ea8ff',progressWeekEnd:'#8db7ff'},
  {progressTrack:'#777777',progressDay:'#808080',progressDayEnd:'#555555',progressWeek:'#ff0000',progressWeekEnd:'#00ff00'},
  {progressTrack:'#17151c',progressDay:'#8000ff',progressDayEnd:'#9900ff',progressWeek:'#00ff80'},
];
const result=[];
for (let theme=0; theme<themes.length; theme++) {
  for (const tone of ['session','cache','day','week','error']) {
    for (let percent=0; percent<=100; percent++) {
      const rail=progressRail({tone,ratio:percent/100,label:'本会话 12k/$0.12/52ts',rightText:'总 $100.00'},themes[theme]);
      result.push({theme,tone,percent,track:themes[theme].progressTrack,style:rail.style,fill:rail.children[1].style,
        trackLayer:rail.children[2],fillLayer:rail.children[3]});
    }
  }
}
console.log(JSON.stringify(result));
'''
    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def luminance(rgb):
    normalized = [value / 255 for value in rgb]
    channels = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in normalized]
    return sum(weight * value for weight, value in zip((.2126, .7152, .0722), channels))


def contrast(a, b):
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + .05) / (dark + .05)


def hex_rgb(value):
    return [int(value[offset:offset + 2], 16) for offset in (1, 3, 5)]


@pytest.fixture(scope="module")
def rails():
    return rendered_rails()


def test_text_passes_contrast_through_every_cache_percentage_and_gradient(rails):
    minimum = math.inf
    for rail in rails:
        fill_ink = hex_rgb(rail["style"]["--codex-usage-hud-progress-fill-ink"])
        track_ink = hex_rgb(rail["style"]["--codex-usage-hud-progress-track-ink"])
        assert contrast(track_ink, hex_rgb(rail["track"])) >= 4.5, rail
        stops = re.findall(r"rgb\((\d+), (\d+), (\d+)\)", rail["fill"]["background"])
        first, last = ([int(channel) for channel in stop] for stop in stops)
        for step in range(65):
            # CSS explicitly interpolates in sRGB; sample between the stops,
            # independently of the implementation's sample count/rounding.
            background = [a + (b - a) * step / 64 for a, b in zip(first, last)]
            ratio = contrast(fill_ink, background)
            minimum = min(minimum, ratio)
            assert ratio >= 4.5, (rail["theme"], rail["tone"], rail["percent"], step, ratio)
    assert minimum >= 4.7


def test_text_layers_match_and_clip_at_exact_fill_boundary_in_both_layouts(rails):
    for rail in rails:
        assert rail["fill"]["width"] == f'{rail["percent"]}%'
        assert rail["style"]["--codex-usage-hud-progress-fill-width"] == rail["fill"]["width"]
        base, filled = rail["trackLayer"], rail["fillLayer"]
        assert base["children"] == filled["children"]
        assert filled["aria-hidden"] == "true"
        assert "codex-usage-hud-progress-track-text" in filled["className"]
    assert "mix-blend-mode: difference" not in STYLE
    assert "clip-path: inset(0 0 0 var(--codex-usage-hud-progress-fill-width, 0%))" in STYLE
    assert "clip-path: inset(0 calc(100% - var(--codex-usage-hud-progress-fill-width, 0%)) 0 0)" in STYLE
    # Both layers share this class, so sizing/padding/ellipsis remain identical
    # in the collapsed strip and in the expanded budget rows.
    assert ".codex-usage-hud-progress-strip .codex-usage-hud-progress-track-text" in STYLE
    assert ".codex-usage-hud-budget-rails .codex-usage-hud-progress-track-text" in STYLE
    assert ".codex-usage-hud-progress-fill::before" not in STYLE
