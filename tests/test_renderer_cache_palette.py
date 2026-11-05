"""Cache hit intensity and its separation from the budget tabs."""

import subprocess

from codex_usage_hud.renderer_assets.budget import TEXT


def test_cache_palette_tracks_hits_and_only_colors_cache_and_session_rails():
    start = TEXT.index("  function normalizeProgressRatio(")
    end = TEXT.index("  function renderProgressList(", start)
    script = r'''
const assert = require('node:assert/strict');
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const document = {createElement() {return {
  dataset: {}, style: {setProperty(name, value) {this[name] = value;}}, children: [],
  appendChild(child) {this.children.push(child);}, append(...children) {this.children.push(...children);},
  setAttribute(name, value) {this[name] = value;},
};}};
''' + TEXT[start:end] + r'''
const parse = value => value.match(/hsl\((\d+) (\d+)% (\d+)%\)/).slice(1).map(Number);
const low = cacheProgressColors(0), high = cacheProgressColors(1);
assert.equal(parse(low.start)[0],270,'default cache is violet');
let previous = parse(low.start);
for (const ratio of [.1,.25,.5,.75,.9,1]) {
  const color = parse(cacheProgressColors(ratio).start);
  assert(color[1] > previous[1], 'more hits gain saturation');
  assert(color[2] >= previous[2], 'more hits gain light');
  assert.equal(color[0],270,'hit changes stay in one color family');
  previous = color;
}
assert.deepEqual(cacheProgressColors(-1),low);
assert.deepEqual(cacheProgressColors(NaN),low);
assert.deepEqual(cacheProgressColors(Infinity),low);
assert.deepEqual(cacheProgressColors(2),high);
const themes = [
  [{progressDay:'#f3d27a',progressWeek:'#b5dd92'}, [44,92]],
  [{progressDay:'#0088ff',progressWeek:'#00bb77'}, [208,158]],
  [{progressDay:'#8000ff',progressDayEnd:'#9900ff',progressWeek:'#00ff80'}, [270,276,150]],
  [{progressDay:'#8000ff',progressWeek:'#ff0080'}, [270,330]],
];
const distance = (a,b) => Math.min(Math.abs(a-b),360-Math.abs(a-b));
for (const [tokens, otherHues] of themes) {
  for (const ratio of [0,.5,1]) {
    const colors = cacheProgressColors(ratio,tokens);
    for (const hue of otherHues) {
      assert(distance(parse(colors.start)[0],hue) >= 34);
      assert(distance(parse(colors.end)[0],hue) >= 34,'both ends avoid budget colors');
    }
  }
  const session = progressRail({tone:'session',ratio:.8,label:'本会话'},tokens);
  const cache = progressRail({tone:'cache',ratio:.8,label:'缓存命中 80%'},tokens);
  assert.equal(session.style['--codex-usage-hud-cache-fill'],cache.style['--codex-usage-hud-cache-fill']);
  assert.equal(session.children[1].style.width,'80%');
  for (const tone of ['day','week','error']) {
    const rail = progressRail({tone,ratio:.8,label:tone},tokens);
    assert.equal(rail.style['--codex-usage-hud-cache-fill'],undefined,'other tab colors are preserved');
  }
}
'''
    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
