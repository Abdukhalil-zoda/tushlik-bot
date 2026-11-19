CREATE TABLE users (
    id           BIGINT PRIMARY KEY,           -- Telegram user id
    username     TEXT,
    first_name   TEXT NOT NULL DEFAULT '',
    card_number  TEXT,                         -- payer's card, shown only in private chats
    card_holder  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chats (
    id        BIGINT PRIMARY KEY,              -- Telegram group id
    title     TEXT NOT NULL DEFAULT '',
    active    BOOLEAN NOT NULL DEFAULT TRUE,
    added_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- draft -> open (posted, people pick dishes) -> closed (debts sent) -> done (everyone paid); cancelled
CREATE TABLE bills (
    id               SERIAL PRIMARY KEY,
    payer_id         BIGINT NOT NULL REFERENCES users(id),
    chat_id          BIGINT REFERENCES chats(id),
    title            TEXT NOT NULL,
    service_percent  NUMERIC(5,2) NOT NULL DEFAULT 0,
    receipt_total    BIGINT,
    status           TEXT NOT NULL DEFAULT 'draft',
    group_message_id BIGINT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ
);

CREATE TABLE bill_items (
    id          SERIAL PRIMARY KEY,
    bill_id     INT NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    position    INT NOT NULL,
    name        TEXT NOT NULL,
    unit_price  BIGINT NOT NULL,
    qty         INT NOT NULL DEFAULT 1,
    shared      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE participants (
    bill_id    INT NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL REFERENCES users(id),
    joined_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bill_id, user_id)
);

CREATE TABLE claims (
    item_id  INT NOT NULL REFERENCES bill_items(id) ON DELETE CASCADE,
    user_id  BIGINT NOT NULL REFERENCES users(id),
    units    INT NOT NULL,
    PRIMARY KEY (item_id, user_id)
);

-- pending -> reported (debtor says paid) -> confirmed (payer saw the money); rejected goes back to pending
CREATE TABLE debts (
    id               SERIAL PRIMARY KEY,
    bill_id          INT NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    user_id          BIGINT NOT NULL REFERENCES users(id),
    amount           BIGINT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    remind_at        TIMESTAMPTZ,
    last_ping_at     TIMESTAMPTZ,
    receipt_chat_id  BIGINT,
    receipt_msg_id   BIGINT,
    reported_at      TIMESTAMPTZ,
    confirmed_at     TIMESTAMPTZ,
    UNIQUE (bill_id, user_id)
);
CREATE INDEX debts_remind ON debts (remind_at) WHERE remind_at IS NOT NULL;

-- kind: server | coffee; status: pending -> confirmed | rejected
CREATE TABLE donations (
    id               SERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES users(id),
    kind             TEXT NOT NULL,
    amount           BIGINT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    receipt_chat_id  BIGINT,
    receipt_msg_id   BIGINT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at       TIMESTAMPTZ
);
CREATE INDEX donations_month ON donations (created_at) WHERE status = 'confirmed';

-- What the owner paid for the server himself because donations did not reach the goal.
CREATE TABLE server_covers (
    month       DATE PRIMARY KEY,              -- first day of the month
    goal        BIGINT NOT NULL,
    collected   BIGINT NOT NULL,
    covered     BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
INSERT INTO meta VALUES ('started_month', to_char(now() AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM-01'));
