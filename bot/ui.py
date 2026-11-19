"""Callback data, keyboards and message texts. Change wording here, not in handlers."""
from __future__ import annotations

import datetime as dt
from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import Config
from .split import Item, Split

# --- callback data (max 64 bytes each) ---------------------------------------


class Menu(CallbackData, prefix="m"):
    a: str  # home | help | bills | card | donate | top | server | about


class Post(CallbackData, prefix="p"):
    bill: int
    chat: int


class Pick(CallbackData, prefix="k"):
    bill: int
    item: int
    op: str  # + | -


class BillA(CallbackData, prefix="b"):
    bill: int
    a: str  # done | shared | close | close_ok | cancel | view | pick


class DebtA(CallbackData, prefix="d"):
    debt: int
    a: str  # paid | receipt | later | l1 | l3 | lt | ok | no | ping


class Don(CallbackData, prefix="n"):
    a: str  # kind | sent | receipt | cancel | ok | no
    kind: str = ""
    id: int = 0


class Top(CallbackData, prefix="t"):
    period: str  # month | prev | all


# --- formatting --------------------------------------------------------------


def money(n: int | float, cfg: Config) -> str:
    return f"{int(round(n)):,}".replace(",", " ") + f" {cfg.currency}"


def who(uid: int, first_name: str | None, username: str | None = None) -> str:
    """A mention: tags the person in a group."""
    name = escape(first_name or username or str(uid))
    return f'<a href="tg://user?id={uid}">{name}</a>'


def bar(part: int, whole: int, width: int = 10) -> str:
    p = 0 if whole <= 0 else min(1.0, part / whole)
    full = round(p * width)
    return "▓" * full + "░" * (width - full) + f" {round(p * 100)}%"


def card_text(number: str | None, holder: str | None) -> str:
    if not number:
        return "карта не указана"
    return f"<code>{escape(number)}</code>" + (f" ({escape(holder)})" if holder else "")


STATUS = {"pending": "⏳", "reported": "🕓", "confirmed": "✅"}

HELP = """<b>Как разделить счёт</b>

1. Один раз укажи свою карту: /card
2. Пришли мне сюда JSON с чеком (текстом или файлом .json):
<pre>{example}</pre>
• <code>items</code> — блюда; <code>qty</code> — сколько порций (по умолчанию 1), <code>price</code> — цена одной порции.
• <code>me</code> — сколько порций этого блюда ел ты сам (необязательно): отмечу за тобой сразу. Остальные отметят себя сами.
• <code>shared</code> — общее (чай, хлеб): делится поровну на всех участников, включая тебя.
• <code>service_percent</code> — обслуживание в процентах, добавляется к доле каждого.
• <code>total</code> — итог по чеку, необязательно: я сверю его со своим подсчётом.
3. Выбери группу. Я опубликую счёт, и каждый отметит кнопками, что ел.
4. Нажми «Закрыть счёт». Каждому придёт сумма и твоя карта. Ты увидишь, кто уже скинул, и сможешь пингануть остальных.

Попроси Claude или ChatGPT: «сделай JSON по этому фото чека в формате tushlik-bot», и пришли результат сюда."""

EXAMPLE = """{
  "title": "Обед в Рохат",
  "items": [
    {"name": "Плов", "price": 45000, "qty": 3, "me": 1},
    {"name": "Лагман", "price": 38000}
  ],
  "shared": [
    {"name": "Чай", "price": 12000},
    {"name": "Хлеб", "price": 4000, "qty": 2}
  ],
  "service_percent": 15,
  "total": 221950
}"""


def home_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🧾 Как разделить счёт", callback_data=Menu(a="help"))
    b.button(text="📋 Мои счета", callback_data=Menu(a="bills"))
    b.button(text="💳 Моя карта", callback_data=Menu(a="card"))
    b.button(text="💛 Поддержать", callback_data=Menu(a="donate"))
    b.button(text="🏆 Лидерборд", callback_data=Menu(a="top"))
    b.button(text="🖥 Сервер", callback_data=Menu(a="server"))
    b.button(text="ℹ️ Open source", callback_data=Menu(a="about"))
    b.adjust(1, 2, 2, 2)
    return b.as_markup()


def home_text() -> str:
    return ("👋 Я делю общий счёт за обед.\n\n"
            "Один платит, я считаю, кто сколько должен, присылаю каждому сумму и карту и слежу, кто уже скинул.")


# --- bill in the group ---------------------------------------------------------


