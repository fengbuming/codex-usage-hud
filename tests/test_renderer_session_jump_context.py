"""Execute jump receipts with and without a preceding content search."""

import subprocess

from codex_usage_hud.renderer_assets.session_view import TEXT


def test_jump_float_keeps_navigation_context_and_native_keyword_search() -> None:
    prepare = TEXT[TEXT.index("    function prepareSearchJump("):TEXT.index("    function fillThreadFindInput(")]
    apply = TEXT[TEXT.index("    async function applySearchJump("):TEXT.index("    let installed = false;")]
    classification = TEXT[TEXT.index("    function searchEntryHasBodyMatch("):TEXT.index("    // 命中徽标")]
    script = r'''
const assert = require('node:assert/strict');
let searchFloat = null, pendingSearchJump = '', pendingSearchContext = null;
let threadFindJump = null, renders = 0, nativeQueries = [];
const ctx = {lifecycle: {timeout() { throw Error('unexpected retry'); }}};
let composerId = '';
const document = {getElementById() { return {}; }, querySelectorAll() {
  return composerId ? [{closest() { return null; }, getAttribute() { return composerId; }}] : [];
}};
let activeId = 'target';
function cancelThreadFindJump() { threadFindJump = null; }
function renderSearchFloat() { renders++; }
function bindSearchFloat() {}
function readActiveSessionRef() { return {sessionId: activeId}; }
async function expandSearchFileDisclosures() {}
function locateRenderedSearchTarget() { return false; }
function openThreadFind(query) { nativeQueries.push(query); }
function searchKindLabel(kind) { return kind; }
''' + classification + prepare + apply + r'''
(async () => {
  const rows = [{id: 'a', title: 'First'}, {id: 'b', title: 'Second'}];
  prepareSearchJump('a', {query: '', revision: 'r1', matches: rows});
  await applySearchJump({itemId: 'a', verified: true});
  assert.equal(renders, 1);
  assert.deepEqual(searchFloat.matches, rows);
  assert.equal(searchFloat.currentId, 'a');
  assert.equal(searchFloat.query, '');
  assert.equal(searchFloat.onlyExact, false);
  assert.equal(pendingSearchJump, '');
  assert.deepEqual(nativeQueries, []);

  // A second click in the float keeps the original list and its presentation.
  searchFloat.collapsed = true;
  searchFloat.side = 'side-left';
  prepareSearchJump('b');
  await applySearchJump({itemId: 'b', verified: true});
  assert.equal(searchFloat.currentId, 'b');
  assert.deepEqual(searchFloat.matches, rows);
  assert.equal(searchFloat.collapsed, true);
  assert.equal(searchFloat.side, 'side-left');
  assert.deepEqual(nativeQueries, []);

  // A failed navigation cannot claim that the new session is current.
  prepareSearchJump('a');
  await applySearchJump({itemId: 'a', verified: false});
  assert.equal(searchFloat.currentId, 'b');
  assert.equal(pendingSearchJump, '');

  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('target'));
  const targetKey = Array.from(new Uint8Array(digest), v => v.toString(16).padStart(2, '0')).join('');
  prepareSearchJump('a', {query: 'KB2468871', revision: 'r1', matches: rows});
  await applySearchJump({itemId: 'a', verified: true, search: {
    query: 'KB2468871', locateQuery: 'KB2468871', targetKey,
    revision: 'r1', matches: [{id: 'a', title: 'Content hit', kinds: ['assistant']}],
  }});
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(searchFloat.matches[0].title, 'Content hit');
  assert.equal(searchFloat.query, 'KB2468871');
  assert.deepEqual(nativeQueries, ['KB2468871']);

  // Archived conversations have an exact composer identity but no active row.
  composerId = '10000000-0000-4000-8000-000000000abc';
  activeId = 'old-sidebar-session';
  const composerDigest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(composerId));
  const composerKey = Array.from(new Uint8Array(composerDigest), v => v.toString(16).padStart(2, '0')).join('');
  prepareSearchJump('b');
  await applySearchJump({itemId: 'b', verified: true, search: {
    query: 'KB2468871', targetKey: composerKey, revision: 'r1',
    matches: rows.map(row => ({...row, kinds: ['assistant']})),
  }});
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(searchFloat.currentId, 'b');
  assert.deepEqual(nativeQueries, ['KB2468871', 'KB2468871']);

  for (const kinds of [['metadata'], ['identity'], ['tool'], ['file']]) {
    prepareSearchJump('a');
    await applySearchJump({itemId: 'a', verified: true, search: {
      query: 'KB2468871', targetKey: composerKey, revision: 'r1',
      matches: [{id: 'a', kinds, exactPhrase: true}],
    }});
    assert.equal(searchFloat.currentId, 'a');
    assert.ok(searchFloat.notice.includes('没有正文命中依据'));
    assert.deepEqual(nativeQueries, ['KB2468871', 'KB2468871']);
  }

  // A token chip is an explicit request even for a metadata-only hit.
  searchFloat.pendingToken = 'manual-term';
  prepareSearchJump('a');
  await applySearchJump({itemId: 'a', verified: true, search: {
    query: 'KB2468871', targetKey: composerKey, revision: 'r1',
    matches: [{id: 'a', kinds: ['metadata']}],
  }});
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(nativeQueries.at(-1), 'manual-term');

  prepareSearchJump('a');
  await applySearchJump({itemId: 'a', verified: true, search: {
    query: 'KB2468871', error: 'inventory-changed',
  }});
  assert.equal(searchFloat.currentId, 'a');
  assert.ok(searchFloat.notice.includes('检索详情未能加载'));
  assert.ok(!searchFloat.notice.includes('未能确认跳转'));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(
        ["node", "--input-type=commonjs"], input=script,
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
