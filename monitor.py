#!/usr/bin/env python3
import time
import threading
import sqlite3
import urllib.request
import json
from datetime import datetime, timezone
from pathlib import Path
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich import box

BLOCKS_API = "https://gensyn-mainnet.explorer.alchemy.com/api/v2/main-page/blocks"
STATS_API  = "https://gensyn-mainnet.explorer.alchemy.com/api/v2/stats"
RPC_URL    = "https://gensyn-mainnet.g.alchemy.com/v2/_svLE5o6Zh9quznNSK3No"
L1_RPC_URL = "https://eth-mainnet.g.alchemy.com/v2/_svLE5o6Zh9quznNSK3No"
USDC_E       = "0x5b32c997211621d55a89Cc5abAF1cC21F3A6ddF5"
BBV          = "0x2CBEE00F91A2BC50a7D5C53DFfa6BAB79d7E0243"  # BuybackVault proxy
OP_PORTAL    = "0x0280eb8c305e414d56bf2e396859c27415ba54fc"  # OptimismPortal on L1 — holds all L2 ETH
AI_TOKEN     = "0x4e742319f6b0fec4afa504fc8ed3ceab0fb751a2"  # Gensyn AI token (18 dec)
POOL         = "0x3e228359c8cE20FAE623e54b438C74420Ce30e5b"  # Uniswap V3 AI/USDC.e 0.3%
MORPHO_VAULT = "0x1b6C76fF584FBee80e4BBd7a4eB060c6C8Dd3B9F"  # Gensyn Prime USDC (gpUSDC)
DB_PATH      = Path(__file__).parent / "delphi.db"

# ── palette ──────────────────────────────────────────────────────────────
G   = "#87af87"   # sage green  (buys, positive)
R   = "#af5f75"   # dusty rose  (sells, negative)
TAN = "#d7af5f"   # tan         (neutral / totals)
DIM = "#5f5f5f"   # dim gray
WHT = "#d0d0d0"   # off-white values
HDR = "bold #d0d0d0 underline"

console = Console()
state   = {"blocks": [], "stats": {}, "error": None, "rpc": {}, "l1_eth": None}


