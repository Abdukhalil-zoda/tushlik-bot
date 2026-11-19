"""Parse and validate the bill JSON the payer sends in DM. See examples/bill.json."""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MAX_ITEMS = 60
MAX_QTY = 100
MAX_NAME = 64


class BillError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class DraftItem:
    name: str
    unit_price: int
    qty: int
    shared: bool
    me: int = 0  # portions the payer ate himself (dishes only)


@dataclass
class Draft:
    title: str
    items: list[DraftItem]
    service_percent: Decimal
    total: int | None  # what the receipt says, only used for a sanity check


def _money(v, where: str, problems: list[str]) -> int | None:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        problems.append(f"{where}: «{v}» — не число")
        return None
    if d <= 0:
        problems.append(f"{where}: сумма должна быть больше нуля")
        return None
    return int(d.to_integral_value())


def _items(raw, shared: bool, key: str, problems: list[str]) -> list[DraftItem]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append(f"«{key}» должен быть списком")
        return []
    out = []
    for i, r in enumerate(raw, 1):
        where = f"{key}[{i}]"
        if not isinstance(r, dict):
            problems.append(f"{where}: нужен объект с name и price")
            continue
        name = str(r.get("name") or "").strip()
        if not name:
            problems.append(f"{where}: нет name")
            continue
        where = f"{key}[{i}] «{name[:20]}»"
        if len(name) > MAX_NAME:
            name = name[:MAX_NAME]
        price = _money(r.get("price"), where, problems)
        qty = r.get("qty", 1)
        if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= MAX_QTY:
            problems.append(f"{where}: qty должно быть целым от 1 до {MAX_QTY}")
            continue
        me = r.get("me", 0) or 0
        if shared:
            me = 0  # shared things are split on everyone anyway
        elif not isinstance(me, int) or isinstance(me, bool) or not 0 <= me <= qty:
            problems.append(f"{where}: me — сколько порций ел ты сам, целое от 0 до {qty}")
            continue
        if price is not None:
            out.append(DraftItem(name, price, qty, shared, me))
    return out


def parse(text: str) -> Draft:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise BillError([f"это не JSON: строка {e.lineno}, позиция {e.colno} — {e.msg}"]) from None
    if not isinstance(data, dict):
        raise BillError(["нужен JSON-объект { ... }"])
    problems: list[str] = []
    items = _items(data.get("items"), False, "items", problems)
    items += _items(data.get("shared"), True, "shared", problems)
    if not items and not problems:
        problems.append("нет ни одного блюда: заполни «items»")
    if len(items) > MAX_ITEMS:
        problems.append(f"слишком много позиций: {len(items)} (максимум {MAX_ITEMS})")
    try:
        svc = Decimal(str(data.get("service_percent", 0) or 0))
    except InvalidOperation:
        svc = Decimal(-1)
    if not Decimal(0) <= svc <= Decimal(50):
        problems.append("service_percent должен быть от 0 до 50")
    total = None
    if data.get("total") is not None:
        total = _money(data["total"], "total", problems)
    if problems:
        raise BillError(problems)
    title = str(data.get("title") or "Обед").strip()[:MAX_NAME]
    return Draft(title, items, svc, total)
