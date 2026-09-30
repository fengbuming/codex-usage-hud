"""Chrome DevTools Protocol helpers for the Codex renderer DOM.

The probe intentionally uses only the Python standard library.  It talks to a
local Codex remote-debugging port when one is already available and otherwise
returns ``None`` quickly so callers can fall back to native window tracking.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import ipaddress
import json
import os
import socket
import struct
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import ProxyHandler, Request, build_opener


DEFAULT_CDP_PORT = 9229
DEFAULT_CDP_CACHE_SECONDS = 0.20
DEFAULT_CDP_FAILURE_COOLDOWN_SECONDS = 2.0
DEFAULT_CDP_TIMEOUT_SECONDS = 0.45
DEFAULT_CDP_SWITCH_TIMEOUT_SECONDS = 1.8
CDP_PORT_ENV = "CODEX_USAGE_HUD_CDP_PORT"
CDP_DOM_ENV = "CODEX_USAGE_HUD_CDP_DOM"


@dataclass(frozen=True)
class CdpRect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)


@dataclass(frozen=True)
class CdpDomSnapshot:
    session_id: str
    title: str
    device_pixel_ratio: float
    header_rect: CdpRect | None = None
    title_rect: CdpRect | None = None
    top_slot_rect: CdpRect | None = None
    composer_rect: CdpRect | None = None
    app_error: str = ""


@dataclass(frozen=True)
class CdpSessionSwitchResult:
    ok: bool
    status: str
    requested_session_id: str = ""
    requested_title: str = ""
    active_session_id: str = ""
    active_title: str = ""
    matched_by: str = ""
    available_count: int = 0
    message: str = ""


DOM_PROBE_SCRIPT = r"""
(() => {
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const normalizeThreadId = (value) => {
    const text = normalize(value);
    const match = text.match(/^(?:[a-z0-9_.-]+:)(.+)$/i);
    return match ? normalize(match[1]) : text;
  };
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const hudRootSelector = "#codex-usage-hud-root";
  const visible = (node) => {
    if (!(node instanceof HTMLElement) || !node.isConnected) return false;
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const rectFor = (node) => {
    if (!visible(node)) return null;
    const rect = node.getBoundingClientRect();
    return {
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
    };
  };
  const rowHref = (row) => row?.getAttribute?.("href") || row?.querySelector?.("a")?.getAttribute?.("href") || "";
  const locationThreadId = () => {
    const source = `${location.pathname}${location.search}${location.hash}`;
    const match = source.match(/(?:session|conversation|thread)(?:\/|=|:|-)([A-Za-z0-9_.-]+)/i)
      || source.match(/\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:[/?#]|$)/)
      || source.match(/\/([A-Za-z0-9_-]{24,})(?:[/?#]|$)/);
    return match ? normalizeThreadId(decodeURIComponent(match[1])) : "";
  };
  const threadRows = Array.from(document.querySelectorAll("[data-app-action-sidebar-thread-id]"));
  const refFromRow = (row) => {
    const href = rowHref(row);
    const idMatch = href.match(/(?:session|conversation|thread)[=/:-]([A-Za-z0-9_.-]+)/i) || href.match(/([A-Za-z0-9_-]{8,})$/);
    const rawSessionId = row.getAttribute("data-app-action-sidebar-thread-id")
      || (idMatch && idMatch[1])
      || row.getAttribute("data-session-id")
      || row.getAttribute("data-testid")
      || "";
    const sessionId = normalizeThreadId(rawSessionId);
    const titleNode = row.querySelector("[data-thread-title], .truncate.select-none, .truncate.text-base");
    const rawTitle = titleNode?.textContent || (titleNode ? "" : (row.textContent || ""));
    const title = normalize(titleNode ? rawTitle : rawTitle.replace(/\s*(Export|Delete|Move|Remove from project|导出|删除|移动|移出项目)+$/g, "")).slice(0, 160);
    return { rawSessionId, sessionId, title };
  };
  const rowMatchesLocation = (row) => {
    const href = rowHref(row);
    if (href) {
      try {
        const url = new URL(href, location.href);
        if (url.href === location.href) return true;
      } catch (_) {
        if (location.href.includes(href)) return true;
      }
    }
    const ref = refFromRow(row);
    return (
      (!!ref.rawSessionId && location.href.includes(ref.rawSessionId))
      || (!!ref.sessionId && location.href.includes(ref.sessionId))
    );
  };
  const rowSelectedByState = (row) => {
    if (row.getAttribute("data-app-action-sidebar-thread-active") === "true") return true;
    if (row.getAttribute("aria-current") === "page" || row.getAttribute("aria-current") === "true") return true;
    if (row.getAttribute("aria-selected") === "true") return true;
    if (row.getAttribute("data-active") === "true" || row.getAttribute("data-selected") === "true") return true;
    if (row.matches?.("[data-state='active'], [data-state='selected'], .active, .selected")) return true;
    return false;
  };
  const activeRow = threadRows.find(rowSelectedByState) || threadRows.find(rowMatchesLocation) || null;
  const activeRef = activeRow ? refFromRow(activeRow) : { sessionId: locationThreadId(), title: "" };
  const compact = (value, limit = 220) => {
    const text = normalize(value);
    return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 3))}...`;
  };
  const appErrorText = () => {
    const errorSelectors = [
      "[role='alert']",
      "[role='status']",
      "[aria-live]",
      "[data-testid*='toast' i]",
      "[data-testid*='notification' i]",
      "[data-testid*='error' i]",
      "[class*='toast' i]",
      "[class*='notification' i]",
      "[class*='error' i]",
      "[class*='danger' i]",
      "[class*='destructive' i]",
      "[class*='alert' i]",
    ];
    const selectors = errorSelectors.join(", ");
    const errorPattern = /(exceeded retry limit|too many requests|\b429\b|\brate limit(?:ed)?\b|\b5\d\d\b|network error|request failed|failed to fetch|server error|internal server error|service unavailable|temporarily unavailable|something went wrong|unexpected error|error occurred|请求失败|网络错误|服务不可用|重试上限|操作超时)/i;
    const readableText = (node) => {
      if (!node) return "";
      const label = normalize([
        node.getAttribute?.("aria-label"),
        node.getAttribute?.("title"),
      ].filter(Boolean).join(" "));
      for (const selector of [".wrap-anywhere", ".text-pretty"]) {
        const text = normalize(Array.from(node.querySelectorAll?.(selector) || [])
          .filter(visible)
          .map((child) => child.textContent || "")
          .filter(Boolean)
          .join(" "));
        if (text) return normalize([label, text].filter(Boolean).join(" "));
      }
      return normalize([
        label,
        node.textContent,
      ].filter(Boolean).join(" "));
    };
    const boundedText = (node, limit = 520) => {
      const text = readableText(node);
      return text && text.length <= limit ? text : "";
    };
    const usableContainer = (node) => (
      visible(node) && !node.closest("#codex-usage-hud-root")
    );
    const hasErrorClass = (node) => (
      /toast|notification|snackbar|alert|error|danger|destructive/i.test(
        String(node?.className || "")
      )
    );
    const hasErrorTestId = (node) => (
      /toast|notification|error/i.test(
        String(node?.getAttribute?.("data-testid") || "")
      )
    );
    const hasErrorIcon = (node) => (
      !!node?.matches?.("[class*='text-token-error-foreground']")
      || !!node?.querySelector?.("[class*='text-token-error-foreground']")
    );
    const hasStructuredErrorText = (node) => (
      !!node?.querySelector?.(".wrap-anywhere, .text-pretty")
    );
    const errorSemantic = (node) => {
      if (!(node instanceof Element)) return false;
      const role = String(node.getAttribute("role") || "");
      return role === "alert"
        || hasErrorClass(node)
        || hasErrorTestId(node)
        || hasErrorIcon(node);
    };
    const errorBannerLike = (node) => {
      if (!(node instanceof Element)) return false;
      return errorSemantic(node) && (
        hasStructuredErrorText(node)
        || node.matches?.("[role='alert']")
      );
    };
    const readableContainerFor = (node, allowAncestorBanner) => {
      if (!(node instanceof Element)) return null;
      const aside = node.closest("aside");
      if (
        allowAncestorBanner
        && aside
        && usableContainer(aside)
        && boundedText(aside)
        && errorBannerLike(aside)
      ) {
        return aside;
      }
      const explicit = node.closest(selectors);
      if (
        explicit
        && usableContainer(explicit)
        && boundedText(explicit)
        && errorSemantic(explicit)
      ) {
        return explicit;
      }
      if (allowAncestorBanner) {
        let current = node.parentElement;
        for (let depth = 0; current instanceof Element && depth < 8; depth += 1, current = current.parentElement) {
          if (
            usableContainer(current)
            && boundedText(current)
            && errorBannerLike(current)
          ) {
            return current;
          }
        }
      }
      return usableContainer(node) && boundedText(node) && errorSemantic(node) ? node : null;
    };
    const candidateFor = (node, index, baseScore, requirePattern, allowAncestorBanner) => {
      const container = readableContainerFor(node, allowAncestorBanner);
      if (!container) return null;
      const text = readableText(container);
      const matchesErrorText = errorPattern.test(text);
      const strongMarker = errorSemantic(container);
      if (
        !text
        || (requirePattern && !matchesErrorText)
        || (!requirePattern && !matchesErrorText && !strongMarker)
      ) {
        return null;
      }
      const rect = container.getBoundingClientRect();
      const className = String(container.className || "");
      const role = String(container.getAttribute("role") || "");
      let score = baseScore + 1000 - index;
      if (role === "alert") score += 160;
      if (role === "status") score += 90;
      if (container.hasAttribute("aria-live")) score += 90;
      if (/toast|notification|snackbar|alert/i.test(className)) score += 80;
      if (/error|danger|destructive/i.test(className)) score += 70;
      if (hasErrorIcon(container)) score += 140;
      if (container.tagName === "ASIDE") score += 90;
      if (node !== container) score += 80;
      if (rect.top <= 160 || rect.bottom >= innerHeight - 220) score += 30;
      if (text.length <= 180) score += 20;
      if (matchesErrorText) score += 100;
      if (/\b429\b|too many requests|exceeded retry limit|rate limit/i.test(text)) score += 180;
      return { text: compact(text), score };
    };
    const iconCandidates = Array.from(
      document.querySelectorAll("[class*='text-token-error-foreground']")
    ).map((node, index) => candidateFor(node, index, 420, false, true));
    const selectorCandidates = Array.from(document.querySelectorAll(selectors))
      .map((node, index) => candidateFor(node, index, 0, true, false));
    const candidates = iconCandidates.concat(selectorCandidates)
      .filter(Boolean)
      .sort((left, right) => right.score - left.score);
    return candidates[0]?.text || "";
  };

  const scoreHeader = (node) => {
    const rect = node.getBoundingClientRect();
    const text = normalize(node.textContent);
    let score = 0;
    if (node.tagName === "HEADER") score += 80;
    if (node.classList.contains("app-header-tint")) score += 35;
    if (node.matches?.("[data-testid='app-shell-header-context-menu-surface']")) score += 140;
    if (node.closest?.("header.app-header-tint")) score += 120;
    if (String(node.className || "").includes("top-toolbar-sm")) score += 110;
    if (rect.top > 20) score += 95;
    if (rect.top <= 4) score -= 140;
    if (rect.width > 300) score += 25;
    if (rect.height >= 34 && rect.height <= 80) score += 30;
    if (/File\s*Edit\s*View\s*Window\s*Help/i.test(text) || text === "FileEditViewWindowHelp") score -= 300;
    if (text && !/File\s*Edit\s*View\s*Window\s*Help/i.test(text)) score += Math.min(20, text.length);
    return score;
  };
  const shellSurface = document.querySelector('[data-testid="app-shell-header-context-menu-surface"]');
  const shellHeader = shellSurface?.closest?.("header.app-header-tint, header, .app-header-tint");
  const header = visible(shellHeader) ? shellHeader : Array.from(document.querySelectorAll([
    "header.app-header-tint",
    "[data-testid='app-shell-header']",
    "[data-testid='app-shell-header-context-menu-surface']",
    ".app-header-tint",
  ].join(", "))).filter(visible)
    .map((node, index) => ({ node, index, score: scoreHeader(node) }))
    .sort((left, right) => (right.score - left.score) || (left.index - right.index))[0]?.node || null;
  const headerRect = rectFor(header);
  const titleText = normalize(activeRef.title);
  const titleScope = header || document;
  const titleCandidates = Array.from(titleScope.querySelectorAll([
    ".app-header-tint [data-thread-title]",
    ".app-header-tint h1",
    ".app-header-tint h2",
    "[data-testid='app-shell-header'] [data-thread-title]",
    "[data-testid='app-shell-header'] h1",
    "[data-testid='app-shell-header'] h2",
    "[data-testid='app-shell-header-context-menu-surface'] [data-thread-title]",
    "[data-testid='app-shell-header-context-menu-surface'] h1",
    "[data-testid='app-shell-header-context-menu-surface'] h2",
    "[data-thread-title]",
    "h1",
    "h2",
  ].join(", "))).filter(visible);
  const title = titleCandidates.find((node) => {
    const text = normalize(node.textContent);
    return titleText ? (text === titleText || titleText.startsWith(text) || text.startsWith(titleText)) : text.length >= 3;
  }) || titleCandidates[0] || null;
  const titleRect = rectFor(title);
  const headerControlButtons = (headerNode, header) => {
    if (!headerNode || !header) return [];
    return Array.from(headerNode.querySelectorAll("button, [role='button'], a"))
      .filter((node) => visible(node) && !node.closest(hudRootSelector))
      .map((node, index) => ({ node, index, rect: node.getBoundingClientRect(), label: normalize([
        node.getAttribute("aria-label"),
        node.getAttribute("title"),
        node.textContent,
      ].filter(Boolean).join(" ")) }))
      .filter((item) => (
        item.rect.width > 0
        && item.rect.height > 0
        && item.rect.left >= header.left - 2
        && item.rect.right <= header.right + 2
        && item.rect.top >= header.top - 2
        && item.rect.bottom <= header.bottom + 2
      ))
      .sort((left, right) => (left.rect.left - right.rect.left) || (left.index - right.index));
  };
  const headerLeftControlEdge = (headerNode, header, controls = headerControlButtons(headerNode, header)) => {
    if (!headerNode || !header) return 0;
    const leftControls = controls
      .map((item) => item.rect)
      .filter((rect) => rect.left < header.left + (header.width * .55));
    if (!leftControls.length) return 0;
    return Math.max(...leftControls.map((rect) => rect.right - header.left)) + 14;
  };
  const headerTitleTextEdge = (headerNode, header, matchedTitleRect) => {
    if (!headerNode || !header) return 0;
    if (
      matchedTitleRect
      && matchedTitleRect.left >= header.left - 2
      && matchedTitleRect.right <= header.right + 2
      && matchedTitleRect.top >= header.top - 2
      && matchedTitleRect.bottom <= header.bottom + 2
    ) {
      return Math.max(0, matchedTitleRect.right - header.left) + 14;
    }
    const maxTextWidth = Math.min(520, header.width * .55);
    const textRects = Array.from(headerNode.querySelectorAll("span, h1, h2, [data-thread-title]"))
      .filter((node) => visible(node) && !node.closest(hudRootSelector))
      .filter((node) => normalize(node.textContent).length > 0)
      .map((node) => node.getBoundingClientRect())
      .filter((rect) => (
        rect.width > 0
        && rect.height > 0
        && rect.left >= header.left - 2
        && rect.right <= header.right + 2
        && rect.top >= header.top - 2
        && rect.bottom <= header.bottom + 2
        && rect.width <= maxTextWidth
        && rect.left < header.left + (header.width * .68)
      ));
    if (!textRects.length) return 0;
    return Math.max(...textRects.map((rect) => rect.right - header.left)) + 14;
  };
  const headerRightControlStart = (headerNode, header, controls = headerControlButtons(headerNode, header)) => {
    if (!headerNode || !header) return 0;
    const rightControls = controls
      .map((item) => item.rect)
      .filter((rect) => rect.right > header.right - Math.min(260, Math.max(160, header.width * .24)));
    if (!rightControls.length) return header.right;
    return Math.min(...rightControls.map((rect) => rect.left));
  };
  const topTitlebarSlot = (headerNode, header, matchedTitleRect) => {
    if (!headerNode || !header) return null;
    const controls = headerControlButtons(headerNode, header);
    const chatActions = controls.find((item) => /chat actions/i.test(item.label));
    const openIn = controls.find((item) => /^open in\b/i.test(item.label));
    const titleEdge = headerTitleTextEdge(headerNode, header, matchedTitleRect);
    const leftControlEdge = headerLeftControlEdge(headerNode, header, controls);
    const fallbackLeft = Math.max(160, Math.min(header.width * .14, 240));
    const left = clamp(
      (chatActions ? chatActions.rect.right + 10 : header.left + Math.max(fallbackLeft, titleEdge, leftControlEdge)),
      header.left + 8,
      header.right - 8
    );
    const rightMargin = Math.max(12, header.width * .04);
    const right = clamp(
      (openIn ? openIn.rect.left - 10 : Math.min(header.right - rightMargin, headerRightControlStart(headerNode, header, controls) - 10)),
      left,
      header.right - 8
    );
    if (right <= left) return null;
    return {
      left,
      top: header.top,
      right,
      bottom: header.bottom,
      width: right - left,
      height: header.height,
    };
  };
  const topSlotRect = topTitlebarSlot(header, headerRect, titleRect);
  const resolvedTitle = titleText || normalize(title?.textContent || "").slice(0, 160);

  const composerClasses = [
    "relative",
    "z-10",
    "flex",
    "flex-col",
    "mx-auto",
    "w-full",
    "max-w-(--thread-content-max-width)",
    "px-toolbar",
  ];
  const hasAllClasses = (node, classes) => {
    const set = new Set(String(node?.className || "").split(/\s+/).filter(Boolean));
    return classes.every((name) => set.has(name));
  };
  const candidates = new Set();
  Array.from(document.querySelectorAll("div")).forEach((node) => {
    if (hasAllClasses(node, composerClasses) && visible(node)) candidates.add(node);
  });
  Array.from(document.querySelectorAll(".composer-footer")).filter(visible).forEach((footer) => {
    candidates.add(footer);
    let node = footer.parentElement;
    for (let depth = 0; node instanceof HTMLElement && depth < 6; depth += 1, node = node.parentElement) {
      if (visible(node)) candidates.add(node);
    }
  });
  Array.from(document.querySelectorAll("textarea, [contenteditable='true']")).filter(visible).forEach((input) => {
    let node = input.parentElement;
    for (let depth = 0; node instanceof HTMLElement && depth < 6; depth += 1, node = node.parentElement) {
      if (visible(node)) candidates.add(node);
    }
  });
  const scoreComposer = (node) => {
    const rect = node.getBoundingClientRect();
    let score = 0;
    if (rect.bottom > innerHeight * 0.55) score += 80;
    if (node.matches?.(".composer-footer")) score += 6;
    if (node.querySelector?.(".composer-footer")) score += 30;
    if (node.querySelector?.("textarea, [contenteditable='true']")) score += 45;
    score += Math.min(20, Array.from(node.querySelectorAll?.("button, [role='button']") || []).filter(visible).length * 2);
    score += Math.min(20, rect.width / 80);
    score -= Math.max(0, (innerHeight * 0.45 - rect.top) / 10);
    return score;
  };
  const composer = Array.from(candidates)
    .map((node, index) => ({ node, index, score: scoreComposer(node) }))
    .sort((left, right) => (right.score - left.score) || (left.index - right.index))[0]?.node || null;

  return {
    sessionId: activeRef.sessionId || "",
    title: resolvedTitle || "",
    devicePixelRatio: window.devicePixelRatio || 1,
    headerRect,
    titleRect,
    topSlotRect,
    composerRect: rectFor(composer),
    appError: appErrorText(),
  };
})()
"""

SESSION_SWITCH_SCRIPT_TEMPLATE = r"""
(() => {
  const target = __TARGET_PAYLOAD__;
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const normalizeThreadId = (value) => {
    const text = normalize(value);
    const match = text.match(/^(?:[a-z0-9_.-]+:)(.+)$/i);
    return match ? normalize(match[1]) : text;
  };
  const hudRootSelector = "#codex-usage-hud-root";
  const visible = (node) => {
    if (!(node instanceof HTMLElement) || !node.isConnected) return false;
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const rowHref = (row) => row?.getAttribute?.("href") || row?.querySelector?.("a")?.getAttribute?.("href") || "";
  const locationThreadId = () => {
    const source = `${location.pathname}${location.search}${location.hash}`;
    const match = source.match(/(?:session|conversation|thread)(?:\/|=|:|-)([A-Za-z0-9_.-]+)/i)
      || source.match(/\/([0-9a-fA-F-]{36})(?:[/?#]|$)/)
      || source.match(/\/([A-Za-z0-9_-]{24,})(?:[/?#]|$)/);
    return match ? normalizeThreadId(decodeURIComponent(match[1])) : "";
  };
  const refFromRow = (row) => {
    const href = rowHref(row);
    const idMatch = href.match(/(?:session|conversation|thread)[=/:-]([A-Za-z0-9_.-]+)/i) || href.match(/([A-Za-z0-9_-]{8,})$/);
    const rawSessionId = normalize(
      row.getAttribute("data-app-action-sidebar-thread-id")
      || (idMatch && idMatch[1])
      || row.getAttribute("data-session-id")
      || row.getAttribute("data-testid")
      || ""
    );
    const sessionId = normalizeThreadId(rawSessionId);
    const titleNode = row.querySelector("[data-thread-title], .truncate.select-none, .truncate.text-base");
    const rawTitle = titleNode?.textContent || (titleNode ? "" : (row.textContent || ""));
    const title = normalize(titleNode ? rawTitle : rawTitle.replace(/\s*(Export|Delete|Move|Remove from project|导出|删除|移动|移出项目)+$/g, "")).slice(0, 160);
    return { rawSessionId, sessionId, title };
  };
  const rowMatchesLocation = (row) => {
    const href = rowHref(row);
    if (href) {
      try {
        const url = new URL(href, location.href);
        if (url.href === location.href) return true;
      } catch (_) {
        if (location.href.includes(href)) return true;
      }
    }
    const ref = refFromRow(row);
    return (
      (!!ref.rawSessionId && location.href.includes(ref.rawSessionId))
      || (!!ref.sessionId && location.href.includes(ref.sessionId))
    );
  };
  const rowSelectedByState = (row) => {
    if (row.getAttribute("data-app-action-sidebar-thread-active") === "true") return true;
    if (row.getAttribute("aria-current") === "page" || row.getAttribute("aria-current") === "true") return true;
    if (row.getAttribute("aria-selected") === "true") return true;
    if (row.getAttribute("data-active") === "true" || row.getAttribute("data-selected") === "true") return true;
    if (row.matches?.("[data-state='active'], [data-state='selected'], .active, .selected")) return true;
    return false;
  };
  const activeRef = () => {
    const rows = Array.from(document.querySelectorAll("[data-app-action-sidebar-thread-id]"));
    const row = rows.find(rowSelectedByState) || rows.find(rowMatchesLocation) || null;
    return row ? refFromRow(row) : { sessionId: locationThreadId(), title: "" };
  };
  const queryRows = () => Array.from(document.querySelectorAll("[data-app-action-sidebar-thread-id]"))
    .filter((row) => visible(row) && !row.closest(hudRootSelector));
  const labelForNode = (node) => normalize([
    node?.getAttribute?.("aria-label"),
    node?.getAttribute?.("title"),
    node?.textContent,
  ].filter(Boolean).join(" "));
  const revealSidebar = async () => {
    const toggles = Array.from(document.querySelectorAll("button, [role='button'], a"))
      .filter((node) => visible(node) && !node.closest(hudRootSelector))
      .map((node) => ({ node, label: labelForNode(node), rect: node.getBoundingClientRect() }))
      .filter((item) => /sidebar|history|conversation|conversations|chat history|对话|会话|历史/i.test(item.label))
      .sort((left, right) => (left.rect.left - right.rect.left) || (left.rect.top - right.rect.top));
    const toggle = toggles[0]?.node;
    if (!toggle) return false;
    toggle.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    if (typeof toggle.click === "function") toggle.click();
    for (let attempt = 0; attempt < 10; attempt += 1) {
      await sleep(90);
      if (queryRows().length > 0) return true;
    }
    return queryRows().length > 0;
  };
  const titleMatches = (candidate, requested) => {
    if (!candidate || !requested) return false;
    const left = normalize(candidate).toLowerCase();
    const right = normalize(requested).toLowerCase();
    return left === right || left.startsWith(right) || right.startsWith(left);
  };
  const projectLabelFromWorkdir = (value) => {
    const text = normalize(value);
    if (!text) return "";
    const parts = text.split(/[\\/]+/).filter(Boolean);
    return normalize(parts[parts.length - 1] || "");
  };
  const clickPrimaryNode = (node) => {
    if (!(node instanceof HTMLElement)) return;
    node.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, cancelable: true, view: window }));
    node.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
    node.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
    if (typeof node.click === "function") node.click();
    else node.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  };
  const visibleSearchInput = () => Array.from(document.querySelectorAll("input[cmdk-input], input[placeholder*='搜索'], input[role='combobox'], textarea"))
    .find((node) => visible(node) && !node.closest(hudRootSelector)) || null;
  const setNativeInputValue = (input, nextValue) => {
    if (!input) return;
    const prototype = Object.getPrototypeOf(input);
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    if (descriptor?.set) descriptor.set.call(input, nextValue);
    else input.value = nextValue;
    input.dispatchEvent(new InputEvent("input", { bubbles: true, data: nextValue, inputType: "insertText" }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const openSearchDialog = async () => {
    let input = visibleSearchInput();
    if (input) return input;
    const buttons = Array.from(document.querySelectorAll("button, [role='button'], a"))
      .filter((node) => visible(node) && !node.closest(hudRootSelector))
      .map((node) => ({ node, label: labelForNode(node) }));
    const searchButton = buttons.find((item) => /搜索|search/i.test(item.label))?.node || null;
    if (!searchButton) return null;
    clickPrimaryNode(searchButton);
    for (let attempt = 0; attempt < 10; attempt += 1) {
      await sleep(80);
      input = visibleSearchInput();
      if (input) return input;
    }
    return null;
  };
  const searchCommandItems = () => Array.from(document.querySelectorAll("[cmdk-item], [role='option']"))
    .filter((node) => visible(node) && !node.closest(hudRootSelector))
    .map((node) => ({
      node,
      text: normalize(node.textContent || ""),
      aria: normalize(node.getAttribute("aria-label") || ""),
      shortcut: normalize(node.querySelector?.("kbd")?.textContent || ""),
    }));
  const activateViaSearch = async (title, sessionId, workdir) => {
    const input = await openSearchDialog();
    if (!input) return { ok: false, status: "search-unavailable", matchedBy: "" };
    input.focus?.();
    setNativeInputValue(input, title);
    const projectLabel = projectLabelFromWorkdir(workdir).toLowerCase();
    for (let attempt = 0; attempt < 12; attempt += 1) {
      await sleep(80);
      const items = searchCommandItems();
      const match = items.find((item) => titleMatches(item.text, title) && (!projectLabel || item.text.toLowerCase().includes(projectLabel)))
        || items.find((item) => titleMatches(item.text, title))
        || items.find((item) => item.text.toLowerCase().includes(normalize(title).toLowerCase()));
      if (!match) {
        if (attempt < 11) continue;
        return { ok: false, status: "search-no-result", matchedBy: "" };
      }
      const shortcutMatch = match.shortcut.match(/Ctrl\+([0-9])/i);
      if (shortcutMatch) {
        const digit = shortcutMatch[1];
        const keyTarget = document.activeElement instanceof HTMLElement ? document.activeElement : input;
        keyTarget?.dispatchEvent(new KeyboardEvent("keydown", {
          key: digit,
          code: `Digit${digit}`,
          ctrlKey: true,
          bubbles: true,
          cancelable: true,
        }));
        keyTarget?.dispatchEvent(new KeyboardEvent("keyup", {
          key: digit,
          code: `Digit${digit}`,
          ctrlKey: true,
          bubbles: true,
          cancelable: true,
        }));
      } else {
        clickPrimaryNode(match.node);
      }
      for (let waitAttempt = 0; waitAttempt < 20; waitAttempt += 1) {
        await sleep(80);
        const active = activeRef();
        if (
          (sessionId && active.sessionId === sessionId)
          || (title && titleMatches(active.title, title))
        ) {
          return {
            ok: true,
            status: "switched",
            matchedBy: shortcutMatch
              ? (projectLabel ? "search-shortcut-project" : "search-shortcut")
              : (projectLabel ? "search-title-project" : "search-title"),
          };
        }
      }
      return {
        ok: false,
        status: "search-switch-timeout",
        matchedBy: shortcutMatch
          ? (projectLabel ? "search-shortcut-project" : "search-shortcut")
          : (projectLabel ? "search-title-project" : "search-title"),
      };
    }
    return { ok: false, status: "search-no-result", matchedBy: "" };
  };
  const targetRawSessionId = normalize(target?.sessionId || "");
  const targetSessionId = normalizeThreadId(targetRawSessionId);
  const targetTitle = normalize(target?.title || "");
  const targetWorkdir = normalize(target?.workdir || "");
  const rowMatch = (rows) => {
    const refs = rows.map((row) => ({ row, ref: refFromRow(row) }));
    if (targetSessionId) {
      const idMatch = refs.find((item) => item.ref.sessionId === targetSessionId)
        || refs.find((item) => targetRawSessionId && item.ref.rawSessionId === targetRawSessionId)
        || refs.find((item) => item.ref.rawSessionId.endsWith(`:${targetSessionId}`))
        || refs.find((item) => rowHref(item.row).includes(targetSessionId))
        || null;
      if (idMatch) return idMatch;
    }
    return refs.find((item) => targetTitle && item.ref.title === targetTitle)
      || refs.find((item) => targetTitle && titleMatches(item.ref.title, targetTitle))
      || null;
  };
  const targetProjectRows = () => {
    const projectLabel = projectLabelFromWorkdir(targetWorkdir).toLowerCase();
    if (!projectLabel) return [];
    return Array.from(document.querySelectorAll("[data-app-action-sidebar-project-row]"))
      .filter((row) => (
        !row.closest(hudRootSelector)
        && normalize(row.getAttribute("data-app-action-sidebar-project-label") || row.getAttribute("aria-label") || row.textContent).toLowerCase() === projectLabel
      ));
  };
  const revealTargetProjects = async () => {
    const projectLabel = projectLabelFromWorkdir(targetWorkdir);
    if (!projectLabel) return [];
    let projects = targetProjectRows();
    if (!projects.length) {
      const projectsToggle = Array.from(document.querySelectorAll("[data-app-action-sidebar-section-toggle]"))
        .find((node) => visible(node) && /^(projects?|项目)$/i.test(normalize(node.textContent)));
      if (projectsToggle?.getAttribute("aria-expanded") === "false") {
        clickPrimaryNode(projectsToggle);
        await sleep(100);
        projects = targetProjectRows();
      }
    }
    for (const project of projects) {
      project.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      if (
        project.getAttribute("aria-expanded") === "false"
        || project.getAttribute("data-app-action-sidebar-project-collapsed") === "true"
      ) {
        clickPrimaryNode(project);
        await sleep(120);
      }
    }
    return targetProjectRows()
      .map((row) => row.closest("[data-sidebar-project-kind]"))
      .filter(Boolean);
  };
  const nextProjectExpander = (scopes) => {
    for (const scope of scopes) {
      const showMore = Array.from(scope.querySelectorAll("button, [role='button']"))
        .find((node) => (
          visible(node)
          && !node.closest(hudRootSelector)
          && /^(show more|load more|展开显示|显示更多|展开更多)$/i.test(normalize(node.textContent || node.getAttribute("aria-label")))
        ));
      if (showMore) return showMore;
      const collapsedGroup = Array.from(
        scope.querySelectorAll("[data-app-action-sidebar-section-toggle][aria-expanded='false']")
      ).find((node) => visible(node) && !node.closest(hudRootSelector));
      if (collapsedGroup) return collapsedGroup;
    }
    return null;
  };
  const activeMatchesTarget = (active) => (
    (targetSessionId && active.sessionId === targetSessionId)
    || (targetTitle && titleMatches(active.title, targetTitle))
  );
  const activateTarget = async () => {
    const current = activeRef();
    if (activeMatchesTarget(current)) {
      const matchedBy = targetSessionId && current.sessionId === targetSessionId
        ? "active-session-id"
        : "active-title-fallback";
      return {
        ok: true,
        status: "already-active",
        requestedSessionId: targetSessionId,
        requestedTitle: targetTitle,
        activeSessionId: current.sessionId || "",
        activeTitle: current.title || "",
        matchedBy,
        availableCount: queryRows().length,
      };
    }
    let rows = queryRows();
    let sidebarRevealRequested = false;
    let match = rowMatch(rows);
    if (!match && !rows.length) {
      sidebarRevealRequested = await revealSidebar();
    }
    for (let attempt = 0; !match && attempt < 12; attempt += 1) {
      const scopes = await revealTargetProjects();
      rows = queryRows();
      match = rowMatch(rows);
      if (match) break;
      const expander = nextProjectExpander(scopes);
      if (!expander) break;
      clickPrimaryNode(expander);
      await sleep(120);
    }
    if (!match) {
      return {
        ok: false,
        status: sidebarRevealRequested
          ? "sidebar-reveal-requested"
          : (rows.length ? "thread-not-found" : "sidebar-unavailable"),
        requestedSessionId: targetSessionId,
        requestedTitle: targetTitle,
        activeSessionId: current.sessionId || "",
        activeTitle: current.title || "",
        matchedBy: sidebarRevealRequested ? "sidebar-toggle" : "",
        availableCount: rows.length,
      };
    }
    const matchedBy = targetSessionId && match.ref.sessionId === targetSessionId
      ? "session-id"
      : (targetSessionId && (match.ref.rawSessionId === targetRawSessionId || match.ref.rawSessionId.endsWith(`:${targetSessionId}`)))
        ? "session-id-prefixed"
        : (targetSessionId && rowHref(match.row).includes(targetSessionId))
          ? "href"
          : (match.ref.title === targetTitle ? "title-fallback" : "title-prefix-fallback");
    const row = match.row;
    row.scrollIntoView?.({ block: "center", inline: "nearest" });
    const titleNode = row.querySelector("[data-thread-title], .truncate.select-none, .truncate.text-base");
    const primary = titleNode?.closest?.("a[href], button, [role='button']")
      || row.querySelector("a[href]")
      || row.querySelector("button, [role='button']")
      || row;
    clickPrimaryNode(primary);
    let active = activeRef();
    for (let attempt = 0; !activeMatchesTarget(active) && attempt < 25; attempt += 1) {
      await sleep(80);
      active = activeRef();
    }
    return {
      ok: activeMatchesTarget(active),
      status: activeMatchesTarget(active) ? "switched" : "switch-timeout",
      requestedSessionId: targetSessionId,
      requestedTitle: targetTitle,
      activeSessionId: active.sessionId || "",
      activeTitle: active.title || "",
      matchedBy,
      availableCount: rows.length,
    };
  };
  return activateTarget();
})()
"""


def cdp_enabled_from_env() -> bool:
    value = os.environ.get(CDP_DOM_ENV)
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def cdp_port_from_env(default: int = DEFAULT_CDP_PORT) -> int:
    raw = os.environ.get(CDP_PORT_ENV, "").strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError:
        return default
    return port if 0 < port < 65536 else default


class CodexCdpProbe:
    """Small cached read-only probe for Codex DOM state over local CDP."""

    def __init__(
        self,
        *,
        port: int | None = None,
        timeout_seconds: float = DEFAULT_CDP_TIMEOUT_SECONDS,
        cache_seconds: float = DEFAULT_CDP_CACHE_SECONDS,
        failure_cooldown_seconds: float = DEFAULT_CDP_FAILURE_COOLDOWN_SECONDS,
        enabled: bool | None = None,
    ) -> None:
        self.port = int(port or cdp_port_from_env())
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.cache_seconds = max(0.0, float(cache_seconds))
        self.failure_cooldown_seconds = max(0.1, float(failure_cooldown_seconds))
        self.enabled = cdp_enabled_from_env() if enabled is None else bool(enabled)
        self.last_status = "idle" if self.enabled else "disabled"
        self.last_error = ""
        self._cache: CdpDomSnapshot | None = None
        self._cache_at = 0.0
        self._failure_until = 0.0

    def snapshot(self, *, force: bool = False) -> CdpDomSnapshot | None:
        if not self.enabled:
            self.last_status = "disabled"
            return None
        now = time.monotonic()
        if not force and self._cache is not None and now - self._cache_at <= self.cache_seconds:
            self.last_status = "cache"
            return self._cache
        if not force and now < self._failure_until:
            self.last_status = "cooldown"
            return None

        try:
            targets = list_targets(self.port, self.timeout_seconds)
            target = pick_page_target(targets)
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            result = evaluate_script(websocket_url, DOM_PROBE_SCRIPT, self.timeout_seconds)
            snapshot = snapshot_from_evaluate_result(result)
        except Exception as exc:
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._failure_until = now + self.failure_cooldown_seconds
            return None

        if snapshot is None:
            self.last_status = "empty"
            self.last_error = "Runtime.evaluate returned no DOM snapshot"
            self._failure_until = now + self.failure_cooldown_seconds
            return None
        self._cache = snapshot
        self._cache_at = time.monotonic()
        self._failure_until = 0.0
        self.last_status = "ok"
        self.last_error = ""
        return snapshot


def session_switch_script(
    session_id: str = "",
    title: str = "",
    workdir: str = "",
) -> str:
    payload = json.dumps(
        {
            "sessionId": str(session_id or "").strip(),
            "title": str(title or "").strip(),
            "workdir": str(workdir or "").strip(),
        },
        ensure_ascii=False,
    )
    return SESSION_SWITCH_SCRIPT_TEMPLATE.replace("__TARGET_PAYLOAD__", payload)


class CodexCdpSessionController:
    """Best-effort controller that switches the active Codex thread via CDP."""

    def __init__(
        self,
        *,
        port: int | None = None,
        timeout_seconds: float = DEFAULT_CDP_SWITCH_TIMEOUT_SECONDS,
        target_cache_seconds: float = 2.0,
        enabled: bool | None = None,
    ) -> None:
        self.port = int(port or cdp_port_from_env())
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.target_cache_seconds = max(0.0, float(target_cache_seconds))
        self.enabled = cdp_enabled_from_env() if enabled is None else bool(enabled)
        self.last_status = "idle" if self.enabled else "disabled"
        self.last_error = ""
        self._cached_target_id = ""
        self._cached_websocket_url = ""
        self._target_cache_at = 0.0

    def activate_thread(
        self,
        *,
        session_id: str = "",
        title: str = "",
        workdir: str = "",
    ) -> CdpSessionSwitchResult:
        requested_session_id = str(session_id or "").strip()
        requested_title = str(title or "").strip()
        requested_workdir = str(workdir or "").strip()
        if not self.enabled:
            self.last_status = "disabled"
            return CdpSessionSwitchResult(
                ok=False,
                status="disabled",
                requested_session_id=requested_session_id,
                requested_title=requested_title,
                message="CDP controller is disabled",
            )
        if not requested_session_id and not requested_title:
            self.last_status = "invalid"
            return CdpSessionSwitchResult(
                ok=False,
                status="missing-target",
                message="session id or title is required",
            )
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")

            def evaluate_switch() -> CdpSessionSwitchResult:
                result = send_cdp_command(
                    websocket_url,
                    "Runtime.evaluate",
                    runtime_evaluate_params(
                        session_switch_script(
                            requested_session_id,
                            requested_title,
                            requested_workdir,
                        ),
                        await_promise=True,
                    ),
                    self.timeout_seconds,
                )
                value = (
                    result.get("result", {})
                    .get("result", {})
                    .get("value")
                )
                if not isinstance(value, dict):
                    raise RuntimeError("CDP switch script returned no value")
                return CdpSessionSwitchResult(
                    ok=bool(value.get("ok")),
                    status=str(value.get("status") or "unknown"),
                    requested_session_id=str(
                        value.get("requestedSessionId") or requested_session_id
                    ).strip(),
                    requested_title=str(
                        value.get("requestedTitle") or requested_title
                    ).strip(),
                    active_session_id=str(value.get("activeSessionId") or "").strip(),
                    active_title=str(value.get("activeTitle") or "").strip(),
                    matched_by=str(value.get("matchedBy") or "").strip(),
                    available_count=int(value.get("availableCount") or 0),
                    message=str(value.get("message") or "").strip(),
                )

            switch_result = evaluate_switch()
            if switch_result.status == "sidebar-reveal-requested":
                time.sleep(0.16)
                retry_result = evaluate_switch()
                if retry_result.status != "sidebar-reveal-requested" or retry_result.ok:
                    switch_result = retry_result
        except Exception as exc:
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._clear_target_cache()
            return CdpSessionSwitchResult(
                ok=False,
                status="cdp-error",
                requested_session_id=requested_session_id,
                requested_title=requested_title,
                message=self.last_error,
            )
        self.last_status = switch_result.status
        self.last_error = switch_result.message
        return switch_result

    def _page_target(self, *, force: bool = False) -> dict[str, Any]:
        if (
            not force
            and self._cached_websocket_url
            and self._cached_target_id
            and time.monotonic() - self._target_cache_at <= self.target_cache_seconds
        ):
            return {
                "id": self._cached_target_id,
                "webSocketDebuggerUrl": self._cached_websocket_url,
            }
        targets = list_targets(self.port, self.timeout_seconds)
        target = pick_page_target(targets)
        self._cached_target_id = str(
            target.get("id") or target.get("webSocketDebuggerUrl") or ""
        )
        self._cached_websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        self._target_cache_at = time.monotonic()
        return target

    def _clear_target_cache(self) -> None:
        self._cached_target_id = ""
        self._cached_websocket_url = ""
        self._target_cache_at = 0.0


def list_targets(port: int, timeout_seconds: float) -> list[dict[str, Any]]:
    data = _read_http_json(port, timeout_seconds, endpoint="/json")
    return data if isinstance(data, list) else []


def cdp_version_info(port: int, timeout_seconds: float) -> dict[str, Any]:
    """Return the bounded local CDP version response for endpoint validation."""
    data = _read_http_json(port, timeout_seconds, endpoint="/json/version")
    if not isinstance(data, dict):
        raise RuntimeError("Invalid CDP version response")
    if not any(
        str(data.get(key) or "").strip()
        for key in ("Browser", "Protocol-Version", "webSocketDebuggerUrl")
    ):
        raise RuntimeError("CDP version response has no protocol identity")
    return data


def _read_http_json(port: int, timeout_seconds: float, *, endpoint: str) -> Any:
    opener = build_opener(ProxyHandler({}))
    errors: list[Exception] = []
    for host in ("127.0.0.1", "[::1]"):
        url = f"http://{host}:{port}{endpoint}"
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with opener.open(request, timeout=timeout_seconds) as response:
                payload = response.read(512 * 1024).decode("utf-8", "replace")
            return json.loads(payload)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise errors[-1]
    raise RuntimeError("No local CDP host was available")


def pick_page_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    pages = [
        target
        for target in targets
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl")
    ]
    codex_pages = [
        target
        for target in pages
        if (
            "codex" in f"{target.get('title') or ''} {target.get('url') or ''}".lower()
            or str(target.get("url") or "").startswith("app://")
        )
    ]
    main_pages = [
        target
        for target in codex_pages
        if not _is_auxiliary_window_target(target)
    ]
    ranked_pages = sorted(
        main_pages,
        key=_page_target_rank,
        reverse=True,
    )
    if ranked_pages:
        return ranked_pages[0]
    if codex_pages:
        raise RuntimeError("No main Codex CDP page target found")
    raise RuntimeError("No Codex CDP page target found")


def _page_target_rank(target: dict[str, Any]) -> tuple[int, int]:
    title = str(target.get("title") or "").strip().lower()
    url = str(target.get("url") or "").strip().lower()
    score = 0
    if "codex" in title:
        score += 80
    if url.startswith("app://"):
        score += 60
    if url.startswith("app://-/index.html"):
        score += 120
    if _is_hotkey_window_target(target):
        score -= 160
    if "hotkey" in title:
        score -= 80
    # Prefer the main app surface over transient helper pages when scores tie.
    return score, -len(url)


def _is_hotkey_window_target(target: dict[str, Any]) -> bool:
    url = str(target.get("url") or "").strip().lower()
    title = str(target.get("title") or "").strip().lower()
    return (
        "initialroute=%2fhotkey-window" in url
        or "initialroute=/hotkey-window" in url
        or "hotkey" in title
    )


def _is_auxiliary_window_target(target: dict[str, Any]) -> bool:
    # 新版头像窗口也使用 index.html；它不承载会话，不能作为启动接入成功的依据。
    query = parse_qs(urlparse(str(target.get("url") or "")).query)
    routes = query.get("initialRoute", [])
    return _is_hotkey_window_target(target) or any(
        route.rstrip("/").casefold() == "/avatar-overlay" for route in routes
    )


def evaluate_script(websocket_url: str, script: str, timeout_seconds: float) -> dict[str, Any]:
    return send_cdp_command(
        websocket_url,
        "Runtime.evaluate",
        runtime_evaluate_params(script),
        timeout_seconds,
    )


def runtime_evaluate_params(
    script: str,
    *,
    return_by_value: bool = True,
    await_promise: bool = False,
) -> dict[str, Any]:
    return {
        "expression": script,
        "returnByValue": return_by_value,
        "awaitPromise": bool(await_promise),
        "allowUnsafeEvalBlockedByCSP": True,
    }


def install_new_document_script(
    websocket_url: str,
    script: str,
    timeout_seconds: float,
) -> str:
    """Install and immediately evaluate a renderer script in one CDP session."""
    results = send_cdp_commands(
        websocket_url,
        [
            ("Page.enable", {}),
            ("Page.addScriptToEvaluateOnNewDocument", {"source": script}),
            ("Runtime.evaluate", runtime_evaluate_params(script)),
        ],
        timeout_seconds,
    )
    identifier = (
        results.get(2, {})
        .get("result", {})
        .get("identifier", "")
    )
    return str(identifier or "")


def remove_new_document_script(
    websocket_url: str,
    identifier: str,
    timeout_seconds: float,
) -> None:
    if not identifier:
        return
    send_cdp_command(
        websocket_url,
        "Page.removeScriptToEvaluateOnNewDocument",
        {"identifier": identifier},
        timeout_seconds,
    )


def send_cdp_command(
    websocket_url: str,
    method: str,
    params: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    return send_cdp_commands(
        websocket_url,
        [(method, params)],
        timeout_seconds,
    )[1]


def send_cdp_commands(
    websocket_url: str,
    commands: list[tuple[str, dict[str, Any]]],
    timeout_seconds: float,
) -> dict[int, dict[str, Any]]:
    if not commands:
        return {}
    parsed = urlparse(websocket_url)
    if parsed.scheme != "ws":
        raise RuntimeError("Only local ws:// CDP endpoints are supported")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
        sock.settimeout(timeout_seconds)
        _websocket_handshake(sock, host, port, path)
        pending: set[int] = set()
        for index, (method, params) in enumerate(commands, start=1):
            pending.add(index)
            command = {
                "id": index,
                "method": method,
                "params": params,
            }
            _send_text_frame(sock, json.dumps(command, separators=(",", ":")))
        deadline = time.monotonic() + timeout_seconds
        results: dict[int, dict[str, Any]] = {}
        while time.monotonic() < deadline:
            message = _receive_text_message(sock)
            payload = json.loads(message)
            command_id = payload.get("id")
            if command_id in pending:
                results[int(command_id)] = payload
                pending.remove(int(command_id))
                if not pending:
                    return results
    raise TimeoutError("Timed out waiting for CDP command response")


def snapshot_from_evaluate_result(result: dict[str, Any]) -> CdpDomSnapshot | None:
    value = (
        result.get("result", {})
        .get("result", {})
        .get("value")
    )
    if not isinstance(value, dict):
        return None
    dpr = _positive_float(value.get("devicePixelRatio")) or 1.0
    app_error = str(value.get("appError") or "").strip()
    normalized_app_error = " ".join(app_error.lower().split())
    permission_advisory_markers = (
        "full access is on",
        "chatgpt will be able to run commands, use the internet",
        "this comes with risks like data loss and prompt injection",
    )
    if any(marker in normalized_app_error for marker in permission_advisory_markers):
        app_error = ""
    return CdpDomSnapshot(
        session_id=str(value.get("sessionId") or "").strip(),
        title=str(value.get("title") or "").strip(),
        device_pixel_ratio=dpr,
        header_rect=_rect_from_value(value.get("headerRect")),
        title_rect=_rect_from_value(value.get("titleRect")),
        top_slot_rect=_rect_from_value(value.get("topSlotRect")),
        composer_rect=_rect_from_value(value.get("composerRect")),
        app_error=app_error,
    )


def _rect_from_value(value: Any) -> CdpRect | None:
    if not isinstance(value, dict):
        return None
    left = _finite_float(value.get("left"))
    top = _finite_float(value.get("top"))
    right = _finite_float(value.get("right"))
    bottom = _finite_float(value.get("bottom"))
    width = _finite_float(value.get("width"))
    height = _finite_float(value.get("height"))
    if left is None or top is None:
        return None
    if right is None and width is not None:
        right = left + width
    if bottom is None and height is not None:
        bottom = top + height
    if right is None or bottom is None:
        return None
    rect = CdpRect(left=left, top=top, right=right, bottom=bottom)
    return rect if rect.width > 0 and rect.height > 0 else None


def _positive_float(value: Any) -> float | None:
    number = _finite_float(value)
    return number if number is not None and number > 0 else None


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _websocket_handshake(sock: socket.socket, host: str, port: int, path: str) -> None:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    authority = _http_authority(host, port)
    origin_authority = _http_authority(_cdp_origin_host(host), port)
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {authority}\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Origin: http://{origin_authority}\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response and len(response) < 8192:
        chunk = sock.recv(1024)
        if not chunk:
            break
        response += chunk
    first_line = response.split(b"\r\n", 1)[0]
    if b" 101 " not in first_line:
        raise RuntimeError("CDP websocket handshake failed")


def _http_authority(host: str, port: int) -> str:
    normalized = str(host or "").strip().strip("[]") or "127.0.0.1"
    if ":" in normalized:
        return f"[{normalized}]:{port}"
    return f"{normalized}:{port}"


def _cdp_origin_host(host: str) -> str:
    normalized = str(host or "").strip().strip("[]")
    if not normalized:
        return "127.0.0.1"
    if normalized.lower() == "localhost":
        return "127.0.0.1"
    try:
        if ipaddress.ip_address(normalized).is_loopback:
            return "127.0.0.1"
    except ValueError:
        return normalized
    return normalized


def _send_text_frame(sock: socket.socket, payload: str) -> None:
    data = payload.encode("utf-8")
    header = bytearray([0x81])
    length = len(data)
    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    sock.sendall(bytes(header) + mask + masked)


def _receive_text_message(sock: socket.socket) -> str:
    parts: list[bytes] = []
    while True:
        first, second = _read_exact(sock, 2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _read_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _read_exact(sock, 8))[0]
        mask = _read_exact(sock, 4) if masked else b""
        payload = _read_exact(sock, length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 0x8:
            raise RuntimeError("CDP websocket closed")
        if opcode == 0x9:
            _send_pong_frame(sock, payload)
            continue
        if opcode in {0x1, 0x0}:
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")


def _send_pong_frame(sock: socket.socket, payload: bytes) -> None:
    header = bytearray([0x8A])
    length = len(payload)
    if length >= 126:
        payload = payload[:125]
        length = len(payload)
    header.append(0x80 | length)
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _read_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("Unexpected EOF from CDP websocket")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


__all__ = [
    "CDP_DOM_ENV",
    "CDP_PORT_ENV",
    "CdpDomSnapshot",
    "CdpRect",
    "CdpSessionSwitchResult",
    "CodexCdpSessionController",
    "CodexCdpProbe",
    "DOM_PROBE_SCRIPT",
    "DEFAULT_CDP_PORT",
    "cdp_version_info",
    "install_new_document_script",
    "pick_page_target",
    "remove_new_document_script",
    "runtime_evaluate_params",
    "session_switch_script",
    "send_cdp_command",
    "send_cdp_commands",
    "snapshot_from_evaluate_result",
]
