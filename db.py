#!/usr/bin/env python3
"""
Syncs Delphi mainnet subgraph data into a local SQLite database.
Only fetches records newer than what's already stored (cursor = max block_number per table).
Run standalone or import sync() for use in other scripts.
"""
import sqlite3
import urllib.request
import json
import time
from datetime import datetime, timezone
from pathlib import Path

ENDPOINT = "https://api.goldsky.com/api/public/project_cmnoqdag1obop01z3efnu8ssq/subgraphs/delphi-mainnet/1.0.0/gn"
DB_PATH  = Path(__file__).parent / "delphi.db"
PAGE     = 100  # records per request


def gql(query: str) -> dict:
    body = json.dumps({"query": query}).encode()
    req  = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json", "User-Agent": "chain-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["data"]


def init_db(con: sqlite3.Connection):
    con.executescript("""
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
    """)
    for t in ("buys", "sells", "redemptions", "liquidations", "resolutions"):
        con.execute("INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES (?, 0)", (t,))
    con.commit()


def cursor(con: sqlite3.Connection, table: str) -> int:
    row = con.execute("SELECT last_block FROM sync_log WHERE table_name=?", (table,)).fetchone()
    return row[0] if row else 0


def set_cursor(con: sqlite3.Connection, table: str, block: int):
    now = datetime.now(timezone.utc).isoformat()
    con.execute("UPDATE sync_log SET last_block=?, last_synced=? WHERE table_name=?", (block, now, table))


def fetch_all_since(entity: str, fields: str, block_gt: int) -> list:
    """Paginate through all records with block_number > block_gt."""
    results, last_id = [], ""
    while True:
        id_filter = f', id_gt: "{last_id}"' if last_id else ""
        q = f"""{{
            {entity}(
                first: {PAGE},
                orderBy: block_number,
                orderDirection: asc,
                where: {{ block_number_gt: "{block_gt}"{id_filter} }}
            ) {{ {fields} }}
        }}"""
        batch = gql(q)[entity]
        results.extend(batch)
        if len(batch) < PAGE:
            break
        last_id = batch[-1]["id"]
    return results


def sync_buys(con: sqlite3.Connection) -> int:
    since = cursor(con, "buys")
    rows  = fetch_all_since("gatewayBuys",
        "id block_number timestamp_ transactionHash_ marketProxy buyer outcomeIdx tokensIn sharesOut",
        since)
    if not rows:
        return 0
    con.executemany(
        "INSERT OR IGNORE INTO buys VALUES (?,?,?,?,?,?,?,?,?)",
        [(r["id"], int(r["block_number"]), int(r["timestamp_"]), r["transactionHash_"],
          r["marketProxy"], r["buyer"], int(r["outcomeIdx"]),
          int(r["tokensIn"]), r["sharesOut"]) for r in rows]
    )
    set_cursor(con, "buys", int(rows[-1]["block_number"]))
    return len(rows)


def sync_sells(con: sqlite3.Connection) -> int:
    since = cursor(con, "sells")
    rows  = fetch_all_since("gatewaySells",
        "id block_number timestamp_ transactionHash_ marketProxy seller outcomeIdx sharesIn tokensOut",
        since)
    if not rows:
        return 0
    con.executemany(
        "INSERT OR IGNORE INTO sells VALUES (?,?,?,?,?,?,?,?,?)",
        [(r["id"], int(r["block_number"]), int(r["timestamp_"]), r["transactionHash_"],
          r["marketProxy"], r["seller"], int(r["outcomeIdx"]),
          r["sharesIn"], int(r["tokensOut"])) for r in rows]
    )
    set_cursor(con, "sells", int(rows[-1]["block_number"]))
    return len(rows)


def sync_redemptions(con: sqlite3.Connection) -> int:
    since = cursor(con, "redemptions")
    rows  = fetch_all_since("gatewayRedemptions",
        "id block_number timestamp_ transactionHash_ marketProxy redeemer sharesIn tokensOut",
        since)
    if not rows:
        return 0
    con.executemany(
        "INSERT OR IGNORE INTO redemptions VALUES (?,?,?,?,?,?,?,?)",
        [(r["id"], int(r["block_number"]), int(r["timestamp_"]), r["transactionHash_"],
          r["marketProxy"], r["redeemer"], r["sharesIn"], int(r["tokensOut"])) for r in rows]
    )
    set_cursor(con, "redemptions", int(rows[-1]["block_number"]))
    return len(rows)


