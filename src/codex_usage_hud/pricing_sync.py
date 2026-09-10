"""Safe parsing and comparison helpers for official HTML pricing pages."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import hashlib
from pathlib import Path
import json
import re
import time
from typing import Iterable
from urllib.request import Request, urlopen


OPENAI_MODELS_URL = "https://developers.openai.com/api/docs/models"
OPENAI_PRICING_SNAPSHOT_URL = "https://raw.githubusercontent.com/fengbuming/codex-usage-hud/pricing-snapshot/docs/openai-pricing-snapshot.json"
SNAPSHOT_MIRROR_URL_TEMPLATES = ("https://ghproxy.net/{url}", "https://gh-proxy.com/{url}")


@dataclass(frozen=True)
class OfficialPrice:
    model: str
    input: Decimal
    cached_input: Decimal = Decimal("0")
    output: Decimal = Decimal("0")
    reasoning: Decimal = Decimal("0")
    cache_write: Decimal = Decimal("0")
    currency: str = "USD"
    unit: str = "USD_per_1M_tokens"

    def to_dict(self) -> dict[str, object]:
        return {"model": self.model, "input": float(self.input), "cached_input": float(self.cached_input),
                "cache_write": float(self.cache_write), "output": float(self.output),
                "reasoning": float(self.reasoning), "currency": self.currency, "unit": self.unit}


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table": self._table = []
        elif tag == "tr": self._row = []
        elif tag in {"td", "th"} and self._row is not None: self._cell = []

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if normalized:
            self.text.append(normalized)
        if self._cell is not None: self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join(self._cell).strip()); self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
                if self._table is not None:
                    self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


_MONEY = re.compile(r"(?:\$\s*)?([0-9]+(?:\.[0-9]+)?)\s*(?:USD)?", re.I)
_MODEL = re.compile(r"\b(?:gpt-|o[134](?:-|$)|chatgpt-|text-|whisper-|dall-e-)[A-Za-z0-9_.:-]*\b", re.I)


def _money(value: str) -> Decimal | None:
    match = _MONEY.search(value.replace(",", ""))
    if not match: return None
    try: return Decimal(match.group(1))
    except InvalidOperation: return None


def parse_openai_models_html(body: str | bytes) -> dict[str, OfficialPrice]:
    raw = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    parser = _TableParser(); parser.feed(raw)
    records: dict[str, OfficialPrice] = {}
    table_rows = parser.tables[0] if parser.tables else parser.rows
    for row in table_rows:
        if len(row) < 3: continue
        joined = " ".join(row)
        model_match = _MODEL.search(row[0]) or _MODEL.search(joined)
        if not model_match: continue
        model = model_match.group(0)
        if model in records:
            continue
        amounts = [_money(cell) for cell in row[1:]]
        amounts = [item for item in amounts if item is not None]
        if len(amounts) < 2: continue
        output_index = 3 if len(amounts) >= 4 else len(amounts) - 1
        records[model] = OfficialPrice(model=model, input=amounts[0], output=amounts[output_index],
                                       cached_input=amounts[1] if len(amounts) >= 3 else amounts[0],
                                       cache_write=amounts[2] if len(amounts) >= 4 else Decimal("0"),
                                       reasoning=amounts[output_index])
    for index, token in enumerate(parser.text):
        model_match = _MODEL.fullmatch(token)
        if not model_match or model_match.group(0) in records:
            continue
        segment = parser.text[index + 1:index + 100]
        next_model = next((offset for offset, value in enumerate(segment) if _MODEL.fullmatch(value)), len(segment))
        segment = segment[:next_model]
        try:
            input_index = segment.index("Input price")
            output_index = segment.index("Output price")
        except ValueError:
            continue
        input_amount = next((_money(value) for value in segment[input_index + 1:output_index] if _money(value) is not None), None)
        output_amount = next((_money(value) for value in segment[output_index + 1:] if _money(value) is not None), None)
        if input_amount is not None and output_amount is not None:
            model = model_match.group(0)
            records[model] = OfficialPrice(model=model, input=input_amount, cached_input=input_amount,
                                           output=output_amount, reasoning=output_amount)
    if not records:
        raise ValueError("official pricing page contained no reliable model price rows")
    return records


def source_hash(body: str | bytes) -> str:
    data = body if isinstance(body, bytes) else body.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def pricing_snapshot_urls(url: str = OPENAI_PRICING_SNAPSHOT_URL) -> tuple[str, ...]:
    urls = [url]
    for template in SNAPSHOT_MIRROR_URL_TEMPLATES:
        urls.append(template.replace("{url}", url))
    return tuple(urls)


def fetch_pricing_snapshot(*, timeout_seconds: float = 15.0) -> tuple[dict[str, OfficialPrice], dict[str, object]]:
    errors: list[str] = []
    for url in pricing_snapshot_urls():
        try:
            separator = "&" if "?" in url else "?"
            request_url = f"{url}{separator}v={int(time.time() // 300)}"
            request = Request(request_url, headers={"Accept": "application/json", "User-Agent": "codex-usage-hud"})
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise ValueError("pricing snapshot is too large")
            payload = json.loads(body.decode("utf-8"))
            if payload.get("schema_version") != 2 or payload.get("provider") != "openai":
                raise ValueError("unsupported pricing snapshot")
            prices: dict[str, OfficialPrice] = {}
            for row in payload.get("prices", []):
                model = str(row.get("model") or "").strip()
                if not model or not _MODEL.fullmatch(model):
                    continue
                prices[model] = OfficialPrice(model=model, input=Decimal(str(row["input"])),
                    cached_input=Decimal(str(row.get("cached_input", row["input"]))),
                    cache_write=Decimal(str(row.get("cache_write", 0))),
                    output=Decimal(str(row["output"])), reasoning=Decimal(str(row.get("reasoning", row["output"]))))
            if not prices:
                raise ValueError("pricing snapshot contains no supported prices")
            return prices, {"snapshot_url": request_url, "checked_at": str(payload.get("checked_at") or ""),
                            "sources": list(payload.get("sources") or []), "source_hash": source_hash(body)}
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    bundled = Path(__file__).resolve().parent / "assets" / "openai-pricing-snapshot.json"
    try:
        body = bundled.read_bytes()
        payload = json.loads(body.decode("utf-8"))
        if payload.get("schema_version") != 2:
            raise ValueError("unsupported bundled pricing snapshot")
        prices = {
            str(row["model"]): OfficialPrice(model=str(row["model"]), input=Decimal(str(row["input"])),
                cached_input=Decimal(str(row.get("cached_input", row["input"]))), output=Decimal(str(row["output"])),
                reasoning=Decimal(str(row.get("reasoning", row["output"]))),
                cache_write=Decimal(str(row.get("cache_write", 0))))
            for row in payload.get("prices", []) if isinstance(row, dict) and _MODEL.fullmatch(str(row.get("model") or ""))
        }
        if prices:
            return prices, {"snapshot_url": str(bundled), "checked_at": str(payload.get("checked_at") or ""),
                            "sources": list(payload.get("sources") or []), "source_hash": source_hash(body), "bundled": True}
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        errors.append(f"{bundled}: {exc}")
    raise ValueError("unable to fetch pricing snapshot: " + "; ".join(errors))


def classify_price_changes(local: dict[str, object], official: Iterable[OfficialPrice]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    incoming = {item.model: item for item in official}
    for model, item in incoming.items():
        old = local.get(model)
        if old is None:
            result.append({"model": model, "kind": "added", "official": item.to_dict()}); continue
        for field in ("input", "cached_input", "cache_write", "output", "reasoning"):
            old_value = Decimal(str(getattr(old, field, old.get(field, 0) if isinstance(old, dict) else 0)))
            new_value = getattr(item, field)
            if old_value != new_value:
                result.append({"model": model, "field": field,
                               "kind": "increased" if new_value > old_value else "decreased",
                               "local": float(old_value), "official": float(new_value)})
    for model in local:
        if model not in incoming: result.append({"model": model, "kind": "removed"})
    return result
