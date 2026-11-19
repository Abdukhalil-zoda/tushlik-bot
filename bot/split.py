"""Who owes what. Pure functions, no Telegram, no DB: easy to test and to change.

Rules:
- A dish (not shared) is paid by those who picked it, in proportion to the portions they picked.
  If the picked portions are fewer than the dish quantity, each pays the unit price per portion
  and the rest stays unclaimed. If more people picked than there are portions (one pizza for three),
  the whole dish is split among them proportionally.
- Shared things (tea, bread) are split equally among all participants.
- Service percent is added on top of each person's subtotal.
- Each amount is rounded UP to `round_to` (so the payer never loses money on rounding).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    unit_price: int
    qty: int = 1
    shared: bool = False

    @property
    def cost(self) -> int:
        return self.unit_price * self.qty


@dataclass
class Split:
    exact: dict[int, Fraction] = field(default_factory=dict)  # user -> amount before rounding
    amounts: dict[int, int] = field(default_factory=dict)  # user -> rounded amount
    unclaimed: list[tuple[Item, int, Fraction]] = field(default_factory=list)  # (item, portions, cost with service)

    @property
    def unclaimed_total(self) -> int:
        return math.ceil(sum((c for _, _, c in self.unclaimed), Fraction(0)))


def round_up(x: Fraction, step: int) -> int:
    step = max(1, step)
    return math.ceil(x / step) * step


def compute(items: list[Item], claims: dict[int, dict[int, int]], participants: set[int],
            service_percent: Decimal | int | float = 0, round_to: int = 1) -> Split:
    """claims: item_id -> {user_id: portions}. participants: everyone who joined (incl. "only shared")."""
    k = 1 + Fraction(Decimal(str(service_percent))) / 100
    people = set(participants) | {u for c in claims.values() for u, n in c.items() if n > 0}
    sub: dict[int, Fraction] = {u: Fraction(0) for u in people}
    res = Split()
    shared_total = Fraction(0)
    for it in items:
        if it.shared:
            shared_total += it.cost
            continue
        picked = {u: n for u, n in claims.get(it.id, {}).items() if n > 0}
        total = sum(picked.values())
        if total == 0:
            res.unclaimed.append((it, it.qty, it.cost * k))
        elif total >= it.qty:
            for u, n in picked.items():
                sub[u] += Fraction(it.cost * n, total)
        else:
            for u, n in picked.items():
                sub[u] += it.unit_price * n
            left = it.qty - total
            res.unclaimed.append((it, left, it.unit_price * left * k))
    if shared_total:
        if people:
            for u in people:
                sub[u] += shared_total / len(people)
        else:
            for it in items:
                if it.shared:
                    res.unclaimed.append((it, it.qty, it.cost * k))
    for u, s in sub.items():
        res.exact[u] = s * k
        res.amounts[u] = round_up(s * k, round_to)
    return res


def bill_total(items: list[Item], service_percent: Decimal | int | float = 0) -> int:
    k = 1 + Fraction(Decimal(str(service_percent))) / 100
    return math.ceil(sum(it.cost for it in items) * k)
