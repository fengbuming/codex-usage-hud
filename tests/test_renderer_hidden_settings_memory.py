"""Exercise the actual close handler without opening the user's desktop."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_closing_settings_releases_body_after_capturing_state_and_keeps_dirty_guard():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    source = (Path(__file__).resolve().parents[1] / 'src/codex_usage_hud/renderer_assets/settings_shell.py').read_text(encoding='utf-8')
    start = source.index('      function closeSettingsModal(')
    end = source.index('      function applySettingsPayload(', start)
    script = r'''
const calls=[];
const modal={hidden:false,querySelector:()=>({replaceChildren:()=>calls.push('release')})};
const document={getElementById:()=>modal};
const settingsModalId='settings';
const settingsActiveTab='backgroundUsage';
const settingsDirtyProviders=new Set(['unsaved']);
const codexProviderDrafts=new Map();
const codexProviderDirty=new Set();
const sessionCleanupState={};
let settingsProviderDraft={};
function captureSettingsProviderForm(){calls.push('captureForm')}
function openSettingsDiscardConfirm(){calls.push('dirtyGuard')}
function captureBackgroundUsageScrollPositions(){calls.push('captureScroll')}
function clearBackgroundUsageRequestTimeout(){}
function closeCodexCliDialog(){}
function closeSessionTransferDialog(){}
function closeSettingsConfirm(){}
function clearSettingsRestartPrompt(){}
function writeSettingsUiState(){}
function ensureRestReminderCountdownTicker(){}
function stopSessionCleanupElapsedTicker(){}
function stopSessionCleanupSearchTimer(){}
function detachSessionIndexUi(){}
function resetSessionCleanupPendingRequests(){}
function syncCodexCliQuickLaunchMenu(){}
''' + source[start:end] + r'''
closeSettingsModal();
if(modal.hidden || calls.includes('release')) throw Error('dirty form was discarded');
settingsDirtyProviders.clear();
closeSettingsModal();
if(!modal.hidden || !calls.includes('release')) throw Error('hidden DOM was retained');
if(calls.indexOf('captureScroll')>calls.indexOf('release')) throw Error('scroll captured too late');
console.log(JSON.stringify({ok:true}));
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(result.stdout) == {'ok': True}