def bill_text(cfg: Config, bill, payer, items: list[Item], claims: dict[int, dict[int, int]],
              people: dict[int, tuple[str, str | None]], split: Split, debts: list | None = None) -> str:
    """people: user_id -> (first_name, username)."""
    def name(u: int) -> str:
        fn, un = people.get(u, (str(u), None))
        return escape(fn or un or str(u))

    lines = [f"🧾 <b>{escape(bill['title'])}</b>", f"Платил: {who(payer['id'], payer['first_name'], payer['username'])}"]
    svc = bill["service_percent"]
    total = sum(it.cost for it in items)
    with_svc = total * (1 + float(svc) / 100)
    lines.append(f"Сумма: {money(with_svc, cfg)}" + (f" (с обслуживанием {svc:g}%)" if svc else ""))
    dishes = [it for it in items if not it.shared]
    if dishes:
        lines.append("\n<b>Блюда</b>")
        for it in dishes:
            qty = f" × {it.qty}" if it.qty > 1 else ""
            c = claims.get(it.id, {})
            picked = ", ".join(f"{name(u)}" + (f" {n}" if n > 1 or it.qty > 1 else "") for u, n in c.items()) or "никто"
            lines.append(f"• {escape(it.name)} — {money(it.unit_price, cfg)}{qty} · {picked}")
    shared = [it for it in items if it.shared]
    if shared:
        lines.append("\n<b>Общее</b> (делится на всех)")
        lines.append(", ".join(f"{escape(it.name)} {money(it.cost, cfg)}" for it in shared))

    status = bill["status"]
    if status == "open":
        if split.amounts:
            lines.append("\n<b>Кто сколько</b> (предварительно, общее пересчитается)")
            for u, a in split.amounts.items():
                lines.append(f"• {name(u)} — {money(a, cfg)}" + (" (платил)" if u == payer["id"] else ""))
        lines.append("\nНажми кнопку ниже и отметь, что ты ел.")
    elif debts is not None:
        lines.append("\n<b>Кто сколько</b>")
        for u, a in split.amounts.items():
            if u == payer["id"]:
                lines.append(f"• {name(u)} — {money(a, cfg)} (платил)")
        for d in debts:
            mark = STATUS.get(d["status"], "⏳")
            if d["status"] == "pending" and d["remind_at"]:
                mark = "⏰"
            lines.append(f"{mark} {who(d['user_id'], d['first_name'], d['username'])} — {money(d['amount'], cfg)}")
        if status == "done":
            lines.append("\n🎉 Все рассчитались.")
        else:
            lines.append("\n⏳ ждём · 🕓 ждёт подтверждения · ⏰ отложил · ✅ скинул")
    if split.unclaimed and status != "done":
        un = ", ".join(f"{escape(it.name)} ×{n}" for it, n, _ in split.unclaimed)
        lines.append(f"\nНе выбрано: {un} — {money(split.unclaimed_total, cfg)}")
    return "\n".join(lines)


def bill_kb(bill, bot_username: str) -> InlineKeyboardMarkup | None:
    b = InlineKeyboardBuilder()
    if bill["status"] == "open":
        b.button(text="🍽 Отметить, что я ел", url=f"https://t.me/{bot_username}?start=b{bill['id']}")
        b.button(text="✅ Закрыть счёт (для платившего)", callback_data=BillA(bill=bill["id"], a="close"))
        b.adjust(1)
        return b.as_markup()
    return None


def pick_kb(bill_id: int, items: list[Item], mine: dict[int, int]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    rows = []
    for it in items:
        if it.shared:
            continue
        n = mine.get(it.id, 0)
        mark = "✅ " if n else ""
        label = f"{mark}{it.name}" + (f" {n}/{it.qty}" if it.qty > 1 else "")
        b.button(text=label[:40], callback_data=Pick(bill=bill_id, item=it.id, op="+"))
        b.button(text="−", callback_data=Pick(bill=bill_id, item=it.id, op="-"))
        b.button(text="+", callback_data=Pick(bill=bill_id, item=it.id, op="+"))
        rows.append(3)
    b.button(text="🍵 Я ел только общее", callback_data=BillA(bill=bill_id, a="shared"))
    b.button(text="✅ Готово", callback_data=BillA(bill=bill_id, a="done"))
    rows += [1, 1]
    b.adjust(*rows)
    return b.as_markup()


def debt_kb(debt_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📎 Отправить чек", callback_data=DebtA(debt=debt_id, a="receipt"))
    b.button(text="✅ Я оплатил", callback_data=DebtA(debt=debt_id, a="paid"))
    b.button(text="⏰ Отложить", callback_data=DebtA(debt=debt_id, a="later"))
    b.adjust(2, 1)
    return b.as_markup()


def later_kb(debt_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="через 1 час", callback_data=DebtA(debt=debt_id, a="l1"))
    b.button(text="через 3 часа", callback_data=DebtA(debt=debt_id, a="l3"))
    b.button(text="завтра в 10:00", callback_data=DebtA(debt=debt_id, a="lt"))
    b.adjust(3)
    return b.as_markup()


def payer_check_kb(debt_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Деньги пришли", callback_data=DebtA(debt=debt_id, a="ok"))
    b.button(text="❌ Не пришли", callback_data=DebtA(debt=debt_id, a="no"))
    return b.as_markup()


def later_until(choice: str, tz) -> dt.datetime:
    now = dt.datetime.now(tz)
    if choice == "l1":
        return now + dt.timedelta(hours=1)
    if choice == "l3":
        return now + dt.timedelta(hours=3)
    return (now + dt.timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)


# --- donations -------------------------------------------------------------------

KIND = {"server": "🖥 на сервер", "coffee": "☕ на кофе"}


def donate_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🖥 На сервер", callback_data=Don(a="kind", kind="server"))
    b.button(text="☕ Купить кофе", callback_data=Don(a="kind", kind="coffee"))
    b.button(text="🏆 Лидерборд", callback_data=Menu(a="top"))
    b.adjust(2, 1)
    return b.as_markup()


def donate_send_kb(kind: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Я отправил", callback_data=Don(a="sent", kind=kind))
    b.button(text="📎 Отправить с чеком", callback_data=Don(a="receipt", kind=kind))
    b.button(text="Отмена", callback_data=Don(a="cancel"))
    b.adjust(1)
    return b.as_markup()


def owner_don_kb(don_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=Don(a="ok", id=don_id))
    b.button(text="❌ Отменить", callback_data=Don(a="no", id=don_id))
    return b.as_markup()


def top_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Этот месяц", callback_data=Top(period="month"))
    b.button(text="Прошлый", callback_data=Top(period="prev"))
    b.button(text="За всё время", callback_data=Top(period="all"))
    return b.as_markup()


def donate_nudge_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💛 Поддержать бота", callback_data=Menu(a="donate"))
    return b.as_markup()
