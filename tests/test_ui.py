from decimal import Decimal
from zoneinfo import ZoneInfo

from bot import ui
from bot.config import Config
from bot.split import Item, compute

CFG = Config("t", "db", 1, "8600 0000 0000 0000", "OWNER", "https://example.com/repo", "сум", 100,
             Decimal(10), Decimal(12), 12800, ZoneInfo("Asia/Tashkent"))
ITEMS = [Item(1, "Плов", 45000, 3), Item(2, "Чай <горячий>", 12000, 1, shared=True)]
PAYER = {"id": 1, "first_name": "Босс", "username": "boss"}
PEOPLE = {1: ("Босс", "boss"), 2: ("Али", None)}


def bill(status):
    return {"id": 7, "title": "Обед & чай", "service_percent": Decimal(10), "status": status}


def test_open_bill_shows_estimates_and_escapes_html():
    claims = {1: {1: 1, 2: 2}}
    s = compute(ITEMS, claims, {1, 2}, 10, 100)
    text = ui.bill_text(CFG, bill("open"), PAYER, ITEMS, claims, PEOPLE, s)
    assert "Обед &amp; чай" in text and "Чай &lt;горячий&gt;" in text
    assert "предварительно" in text and "Не выбрано" not in text
    kb = ui.bill_kb(bill("open"), "tushlik_bot")
    assert kb.inline_keyboard[0][0].url == "https://t.me/tushlik_bot?start=b7"


def test_closed_bill_tags_debtors_with_status():
    claims = {1: {2: 1}}
    s = compute(ITEMS, claims, {1, 2}, 10, 100)
    debts = [{"user_id": 2, "first_name": "Али", "username": None, "amount": 56200, "status": "reported", "remind_at": None}]
    text = ui.bill_text(CFG, bill("closed"), PAYER, ITEMS, claims, PEOPLE, s, debts)
    assert '🕓 <a href="tg://user?id=2">Али</a> — 56 200 сум' in text
    assert "Не выбрано: Плов ×2" in text
    assert ui.bill_kb(bill("closed"), "x") is None


def test_server_goal_and_bar():
    assert CFG.server_goal_usd == Decimal("11.20")
    assert CFG.server_goal == 143360
    assert ui.bar(0, 100) == "░" * 10 + " 0%"
    assert ui.bar(150, 100) == "▓" * 10 + " 100%"


def test_callback_data_fits_telegram_limit():
    for cb in (ui.Pick(bill=2**31 - 1, item=2**31 - 1, op="+"), ui.Post(bill=2**31 - 1, chat=-1009999999999999),
               ui.DebtA(debt=2**31 - 1, a="receipt"), ui.Don(a="receipt", kind="coffee", id=2**31 - 1)):
        assert len(cb.pack().encode()) <= 64


def test_pick_keyboard_has_three_buttons_per_dish():
    kb = ui.pick_kb(7, ITEMS, {1: 2})
    assert [b.text for b in kb.inline_keyboard[0]] == ["✅ Плов 2/3", "−", "+"]
    assert kb.inline_keyboard[-1][0].text == "✅ Готово"
