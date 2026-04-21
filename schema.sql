CREATE TABLE IF NOT EXISTS buys (
    id              TEXT PRIMARY KEY,
    block_number    INTEGER,
    timestamp_      INTEGER,
    tx_hash         TEXT,
    market_proxy    TEXT,
    buyer           TEXT,
    outcome_idx     INTEGER,
    tokens_in       INTEGER,
    shares_out      TEXT
);
CREATE TABLE IF NOT EXISTS sells (
    id              TEXT PRIMARY KEY,
    block_number    INTEGER,
    timestamp_      INTEGER,
    tx_hash         TEXT,
    market_proxy    TEXT,
    seller          TEXT,
    outcome_idx     INTEGER,
    shares_in       TEXT,
    tokens_out      INTEGER
);
CREATE TABLE IF NOT EXISTS redemptions (
    id              TEXT PRIMARY KEY,
    block_number    INTEGER,
    timestamp_      INTEGER,
    tx_hash         TEXT,
    market_proxy    TEXT,
    redeemer        TEXT,
    shares_in       TEXT,
    tokens_out      INTEGER
);
CREATE TABLE IF NOT EXISTS liquidations (
    id              TEXT PRIMARY KEY,
    block_number    INTEGER,
    timestamp_      INTEGER,
    tx_hash         TEXT,
    market_proxy    TEXT,
    liquidator      TEXT,
    outcome_indices TEXT,
    shares_in       TEXT,
    total_tokens_out INTEGER
);
CREATE TABLE IF NOT EXISTS resolutions (
    id                          TEXT PRIMARY KEY,
    block_number                INTEGER,
    timestamp_                  INTEGER,
    tx_hash                     TEXT,
    market_proxy                TEXT,
    winning_outcome_idx         INTEGER,
    market_creator_reward       INTEGER,
    refund                      INTEGER,
    market_creator_trading_fees INTEGER
);
CREATE TABLE IF NOT EXISTS sync_log (
    table_name  TEXT PRIMARY KEY,
    last_block  INTEGER DEFAULT 0,
    last_synced TEXT
);
INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES ('buys',        0);
INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES ('sells',       0);
INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES ('redemptions', 0);
INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES ('liquidations',0);
INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES ('resolutions', 0);
