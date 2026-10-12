"""Exercise navigation through a sidebar with a parent drag sensor."""

import subprocess

from codex_usage_hud.platforms.cdp_probe import session_switch_script


def test_session_navigation_does_not_start_parent_drag_sensor(tmp_path):
    script = r'''
const assert = require('node:assert/strict');
let activeId = 'source', clicks = 0, dragStarted = false;
class HTMLElement {
  constructor(id, title) {this.id = id; this.title = title; this.isConnected = true;}
  getAttribute(key) {
    if (key === 'data-app-action-sidebar-thread-id') return 'local:' + this.id;
    if (key === 'data-app-action-sidebar-thread-active') return String(this.id === activeId);
    return '';
  }
  querySelector(selector) {
    if (selector.includes('data-thread-title')) return {
      textContent: this.title, closest: () => this,
    };
    return null;
  }
  closest() {return null;}
  getBoundingClientRect() {return {width:200, height:30};}
  scrollIntoView() {}
  dispatchEvent(event) {
    if (event.type === 'pointerdown') dragStarted = true;
    if (event.type === 'pointerup') dragStarted = false;
    return true;
  }
  click() {clicks++; if (!dragStarted) activeId = this.id;}
}
class MouseEvent {constructor(type) {this.type = type;}}
const rows = [new HTMLElement('source', 'Source'), new HTMLElement('target', 'Target')];
const document = {querySelectorAll(selector) {
  return selector === '[data-app-action-sidebar-thread-id]' ? rows : [];
}};
const window = {};
const location = {pathname:'/index.html',search:'',hash:'',href:'app://-/index.html'};
function getComputedStyle() {return {display:'block',visibility:'visible'};}
// A failed switch must finish quickly even when regressed to the old gesture.
function setTimeout(fn) {queueMicrotask(fn);}
const result = ''' + session_switch_script("target", "Target") + r''';
result.then(report => {
  assert.equal(report.ok, true);
  assert.equal(activeId, 'target');
  assert.equal(clicks, 1);
  assert.equal(dragStarted, false, 'navigation must never arm the drag sensor');
}).catch(e => {console.error(e); process.exitCode = 1;});
'''
    path = tmp_path / "session_click.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        ["node", str(path)], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stdout + result.stderr
