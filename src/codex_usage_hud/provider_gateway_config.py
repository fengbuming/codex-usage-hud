"""Preserve complete provider TOML settings when leaving gateway takeover."""

from __future__ import annotations

import re

from . import codex_provider_config as config


def _provider_prefix(provider: str):
    escaped = re.escape(provider)
    return re.compile(
        rf'''^model_providers\s*\.\s*(?:"{escaped}"|'{escaped}'|{escaped})(?=\s*(?:\.|$))''',
        re.IGNORECASE,
    )


def clear_provider_subtables(text: str, provider: str) -> str:
    prefix = _provider_prefix(provider)
    for start, end, header, _body in reversed(config._toml_table_ranges(text)):
        name = config._toml_table_name(header)
        match = prefix.match(name)
        if match and name[match.end():].strip():
            text = text[:start] + text[end:]
    return text


def copy_provider_settings(text: str, source: str, target: str) -> str:
    """Copy parent assignments and all child tables without deleting profiles."""
    parent = config._section_range(text, source)
    if parent is None:
        raise ValueError("所选供应商配置不存在，未恢复兼容身份。")
    prefix = _provider_prefix(source)
    children = []
    for start, end, header, _body in config._toml_table_ranges(text):
        name = config._toml_table_name(header)
        match = prefix.match(name)
        if match and name[match.end():].strip():
            renamed = "model_providers." + target + name[match.end():]
            brackets = ("[[", "]]") if header.startswith("[[") else ("[", "]")
            children.append(text[start:end].replace(header, brackets[0] + renamed + brackets[1], 1))
    candidate = clear_provider_subtables(text, target)
    candidate = config._replace_section_body(candidate, target, parent[2])
    if children:
        newline = config._preferred_newline(text)
        candidate = candidate.rstrip("\r\n") + newline * 2 + newline.join(
            child.rstrip("\r\n") for child in children
        ) + newline
    return candidate