# ── data fetching ─────────────────────────────────────────────────────────

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "chain-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def fetch_l1_eth():
    body = json.dumps([{"jsonrpc": "2.0", "method": "eth_getBalance", "params": [OP_PORTAL, "latest"], "id": 1}]).encode()
    req  = urllib.request.Request(L1_RPC_URL, data=body,
           headers={"Content-Type": "application/json", "User-Agent": "chain-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read())[0]["result"]
    return int(result, 16) / 1e18


def fetch_rpc():
    batch = [
        {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
        {"jsonrpc": "2.0", "method": "eth_syncing",     "params": [], "id": 2},
        {"jsonrpc": "2.0", "method": "net_peerCount",   "params": [], "id": 3},
        {"jsonrpc": "2.0", "method": "eth_gasPrice",    "params": [], "id": 4},
        {"jsonrpc": "2.0", "method": "eth_call",        "params": [{"to": USDC_E,       "data": "0x18160ddd"}, "latest"], "id": 5},
        {"jsonrpc": "2.0", "method": "eth_call",        "params": [{"to": USDC_E,       "data": "0x70a08231" + BBV[2:].lower().zfill(64)}, "latest"], "id": 6},
        {"jsonrpc": "2.0", "method": "eth_call",        "params": [{"to": AI_TOKEN,     "data": "0x18160ddd"}, "latest"], "id": 7},
        {"jsonrpc": "2.0", "method": "eth_call",        "params": [{"to": POOL,         "data": "0x3850c7bd"}, "latest"], "id": 8},
        {"jsonrpc": "2.0", "method": "eth_call",        "params": [{"to": POOL,         "data": "0x1a686502"}, "latest"], "id": 9},
        {"jsonrpc": "2.0", "method": "eth_call",        "params": [{"to": MORPHO_VAULT, "data": "0x01e1d114"}, "latest"], "id": 10},
        {"jsonrpc": "2.0", "method": "eth_call",        "params": [{"to": MORPHO_VAULT, "data": "0x18160ddd"}, "latest"], "id": 11},
    ]
    body = json.dumps(batch).encode()
    req  = urllib.request.Request(RPC_URL, data=body,
           headers={"Content-Type": "application/json", "User-Agent": "chain-monitor/1.0"})
    t0   = time.monotonic()
    with urllib.request.urlopen(req, timeout=10) as r:
        results = json.loads(r.read())
    latency = (time.monotonic() - t0) * 1000  # ms
    by_id   = {x["id"]: x.get("result") for x in results}
    return {
        "latency_ms":  round(latency, 1),
        "block":       int(by_id[1], 16) if by_id.get(1) else None,
        "syncing":     by_id.get(2),
        "peers":       int(by_id[3], 16) if by_id.get(3) else None,
        "gas_price":   int(by_id[4], 16) if by_id.get(4) else None,
        "usdc_e":      int(by_id[5], 16) / 1e6  if by_id.get(5) else None,
        "bbv_usdc":    int(by_id[6], 16) / 1e6  if by_id.get(6) else None,
        "ai_supply":   int(by_id[7], 16) / 1e18 if by_id.get(7) else None,
        "pool_sqrt":   int(by_id[8][2:66], 16)  if (by_id.get(8) and len(by_id[8]) >= 66) else None,
        "pool_liq":    int(by_id[9], 16)         if by_id.get(9) else None,
        "morpho_tvl":  int(by_id[10], 16) / 1e6 if by_id.get(10) else None,
        "morpho_sup":  int(by_id[11], 16) / 1e6 if by_id.get(11) else None,
        "error":       None,
        "ts":          time.time(),
    }


def fetch_loop():
    from db import sync
    tick = 0
    while True:
        try:
            state["blocks"] = get(BLOCKS_API)
            state["error"]  = None
        except Exception as e:
            state["error"] = str(e)
        try:
            state["rpc"] = fetch_rpc()
        except Exception as e:
            state["rpc"] = {"error": str(e), "latency_ms": None}
        try:
            state["l1_eth"] = fetch_l1_eth()
        except Exception:
            pass
        if tick % 15 == 0:
            try:
                state["stats"] = get(STATS_API)
            except Exception:
                pass
            try:
                sync(verbose=False)
            except Exception:
                pass
        tick += 1
        time.sleep(2)


# ── helpers ───────────────────────────────────────────────────────────────

def fmt(val, fallback="—"):
    if val is None:
        return fallback
    try:
        return f"{int(val):,}"
    except Exception:
        return str(val)


def bar(filled_pct, width=28, color=G):
    n = max(0, min(width, round(filled_pct / 100 * width)))
    return Text("█" * n + " " * (width - n), style=f"{color}")


def db_stats():
    if not DB_PATH.exists():
        return {}
    con = sqlite3.connect(DB_PATH)
    s = {}
    s["buy_vol"]   = con.execute("SELECT COALESCE(SUM(tokens_in),0)  FROM buys").fetchone()[0]
    s["sell_vol"]  = con.execute("SELECT COALESCE(SUM(tokens_out),0) FROM sells").fetchone()[0]
    s["redm_vol"]  = con.execute("SELECT COALESCE(SUM(tokens_out),0) FROM redemptions").fetchone()[0]
    s["buy_n"]     = con.execute("SELECT COUNT(*) FROM buys").fetchone()[0]
    s["sell_n"]    = con.execute("SELECT COUNT(*) FROM sells").fetchone()[0]
    s["redm_n"]    = con.execute("SELECT COUNT(*) FROM redemptions").fetchone()[0]
    s["traders"]   = con.execute(
        "SELECT COUNT(DISTINCT a) FROM (SELECT buyer AS a FROM buys UNION SELECT seller FROM sells)"
    ).fetchone()[0]
    s["last_buy"]  = con.execute("SELECT MAX(timestamp_) FROM buys").fetchone()[0]
    s["resolutions"] = con.execute("SELECT COUNT(*) FROM resolutions").fetchone()[0]
    # recent trades
    s["recent"] = con.execute("""
        SELECT 'BUY'  AS side, timestamp_, buyer    AS addr, market_proxy, tokens_in  AS usdc FROM buys
        UNION ALL
        SELECT 'SELL' AS side, timestamp_, seller   AS addr, market_proxy, tokens_out AS usdc FROM sells
        ORDER BY timestamp_ DESC LIMIT 8
    """).fetchall()
    con.close()
    return s


# ── layout helpers ────────────────────────────────────────────────────────

def make_chain_table(latest, stats):
    gas_pct  = latest.get("gas_used_percentage") or 0
    blk_ts   = latest.get("timestamp", "")
    blk_age  = "—"
    if blk_ts:
        try:
            t = datetime.fromisoformat(blk_ts.replace("Z", "+00:00"))
            s = int((datetime.now(timezone.utc) - t).total_seconds())
            blk_age = f"{s}s ago"
        except Exception:
            pass

    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style=WHT, min_width=18)
    t.add_column(style=WHT, justify="right", min_width=12)

    rows = [
        ("Latest Block",    fmt(latest.get("height")),               G),
        ("Block Age",       blk_age,                                 DIM),
        ("Txs in Block",    fmt(latest.get("transactions_count")),    TAN),
        ("Gas Used",        fmt(latest.get("gas_used")),              G),
        ("Block Util %",    f"{gas_pct:.4f}%",                        G),
        ("Txs Today",       fmt(stats.get("transactions_today")),     TAN),
        ("Total Txs",       fmt(stats.get("total_transactions")),     G),
        ("Total Addresses", fmt(stats.get("total_addresses")),        G),
    ]
    for label, val, color in rows:
        t.add_row(Text(label, style=color), Text(val, style=f"bold {WHT}"))
    return Panel(t, title="[bold #d0d0d0]Chain[/]", border_style="#3a3a3a", padding=(0, 1))


def make_rpc_table(rpc, blocks):
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style=WHT, min_width=14)
    t.add_column(style=WHT, justify="right", min_width=16)
    t.add_column(min_width=16)

    if rpc.get("error"):
        t.add_row(Text("Status", style=R), Text("ERROR", style=f"bold {R}"), Text(""))
        return Panel(t, title="[bold #d0d0d0]RPC Health[/]", border_style="#3a3a3a", padding=(0, 1))

    rpc_block  = rpc.get("block")
    expl_block = blocks[0].get("height") if blocks else None
    lag        = (expl_block - rpc_block) if (rpc_block and expl_block) else None
    lat        = rpc.get("latency_ms")
    peers      = rpc.get("peers")
    syncing    = rpc.get("syncing")
    gas_wei    = rpc.get("gas_price")

    lat_color  = G if lat and lat < 200 else (TAN if lat and lat < 500 else R)
    lag_color  = G if lag is not None and lag <= 2 else (TAN if lag is not None and lag <= 5 else R)
    peer_color = G if peers and peers >= 5 else (TAN if peers and peers >= 1 else R)
    lat_pct    = min((lat or 0) / 1000 * 100, 100)

    t.add_row(Text("Status",    style=G),
              Text("HEALTHY" if not syncing else "SYNCING", style=f"bold {G if not syncing else TAN}"),
              Text(""))
    t.add_row(Text("Latency",   style=lat_color),
              Text(f"{lat} ms" if lat else "—", style=f"bold {WHT}"),
              bar(lat_pct, width=14, color=lat_color))
    t.add_row(Text("RPC Block", style=WHT),
              Text(fmt(rpc_block), style=f"bold {WHT}"),
              Text(""))
    t.add_row(Text("Block Lag", style=lag_color),
              Text(f"{lag} blks" if lag is not None else "—", style=f"bold {WHT}"),
              Text(""))
    t.add_row(Text("Peers",     style=peer_color),
              Text(str(peers) if peers is not None else "—", style=f"bold {WHT}"),
              Text(""))
    t.add_row(Text("Gas Price", style=WHT),
              Text(f"{gas_wei/1e9:.4f} gwei" if gas_wei else "—", style=f"bold {WHT}"),
              Text(""))
    return Panel(t, title="[bold #d0d0d0]RPC Health[/]", border_style="#3a3a3a", padding=(0, 1))


def make_delphi_table(ds):
    bv        = ds.get("buy_vol", 0)
    sv        = ds.get("sell_vol", 0)
    rv        = ds.get("redm_vol", 0)
    total_vol = (bv + sv) or 1

    t = Table(box=None, show_header=True, header_style=HDR, padding=(0, 2))
    t.add_column("Activity",  style=WHT, min_width=14)
    t.add_column("USDC",      style=WHT, justify="right", min_width=10)
    t.add_column("N",         style=WHT, justify="right", min_width=4)
    t.add_column("",          min_width=16)

    t.add_row(Text("Buys",         style=G),
              Text(f"${bv/1e6:,.2f}", style=f"bold {WHT}"),
              Text(str(ds.get("buy_n", 0)), style=WHT),
              bar(bv / total_vol * 100, width=14, color=G))
    t.add_row(Text("Sells",        style=R),
              Text(f"${sv/1e6:,.2f}", style=f"bold {WHT}"),
              Text(str(ds.get("sell_n", 0)), style=WHT),
              bar(sv / total_vol * 100, width=14, color=R))
    t.add_row(Text("Redemptions",  style=TAN),
              Text(f"${rv/1e6:,.2f}", style=f"bold {WHT}"),
              Text(str(ds.get("redm_n", 0)), style=WHT),
              bar(rv / total_vol * 100, width=14, color=TAN))
    t.add_row(Text(""),            Text(""),  Text(""), Text(""))
    t.add_row(Text("Total Vol",    style=WHT),
              Text(f"${(bv+sv)/1e6:,.2f}", style=f"bold {WHT}"),
              Text(str(ds.get("buy_n",0)+ds.get("sell_n",0)), style=WHT),
              Text(""))
    t.add_row(Text("Traders",      style=WHT),
              Text(str(ds.get("traders", 0)), style=f"bold {WHT}"),
              Text(""), Text(""))
    t.add_row(Text("Resolutions",  style=WHT),
              Text(str(ds.get("resolutions", 0)), style=f"bold {WHT}"),
              Text(""), Text(""))
    return Panel(t, title="[bold #d0d0d0]Delphi Markets[/]", border_style="#3a3a3a", padding=(0, 1))


def make_trades_table(ds):
    t = Table(box=None, show_header=True, header_style=HDR, padding=(0, 2))
    t.add_column("Side",   min_width=5)
    t.add_column("Time",   style=DIM, min_width=16)
    t.add_column("Wallet", style=DIM, min_width=12)
    t.add_column("USDC",   justify="right", min_width=8)

    for side, ts, addr, market, usdc in (ds.get("recent") or []):
        color = G if side == "BUY" else R
        t_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m/%d %H:%M:%S")
        t.add_row(Text(side,  style=f"bold {color}"),
                  Text(t_str, style=DIM),
                  Text(addr[:6] + "…" + addr[-4:], style=DIM),
                  Text(f"${usdc/1e6:,.2f}", style=f"bold {WHT}"))
    return Panel(t, title="[bold #d0d0d0]Recent Trades[/]", border_style="#3a3a3a", padding=(0, 1))


def make_tokens_panel(rpc):
    usdc_e   = rpc.get("usdc_e")
    bbv_usdc = rpc.get("bbv_usdc")
    ai_supply = rpc.get("ai_supply")
    l1_eth   = state["l1_eth"]

    t = Table(box=None, show_header=True, header_style=HDR, padding=(0, 2))
    t.add_column("Token",   style=WHT, min_width=20)
    t.add_column("Value",   style=WHT, justify="right", min_width=16)
    t.add_column("Note",    style=DIM, min_width=20)

    t.add_row(Text("ETH Supply",     style=G),
              Text(f"{l1_eth:.4f} ETH" if l1_eth is not None else "—", style=f"bold {WHT}"),
              Text("via L1 portal"))
    t.add_row(Text("USDC.e Supply",  style=TAN),
              Text(f"${usdc_e:,.2f}" if usdc_e is not None else "—", style=f"bold {WHT}"),
              Text("total on-chain"))
    t.add_row(Text("AI Supply",      style=G),
              Text(f"{ai_supply:,.0f}" if ai_supply is not None else "—", style=f"bold {WHT}"),
              Text("Gensyn AI token"))
    t.add_row(Text(""), Text(""), Text(""))
    t.add_row(Text("Buy Burn Vault", style=DIM),
              Text(""),
              Text("0x2CBE…0243", style=DIM))
    t.add_row(Text("USDC.e Supply",  style=TAN),
              Text(f"${bbv_usdc:,.2f}" if bbv_usdc is not None else "—", style=f"bold {WHT}"),
              Text("in vault"))

    return Panel(t, title="[bold #d0d0d0]Tokens[/]", border_style="#3a3a3a", padding=(0, 1))


def make_pool_panel(rpc):
    sqrt    = rpc.get("pool_sqrt")
    liq     = rpc.get("pool_liq")
    ai_sup  = rpc.get("ai_supply")

    active  = liq is not None and liq > 0
    if sqrt and sqrt > 0:
        price = (sqrt / (2 ** 96)) ** 2 * 1e12  # token0=AI 18dec, token1=USDC.e 6dec
    else:
        price = None

    t = Table(box=None, show_header=True, header_style=HDR, padding=(0, 2))
    t.add_column("Metric",    style=WHT, min_width=18)
    t.add_column("Value",     style=WHT, justify="right", min_width=16)
    t.add_column("Note",      style=DIM, min_width=14)

    status_color = G if active else DIM
    t.add_row(Text("Status",      style=status_color),
              Text("ACTIVE" if active else "INACTIVE", style=f"bold {status_color}"),
              Text("AI/USDC.e 0.3%"))
    t.add_row(Text("AI Price",    style=TAN),
              Text(f"${price:.6f}" if price else "—", style=f"bold {WHT}"),
              Text("per AI token"))
    t.add_row(Text("Liquidity",   style=WHT),
              Text(fmt(liq) if liq is not None else "—", style=f"bold {WHT}"),
              Text("in-range LP"))
    t.add_row(Text("AI Supply",   style=G),
              Text(f"{ai_sup/1e9:.3f}B" if ai_sup is not None else "—", style=f"bold {WHT}"),
              Text("total circulating"))

    return Panel(t, title="[bold #d0d0d0]Uniswap V3 Pool[/]", border_style="#3a3a3a", padding=(0, 1))


def make_morpho_panel(rpc):
    tvl = rpc.get("morpho_tvl")
    sup = rpc.get("morpho_sup")

    t = Table(box=None, show_header=True, header_style=HDR, padding=(0, 2))
    t.add_column("Metric",    style=WHT, min_width=18)
    t.add_column("Value",     style=WHT, justify="right", min_width=16)
    t.add_column("Note",      style=DIM, min_width=14)

    t.add_row(Text("Vault",       style=DIM),
              Text("gpUSDC",      style=f"bold {WHT}"),
              Text("Gensyn Prime"))
    t.add_row(Text("TVL",         style=TAN),
              Text(f"${tvl:,.2f}" if tvl is not None else "—", style=f"bold {WHT}"),
              Text("USDC.e assets"))
    if tvl is not None and sup is not None and sup > 0:
        nav = tvl / sup
        t.add_row(Text("NAV / share",  style=WHT),
                  Text(f"${nav:.4f}", style=f"bold {WHT}"),
                  Text("USDC.e/gpUSDC"))

    return Panel(t, title="[bold #d0d0d0]Morpho Vault[/]", border_style="#3a3a3a", padding=(0, 1))


def make_blocks_table(blocks):
    t = Table(box=None, show_header=True, header_style=HDR, padding=(0, 2))
    t.add_column("Block",    justify="right", min_width=10)
    t.add_column("Txs",      justify="right", min_width=4)
    t.add_column("Gas Used", justify="right", min_width=10)
    t.add_column("Util %",   justify="right", min_width=7)
    t.add_column("Size",     justify="right", min_width=7)
    t.add_column("",         min_width=20)

    for b in blocks[:8]:
        pct = b.get("gas_used_percentage") or 0
        t.add_row(Text(str(b.get("height", "—")),             style=f"bold {G}"),
                  Text(str(b.get("transactions_count", "—")), style=WHT),
                  Text(fmt(b.get("gas_used")),                style=WHT),
                  Text(f"{pct:.3f}%",                         style=TAN),
                  Text(fmt(b.get("size")),                    style=DIM),
                  bar(pct * 100, width=18, color=G))
    return Panel(t, title="[bold #d0d0d0]Recent Blocks[/]", border_style="#3a3a3a", padding=(0, 1))


# ── layout ────────────────────────────────────────────────────────────────

def build():
    blocks = state["blocks"]
    stats  = state["stats"]
    error  = state["error"]
    rpc    = state["rpc"]
    now    = datetime.now(timezone.utc).strftime("%b %d, %H:%M:%S UTC")
    latest = blocks[0] if blocks else {}
    ds     = db_stats()

    root = Table.grid(expand=True, padding=(0, 0))
    root.add_column()

    # ── header ──
    hdr = Table.grid(expand=True)
    hdr.add_column()
    hdr.add_column(justify="right")
    hdr.add_row(Text("GENSYN CHAIN MONITOR", style=f"bold {WHT}"),
                Text(f"Last updated: {now}", style=DIM))
    root.add_row(hdr)
    root.add_row(Text(""))

    # ── row 1: chain | rpc | tokens ──
    row1 = Table.grid(expand=True, padding=(0, 1))
    row1.add_column(ratio=2)
    row1.add_column(ratio=2)
    row1.add_column(ratio=2)
    row1.add_row(make_chain_table(latest, stats),
                 make_rpc_table(rpc, blocks),
                 make_tokens_panel(rpc))
    root.add_row(row1)

    # ── row 2: delphi | recent trades ──
    row2 = Table.grid(expand=True, padding=(0, 1))
    row2.add_column(ratio=1)
    row2.add_column(ratio=1)
    row2.add_row(make_delphi_table(ds),
                 make_trades_table(ds))
    root.add_row(row2)

    # ── row 3: uniswap pool | morpho vault ──
    row3 = Table.grid(expand=True, padding=(0, 1))
    row3.add_column(ratio=1)
    row3.add_column(ratio=1)
    row3.add_row(make_pool_panel(rpc),
                 make_morpho_panel(rpc))
    root.add_row(row3)

    # ── row 4: recent blocks (full width) ──
    root.add_row(make_blocks_table(blocks))

    # ── footer ──
    online = "● ONLINE" if not error else f"● {error[:50]}"
    foot = Table.grid(expand=True)
    foot.add_column()
    foot.add_column(justify="right")
    foot.add_row(Text("gensyn-mainnet.explorer.alchemy.com  ·  Goldsky/delphi-mainnet", style=DIM),
                 Text(online, style=f"bold {G}" if not error else f"bold {R}"))
    root.add_row(foot)

    return root


# ── main ──────────────────────────────────────────────────────────────────

def main():
    t = threading.Thread(target=fetch_loop, daemon=True)
    t.start()
    time.sleep(0.8)

    with Live(build(), refresh_per_second=2, screen=True) as live:
        while True:
            time.sleep(0.5)
            live.update(build())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n")
