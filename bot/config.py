from __future__ import annotations

import math
import os
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    bot_token: str
    database_url: str
    owner_id: int                 # receives donations and confirms them
    owner_card: str
    owner_card_holder: str
    repo_url: str
    currency: str                 # label shown after amounts
    round_to: int                 # every share is rounded up to this step
    server_cost_usd: Decimal
    server_tax_percent: Decimal
    usd_rate: int                 # 1 USD in `currency`, for the server goal
    tz: ZoneInfo

    @property
    def server_goal_usd(self) -> Decimal:
        return (self.server_cost_usd * (1 + self.server_tax_percent / 100)).quantize(Decimal("0.01"))

    @property
    def server_goal(self) -> int:
        return math.ceil(self.server_goal_usd * self.usd_rate)


def load() -> Config:
    e = os.environ
    return Config(
        bot_token=e["BOT_TOKEN"],
        database_url=e["DATABASE_URL"],
        owner_id=int(e["OWNER_ID"]),
        owner_card=e.get("OWNER_CARD", ""),
        owner_card_holder=e.get("OWNER_CARD_HOLDER", ""),
        repo_url=e.get("REPO_URL", "https://github.com/Abdukhalil-zoda/tushlik-bot"),
        currency=e.get("CURRENCY", "сум"),
        round_to=int(e.get("ROUND_TO", "100")),
        server_cost_usd=Decimal(e.get("SERVER_COST_USD", "10")),
        server_tax_percent=Decimal(e.get("SERVER_TAX_PERCENT", "12")),
        usd_rate=int(e.get("USD_RATE", "12800")),
        tz=ZoneInfo(e.get("TZ", "Asia/Tashkent")),
    )
