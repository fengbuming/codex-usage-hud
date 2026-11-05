"""Keep provider switching available independently of CLI shortcuts."""

import subprocess

from codex_usage_hud.renderer_assets.settings_shell import TEXT


def test_provider_menu_survives_empty_cli_shortcuts_and_chrome_replacement():
    start = TEXT.index("      function syncCodexCliQuickLaunchProviderLabel(")
    end = TEXT.index("      function installCodexCliQuickLaunchMenu()", start)
    script = r'''
const assert = require('node:assert/strict');
let writes = 0;
class Element {
  constructor(kind = '') {
    this.kind = kind; this.dataset = {}; this.children = [];
    this.parentElement = null;
  }
  setAttribute() { writes++; }
  get previousElementSibling() {
    return this.parentElement?.children[this.parentElement.children.indexOf(this) - 1] || null;
  }
  get nextElementSibling() {
    return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null;
  }
  remove() {
    if (!this.parentElement) return;
    const children = this.parentElement.children;
    children.splice(children.indexOf(this), 1);
    this.parentElement = null; writes++;
  }
  insertBefore(node, anchor) {
    node.remove();
    this.children.splice(anchor ? this.children.indexOf(anchor) : this.children.length, 0, node);
    node.parentElement = this; writes++;
  }
  appendChild(node) { this.insertBefore(node, null); }
  insertAdjacentElement(_, node) { this.parentElement.insertBefore(node, this.nextElementSibling); }
  querySelector(selector) {
    return this.children.find(node => {
      if (selector.includes('cli-provider-label')) return node.dataset.codexUsageHudCliProviderLabel;
      if (selector.includes('cli-menu-toggle')) return node.dataset.codexUsageHudCliMenuToggle;
      if (selector.includes('content-anchor')) return node.kind === 'anchor';
      return node.kind === 'native';
    }) || null;
  }
}
const makeMenu = () => {
  const menu = new Element();
  menu.appendChild(new Element('native'));
  menu.appendChild(new Element('anchor'));
  return menu;
};
let menubar = makeMenu(), providers = [], providerState = {
  provider: 'zzq', mismatch: false, switching: false, text: 'provider zzq', title: 'zzq',
};
const menus = [menubar];
const document = {
  createElement: () => new Element(),
  querySelector: selector => menus.map(menu => menu.querySelector(selector)).find(Boolean) || null,
};
const codexCliQuickLaunchMenuState = {toggle: null, providers: [], open: false};
const codexProviderSwitchMenuState = {toggle: null, open: false};
const codexCliQuickLaunchMenuProviders = () => providers;
const codexDesktopApplicationMenu = () => menubar;
const codexCliQuickLaunchProviderLabelState = () => providerState;
const closeCodexCliQuickLaunchMenu = () => { codexCliQuickLaunchMenuState.open = false; };
const closeCodexProviderSwitchMenu = () => { codexProviderSwitchMenuState.open = false; };
const renderCodexCliQuickLaunchMenu = () => {};
const installGatewayResumeAdapter = () => {};
''' + TEXT[start:end] + r'''
syncCodexCliQuickLaunchMenu();
const label = codexProviderSwitchMenuState.toggle;
assert.ok(label);
assert.equal(label.parentElement, menubar);
assert.equal(codexCliQuickLaunchMenuState.toggle, null);
let before = writes;
syncCodexCliQuickLaunchMenu();
assert.equal(writes, before, 'empty shortcuts must not cause an observer feedback loop');

providers = ['zzq'];
syncCodexCliQuickLaunchMenu();
const toggle = codexCliQuickLaunchMenuState.toggle;
assert.ok(toggle);
assert.equal(label.previousElementSibling, toggle);
before = writes;
syncCodexCliQuickLaunchMenu();
assert.equal(writes, before);

providers = [];
codexProviderSwitchMenuState.open = true;
syncCodexCliQuickLaunchMenu();
assert.equal(toggle.parentElement, null);
assert.equal(codexProviderSwitchMenuState.toggle, label);
assert.equal(codexProviderSwitchMenuState.open, true);

menubar = null;
syncCodexCliQuickLaunchMenu();
assert.equal(label.parentElement, null);
assert.equal(codexProviderSwitchMenuState.toggle, null);
menubar = makeMenu(); menus.push(menubar);
syncCodexCliQuickLaunchMenu();
assert.equal(codexProviderSwitchMenuState.toggle.parentElement, menubar);

// Also support a menu without the native content anchor.
menubar.children.find(node => node.kind === 'anchor').remove();
before = writes;
syncCodexCliQuickLaunchMenu();
assert.equal(writes, before);
providerState = null;
syncCodexCliQuickLaunchMenu();
assert.equal(codexProviderSwitchMenuState.toggle, null);
'''
    result = subprocess.run(
        ["node", "-"], input=script, text=True, encoding="utf-8",
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
