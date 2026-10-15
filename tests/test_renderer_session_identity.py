"""Exercise persisted sidebar identities whose DOM key is still provisional."""

import subprocess

from codex_usage_hud.renderer_assets.active_session import TEXT as ACTIVE_SESSION


def test_provisional_sidebar_row_uses_its_committed_conversation_identity() -> None:
    factory = ACTIVE_SESSION.split("  const activeSessionDomain = ctx.domains.register(", 1)[0]
    script = r"""
const assert = require('node:assert/strict');
const window = {};
function normalize(value) { return String(value || '').trim(); }
""" + factory + r"""
const domain = createActiveSessionDomain({}, {});
const id = '01a13d77-c017-70d0-be2f-3eb52994aef6';
const other = '01a13e15-8efa-7940-9e44-6f76144de771';
const raw = 'local:client-new-thread:0f2b5fb9-a076-45ae-a445-689d47985f2a';
function row(key = raw, fiber = null) {
  return {
    __reactFiber$test: fiber,
    matches() { return true; },
    getAttribute(name) {
      return ({'data-app-action-sidebar-thread-id': key,
        'data-app-action-sidebar-thread-host-id': 'local'})[name] || '';
    },
    querySelector() { return {textContent: 'Same title as another session'}; },
  };
}
function owner(conversationId, hostId = 'local', parent = null) {
  return {memoizedProps: {conversationId, hostId}, return: parent};
}
let ref = domain.activeSessionRefFromRow(row(raw, {return: owner(id)}));
assert.equal(ref.sessionId, id);
assert.equal(ref.rendererSessionId, `local:${id}`);
assert.equal(ref.rawSessionId, raw);
assert(!ref.pendingSession);

// After React switches trees, the DOM's fiber can still point to the old
// owner. Read its committed alternate, including when it has no id yet.
const oldRoot = {stateNode: {current: {}}};
const stale = owner(other, 'local', oldRoot);
stale.alternate = owner(id);
assert.equal(domain.activeSessionRefFromRow(row(raw, {return: stale})).sessionId, id);
stale.alternate = owner(null);
assert(domain.activeSessionRefFromRow(row(raw, {return: stale})).pendingSession);
delete stale.alternate;
assert(domain.activeSessionRefFromRow(row(raw, {return: stale})).pendingSession);

// A new thread, missing React data, malformed id, or different host must
// stay pending. Do not borrow an outer conversation with the same title.
for (const fiber of [null, owner(null, 'local', owner(id)),
  owner('client-new-thread:placeholder', 'local', owner(id)),
  owner(id, 'remote', owner(other))]) {
  ref = domain.activeSessionRefFromRow(row(raw, fiber));
  assert.equal(ref.sessionId, '');
  assert.equal(ref.rendererSessionId, raw);
  assert.equal(ref.pendingSession, true);
}

// A canonical DOM identity remains authoritative even with stale props.
ref = domain.activeSessionRefFromRow(row(`local:${id}`, owner(other)));
assert.equal(ref.sessionId, id);
assert(!ref.pendingSession);
"""
    result = subprocess.run(
        ["node", "--input-type=commonjs"], input=script,
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
