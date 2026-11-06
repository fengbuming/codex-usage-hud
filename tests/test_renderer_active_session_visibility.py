"""Retained hidden sidebars must not supply the current conversation/provider."""

import subprocess

from codex_usage_hud.renderer_assets.active_session import TEXT as ACTIVE
from codex_usage_hud.renderer_assets.fragment_01 import SHARED_HEAD


def test_hidden_selected_sidebar_cannot_override_current_session_provider():
    visible = SHARED_HEAD[SHARED_HEAD.index("  const visible ="):
                          SHARED_HEAD.index("  const cssEscape =")]
    rows = ACTIVE[ACTIVE.index("      function activeSessionRows()"):
                  ACTIVE.index("      function activeSessionHomeLooksNewSession()")]
    read = ACTIVE[ACTIVE.index("      function readActiveSessionRef()"):
                  ACTIVE.index("      function activeSessionContainer()")]
    container = ACTIVE[ACTIVE.index("      function activeSessionContainer()"):
                       ACTIVE.index("      function postActiveSession(")]
    program = r'''
const assert = require('node:assert/strict');
class Element {
  constructor(id, list, options = {}) {
    Object.assign(this, {id, list, isConnected:true, width:212, height:30,
                         display:'block', visibility:'visible', selected:true}, options);
  }
  getBoundingClientRect() { return {width:this.width, height:this.height}; }
  closest(selector) { return selector === "[role='list']" ? this.list : null; }
}
const getComputedStyle = node => ({display:node.display, visibility:node.visibility, opacity:'1'});
const hiddenList = {querySelectorAll:()=>[stale]};
const currentList = {querySelectorAll:()=>[stale, current]};
const stale = new Element('weekly', hiddenList, {width:0, height:0});
const current = new Element('image', currentList);
const all = [stale, current];
const document = {querySelectorAll:()=>all, querySelector:()=>null};
const activeSessionIdentitySelector = 'identity';
const activeSessionRowSelector = 'row';
const activeSessionTitleSelector = 'title';
const activeSessionFirstOutsideHud = ()=>null;
const activeSessionNodeOwnedByHud = ()=>false;
const activeSessionRowSelected = node=>node.selected;
const activeSessionRowLooksThread = ()=>true;
const activeSessionRefFromRow = row=>({sessionId:row.id, title:row.id});
const activeSessionRowMatchesLocation = ()=>false;
const activeSessionHomeLooksNewSession = ()=>false;
const activeSessionHeaderTitleText = ()=>'image';
const activeSessionHeaderLooksNewSession = ()=>false;
const activeSessionTitleIsNewSession = ()=>false;
const activeSessionLocationId = ()=>'';
const activeSessionComposerDraftText = ()=>'';
const cleanActiveSessionTitle = value=>value;
const location = {href:'app://-/index.html'};
''' + visible + rows + read + container + r'''
const providers = {weekly:'company', image:'aiapi'};
// The stale selected row precedes the visible selected row, exactly as in
// retained desktop sidebars. Exercise both list selection and row discovery.
for (const hidden of [
  {width:0, height:0},
  {display:'none'},
  {visibility:'hidden'},
  {isConnected:false},
]) {
  Object.assign(stale, {width:212,height:30,display:'block',visibility:'visible',isConnected:true}, hidden);
  assert.equal(activeSessionContainer(), currentList);
  assert.deepEqual(activeSessionRows(), [current]);
  assert.equal(providers[readActiveSessionRef().sessionId], 'aiapi');
}
// Scrolled rows still have geometry and may own a selected pinned/recent list.
stale.isConnected = true;
stale.selected = false;
current.getBoundingClientRect = ()=>({width:212,height:30,top:-500,bottom:-470});
assert.equal(activeSessionContainer(), currentList);
assert.equal(readActiveSessionRef().sessionId, 'image');
// With all sidebar trees hidden, use the visible header instead of claiming
// the stale session and therefore its supplier.
stale.width = current.width = 0;
stale.height = current.height = 0;
current.getBoundingClientRect = ()=>({width:0,height:0});
assert.deepEqual(activeSessionRows(), []);
const ref = readActiveSessionRef();
assert.equal(ref.sessionId, '');
assert.equal(ref.title, 'image');
assert.equal(ref.matchedBy, 'header-title');
'''
    result = subprocess.run(
        ["node", "-"], input=program, text=True, encoding="utf-8",
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
