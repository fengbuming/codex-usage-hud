"""Probe live Codex DOM z-index landscape around #content-search-input.

Run from the repository root:

    python tools/probe_zindex_landscape.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms.cdp_probe import (  # noqa: E402
    CodexCdpProbe,
    evaluate_script,
    list_targets,
    pick_page_target,
)

OPEN_FIND_BAR = "window.postMessage({ type: \"find-in-thread\" }, \"*\");"

ANCESTOR_CHAIN = r"""
(() => {
  const input = document.getElementById("content-search-input");
  if (!input) return { error: "no #content-search-input" };
  const chain = [];
  let node = input;
  while (node && node !== document.documentElement) {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    chain.push({
      tag: node.tagName.toLowerCase(),
      id: node.id || "",
      cls: String(node.className || "").slice(0, 90),
      position: style.position,
      zIndex: style.zIndex,
      opacity: style.opacity,
      rect: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)],
    });
    node = node.parentElement;
  }
  return { chain };
})()
"""

ZINDEX_SURVEY = r"""
(() => {
  const counts = {};
  const samples = {};
  for (const node of document.querySelectorAll("*")) {
    const style = window.getComputedStyle(node);
    const z = style.zIndex;
    if (style.position === "static" || !z || z === "auto") continue;
    const zNum = Number(z);
    if (!Number.isFinite(zNum) || zNum < 1) continue;
    counts[z] = (counts[z] || 0) + 1;
    if (!samples[z]) samples[z] = [];
    if (samples[z].length < 3) {
      samples[z].push(
        node.tagName.toLowerCase()
        + (node.id ? "#" + node.id : "")
        + (String(node.className || "").trim() ? "." + String(node.className).trim().split(/\s+/).slice(0, 3).join(".") : "")
      );
    }
  }
  const hud = document.getElementById("codex-usage-hud-root");
  const hudStyle = hud ? window.getComputedStyle(hud) : null;
  return {
    counts,
    samples,
    hudRoot: hud ? { position: hudStyle.position, zIndex: hudStyle.zIndex } : null,
  };
})()
"""


def main() -> int:
    probe = CodexCdpProbe()
    targets = list_targets(probe.port, 2.0)
    target = pick_page_target(targets)
    if not target:
        print("ERROR: no Codex CDP page target (is Codex running with remote debugging?)")
        return 1
    print(f"target: {target.get('url', '')[:100]}")

    # 1) open the native find-in-thread bar
    evaluate_script(target["webSocketDebuggerUrl"], OPEN_FIND_BAR, 2.0)
    time.sleep(0.8)

    # 2) ancestor chain of the search input
    chain_result = evaluate_script(target["webSocketDebuggerUrl"], chain_script := ANCESTOR_CHAIN, 2.0)
    print("== search bar ancestor chain ==")
    print(json.dumps(chain_result.get("result", {}).get("result", {}).get("value", chain_result), indent=2, ensure_ascii=False))

    # 3) whole-page z-index survey
    survey_result = evaluate_script(target["webSocketDebuggerUrl"], ZINDEX_SURVEY, 4.0)
    value = survey_result.get("result", {}).get("result", {}).get("value", {})
    print("== z-index survey (count -> samples) ==")
    for z in sorted(value.get("counts", {}), key=lambda k: -int(k)):
        print(f"  z={z}  x{value['counts'][z]}  {value['samples'][z]}")
    print("hudRoot:", value.get("hudRoot"))

    # 4) close the find bar (toggle again)
    evaluate_script(target["webSocketDebuggerUrl"], OPEN_FIND_BAR, 2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