def sync_liquidations(con: sqlite3.Connection) -> int:
    since = cursor(con, "liquidations")
    rows  = fetch_all_since("gatewayLiquidations",
        "id block_number timestamp_ transactionHash_ marketProxy liquidator outcomeIndices sharesIn totalTokensOut",
        since)
    if not rows:
        return 0
    con.executemany(
        "INSERT OR IGNORE INTO liquidations VALUES (?,?,?,?,?,?,?,?,?)",
        [(r["id"], int(r["block_number"]), int(r["timestamp_"]), r["transactionHash_"],
          r["marketProxy"], r["liquidator"], r["outcomeIndices"],
          r["sharesIn"], int(r["totalTokensOut"])) for r in rows]
    )
    set_cursor(con, "liquidations", int(rows[-1]["block_number"]))
    return len(rows)


def sync_resolutions(con: sqlite3.Connection) -> int:
    since = cursor(con, "resolutions")
    rows  = fetch_all_since("gatewayWinnerSubmitteds",
        "id block_number timestamp_ transactionHash_ marketProxy winningOutcomeIdx marketCreatorReward refund marketCreatorTradingFeesCut",
        since)
    if not rows:
        return 0
    con.executemany(
        "INSERT OR IGNORE INTO resolutions VALUES (?,?,?,?,?,?,?,?,?)",
        [(r["id"], int(r["block_number"]), int(r["timestamp_"]), r["transactionHash_"],
          r["marketProxy"], int(r["winningOutcomeIdx"]), int(r["marketCreatorReward"]),
          int(r["refund"]), int(r["marketCreatorTradingFeesCut"])) for r in rows]
    )
    set_cursor(con, "resolutions", int(rows[-1]["block_number"]))
    return len(rows)


def sync(verbose=True) -> dict:
    con = sqlite3.connect(DB_PATH)
    init_db(con)
    counts = {}
    for label, fn in [
        ("buys", sync_buys),
        ("sells", sync_sells),
        ("redemptions", sync_redemptions),
        ("liquidations", sync_liquidations),
        ("resolutions", sync_resolutions),
    ]:
        try:
            n = fn(con)
            counts[label] = n
            if verbose and n:
                print(f"  +{n} {label}")
        except Exception as e:
            counts[label] = 0
            if verbose:
                print(f"  ERROR syncing {label}: {e}")
    con.commit()
    con.close()
    return counts


def stats() -> dict:
    con = sqlite3.connect(DB_PATH)
    s = {}
    s["buy_volume"]   = con.execute("SELECT COALESCE(SUM(tokens_in), 0)  FROM buys").fetchone()[0]
    s["sell_volume"]  = con.execute("SELECT COALESCE(SUM(tokens_out), 0) FROM sells").fetchone()[0]
    s["redeem_volume"]= con.execute("SELECT COALESCE(SUM(tokens_out), 0) FROM redemptions").fetchone()[0]
    s["buy_count"]    = con.execute("SELECT COUNT(*) FROM buys").fetchone()[0]
    s["sell_count"]   = con.execute("SELECT COUNT(*) FROM sells").fetchone()[0]
    s["resolution_count"] = con.execute("SELECT COUNT(*) FROM resolutions").fetchone()[0]
    s["last_buy_ts"]  = con.execute("SELECT MAX(timestamp_) FROM buys").fetchone()[0]
    s["last_sell_ts"] = con.execute("SELECT MAX(timestamp_) FROM sells").fetchone()[0]
    s["unique_traders"] = con.execute(
        "SELECT COUNT(DISTINCT addr) FROM (SELECT buyer AS addr FROM buys UNION SELECT seller FROM sells)"
    ).fetchone()[0]
    con.close()
    return s


if __name__ == "__main__":
    import sys
    print(f"DB: {DB_PATH}")
    print("Syncing...")
    sync()
    s = stats()
    print(f"\nBuy volume:     ${s['buy_volume']/1e6:,.2f} USDC ({s['buy_count']} trades)")
    print(f"Sell volume:    ${s['sell_volume']/1e6:,.2f} USDC ({s['sell_count']} trades)")
    print(f"Redeem volume:  ${s['redeem_volume']/1e6:,.2f} USDC")
    print(f"Total volume:   ${(s['buy_volume']+s['sell_volume'])/1e6:,.2f} USDC")
    print(f"Unique traders: {s['unique_traders']}")
    print(f"Resolutions:    {s['resolution_count']}")
