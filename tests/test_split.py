import json
import math
from pathlib import Path

import pytest

from bot.bill_schema import BillError, parse
from bot.split import Item, bill_total, compute, round_up

PLOV = Item(1, "Плов", 45000, 3)
LAGMAN = Item(2, "Лагман", 38000, 1)
TEA = Item(3, "Чай", 12000, 1, shared=True)
BREAD = Item(4, "Хлеб", 4000, 2, shared=True)
MENU = [PLOV, LAGMAN, TEA, BREAD]
ALI, VALI, SAMI = 1, 2, 3


def test_everyone_pays_own_dishes_plus_equal_shared_and_service():
    s = compute(MENU, {1: {ALI: 1, VALI: 2}, 2: {SAMI: 1}}, set(), 10)
    shared = (12000 + 8000) / 3
    assert s.amounts[ALI] == math.ceil((45000 + shared) * 1.1)  # 56 833.33 -> 56 834: rounding is up
    assert s.amounts[VALI] == math.ceil((90000 + shared) * 1.1)
    assert s.amounts[SAMI] == math.ceil((38000 + shared) * 1.1)
    assert not s.unclaimed
    assert sum(s.exact.values()) == bill_total(MENU, 10)


def test_one_dish_for_three_is_split_proportionally():
    pizza = Item(9, "Пицца", 90000, 1)
    s = compute([pizza], {9: {ALI: 1, VALI: 1, SAMI: 1}}, set())
    assert s.amounts == {ALI: 30000, VALI: 30000, SAMI: 30000}


def test_unpicked_portions_stay_unclaimed():
    s = compute(MENU, {1: {ALI: 1}}, set())
    left = {it.name: n for it, n, _ in s.unclaimed}
    assert left == {"Плов": 2, "Лагман": 1}
    assert s.unclaimed_total == 90000 + 38000


def test_only_shared_participant_pays_part_of_tea():
    s = compute(MENU, {1: {ALI: 3}, 2: {VALI: 1}}, {SAMI})
    assert s.amounts[SAMI] == math.ceil((12000 + 8000) / 3)


def test_shared_without_people_is_unclaimed():
    s = compute([TEA], {}, set())
    assert s.unclaimed and s.unclaimed_total == 12000


def test_rounding_is_up():
    assert round_up(1, 100) == 100
    assert round_up(100, 100) == 100
    assert round_up(101, 1000) == 1000
    s = compute([Item(1, "Суп", 10001)], {1: {ALI: 1}}, set(), 0, 100)
    assert s.amounts[ALI] == 10100


def test_example_file_parses_and_total_matches():
    d = parse(Path(__file__).parent.parent.joinpath("examples/bill.json").read_text())
    items = [Item(i, x.name, x.unit_price, x.qty, x.shared) for i, x in enumerate(d.items)]
    assert bill_total(items, d.service_percent) == d.total


@pytest.mark.parametrize("raw, needle", [
    ("not json", "не JSON"),
    ("[]", "объект"),
    ("{}", "нет ни одного блюда"),
    ('{"items": [{"name": "Плов"}]}', "не число"),
    ('{"items": [{"name": "Плов", "price": -5}]}', "больше нуля"),
    ('{"items": [{"name": "Плов", "price": 5, "qty": 0}]}', "qty"),
    ('{"items": [{"name": "Плов", "price": 5}], "service_percent": 80}', "service_percent"),
])
def test_bad_bills_explain_the_problem(raw, needle):
    with pytest.raises(BillError) as e:
        parse(raw)
    assert any(needle in p for p in e.value.problems)


def test_prices_may_be_strings_or_floats():
    d = parse(json.dumps({"items": [{"name": "Сомса", "price": "7000"}, {"name": "Чай", "price": 3000.4}]}))
    assert [i.unit_price for i in d.items] == [7000, 3000]
