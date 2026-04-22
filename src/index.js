// Cloudflare Worker — Gensyn Chain Monitor
// Serves the web dashboard (static assets) + /api/data + cron Delphi sync

const BLOCKS_API  = 'https://gensyn-mainnet.explorer.alchemy.com/api/v2/main-page/blocks';
const STATS_API   = 'https://gensyn-mainnet.explorer.alchemy.com/api/v2/stats';
const GOLDSKY_URL = 'https://api.goldsky.com/api/public/project_cmnoqdag1obop01z3efnu8ssq/subgraphs/delphi-mainnet/1.0.0/gn';
const PUBLIC_RPC  = 'https://gensyn-mainnet.g.alchemy.com/public';

const USDC_E       = '0x5b32c997211621d55a89Cc5abAF1cC21F3A6ddF5';
const BBV          = '0x2CBEE00F91A2BC50a7D5C53DFfa6BAB79d7E0243';
const OP_PORTAL    = '0x0280eb8c305e414d56bf2e396859c27415ba54fc';
const AI_TOKEN     = '0x4e742319f6b0fec4afa504fc8ed3ceab0fb751a2';
const POOL         = '0xf3f77fb85a74f49a3dcb082347d7fefa8aba596f'; // WETH/USDC.e 0.3%
const MORPHO_VAULT = '0x1b6C76fF584FBee80e4BBd7a4eB060c6C8Dd3B9F';

// ── helpers ───────────────────────────────────────────────────────────────

const pad   = (addr) => addr.slice(2).toLowerCase().padStart(64, '0');
const hexN  = (hex)  => hex ? parseInt(hex, 16) : null;

async function getJson(url) {
  const r = await fetch(url, { headers: { 'User-Agent': 'chain-monitor/1.0' } });
  return r.json();
}

// ── RPC batch call ────────────────────────────────────────────────────────

async function fetchRpc(env) {
  const rpcUrl = env.RPC_URL || PUBLIC_RPC;
  const batch = [
    { jsonrpc: '2.0', method: 'eth_blockNumber', params: [],       id: 1 },
    { jsonrpc: '2.0', method: 'eth_syncing',     params: [],       id: 2 },
    { jsonrpc: '2.0', method: 'net_peerCount',   params: [],       id: 3 },
    { jsonrpc: '2.0', method: 'eth_gasPrice',    params: [],       id: 4 },
    { jsonrpc: '2.0', method: 'eth_call', params: [{ to: USDC_E,       data: '0x18160ddd' },             'latest'], id: 5 },
    { jsonrpc: '2.0', method: 'eth_call', params: [{ to: USDC_E,       data: '0x70a08231' + pad(BBV) },  'latest'], id: 6 },
    { jsonrpc: '2.0', method: 'eth_call', params: [{ to: AI_TOKEN,     data: '0x18160ddd' },             'latest'], id: 7 },
    { jsonrpc: '2.0', method: 'eth_call', params: [{ to: POOL,         data: '0x3850c7bd' },             'latest'], id: 8 },
    { jsonrpc: '2.0', method: 'eth_call', params: [{ to: POOL,         data: '0x1a686502' },             'latest'], id: 9 },
    { jsonrpc: '2.0', method: 'eth_call', params: [{ to: MORPHO_VAULT, data: '0x01e1d114' },             'latest'], id: 10 },
    { jsonrpc: '2.0', method: 'eth_call', params: [{ to: MORPHO_VAULT, data: '0x18160ddd' },             'latest'], id: 11 },
  ];

  const t0 = Date.now();
  const r  = await fetch(rpcUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'User-Agent': 'chain-monitor/1.0' },
    body: JSON.stringify(batch),
  });
  const latency = Date.now() - t0;
  const results = await r.json();
  const byId    = Object.fromEntries(results.map(x => [x.id, x.result]));

  // sqrtPriceX96 is uint160 — use BigInt to avoid float precision loss
  let pool_price = null;
  if (byId[8] && byId[8].length >= 66) {
    const sqrtBig = BigInt('0x' + byId[8].slice(2, 66));
    if (sqrtBig > 0n) {
      const sqrt = Number(sqrtBig) / Math.pow(2, 96);
      pool_price = sqrt * sqrt * 1e12; // token0=WETH 18dec, token1=USDC.e 6dec
    }
  }

  return {
    latency_ms:  latency,
    block:       hexN(byId[1]),
    syncing:     byId[2],
    peers:       hexN(byId[3]),
    gas_price:   hexN(byId[4]),
    usdc_e:      byId[5]  ? hexN(byId[5])  / 1e6  : null,
    bbv_usdc:    byId[6]  ? hexN(byId[6])  / 1e6  : null,
    ai_supply:   byId[7]  ? hexN(byId[7])  / 1e18 : null,
    pool_price,
    pool_liq:    byId[9]  ? hexN(byId[9])         : null,
    morpho_tvl:  byId[10] ? hexN(byId[10]) / 1e6  : null,
    morpho_sup:  byId[11] ? hexN(byId[11]) / 1e6  : null,
  };
}

// ── L1 ETH supply (OptimismPortal balance) ────────────────────────────────

async function fetchL1Eth(env) {
  if (!env.L1_RPC_URL) return null;
  const r = await fetch(env.L1_RPC_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'User-Agent': 'chain-monitor/1.0' },
    body: JSON.stringify([{ jsonrpc: '2.0', method: 'eth_getBalance', params: [OP_PORTAL, 'latest'], id: 1 }]),
  });
  const data = await r.json();
  return hexN(data[0].result) / 1e18;
}

// ── Delphi stats from D1 ──────────────────────────────────────────────────

async function ensureSchema(db) {
  await db.batch([
    db.prepare(`CREATE TABLE IF NOT EXISTS buys (id TEXT PRIMARY KEY, block_number INTEGER, timestamp_ INTEGER, tx_hash TEXT, market_proxy TEXT, buyer TEXT, outcome_idx INTEGER, tokens_in INTEGER, shares_out TEXT)`),
    db.prepare(`CREATE TABLE IF NOT EXISTS sells (id TEXT PRIMARY KEY, block_number INTEGER, timestamp_ INTEGER, tx_hash TEXT, market_proxy TEXT, seller TEXT, outcome_idx INTEGER, shares_in TEXT, tokens_out INTEGER)`),
    db.prepare(`CREATE TABLE IF NOT EXISTS redemptions (id TEXT PRIMARY KEY, block_number INTEGER, timestamp_ INTEGER, tx_hash TEXT, market_proxy TEXT, redeemer TEXT, shares_in TEXT, tokens_out INTEGER)`),
    db.prepare(`CREATE TABLE IF NOT EXISTS liquidations (id TEXT PRIMARY KEY, block_number INTEGER, timestamp_ INTEGER, tx_hash TEXT, market_proxy TEXT, liquidator TEXT, outcome_indices TEXT, shares_in TEXT, total_tokens_out INTEGER)`),
    db.prepare(`CREATE TABLE IF NOT EXISTS resolutions (id TEXT PRIMARY KEY, block_number INTEGER, timestamp_ INTEGER, tx_hash TEXT, market_proxy TEXT, winning_outcome_idx INTEGER, market_creator_reward INTEGER, refund INTEGER, market_creator_trading_fees INTEGER)`),
    db.prepare(`CREATE TABLE IF NOT EXISTS sync_log (table_name TEXT PRIMARY KEY, last_block INTEGER DEFAULT 0, last_synced TEXT)`),
    db.prepare(`INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES ('buys', 0)`),
    db.prepare(`INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES ('sells', 0)`),
    db.prepare(`INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES ('redemptions', 0)`),
    db.prepare(`INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES ('liquidations', 0)`),
    db.prepare(`INSERT OR IGNORE INTO sync_log(table_name, last_block) VALUES ('resolutions', 0)`),
  ]);
}

async function fetchDelphiStats(db) {
  const [r0, r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, r13] = await db.batch([
    db.prepare('SELECT COALESCE(SUM(tokens_in),0)  AS v FROM buys'),
    db.prepare('SELECT COALESCE(SUM(tokens_out),0) AS v FROM sells'),
    db.prepare('SELECT COALESCE(SUM(tokens_out),0) AS v FROM redemptions'),
    db.prepare('SELECT COUNT(*) AS v FROM buys'),
    db.prepare('SELECT COUNT(*) AS v FROM sells'),
    db.prepare('SELECT COUNT(*) AS v FROM redemptions'),
    db.prepare('SELECT COUNT(DISTINCT a) AS v FROM (SELECT buyer AS a FROM buys UNION SELECT seller FROM sells)'),
    db.prepare('SELECT COUNT(*) AS v FROM resolutions'),
    db.prepare('SELECT MAX(timestamp_) AS v FROM buys'),
    db.prepare(`
      SELECT 'BUY'  AS side, timestamp_, buyer  AS addr, market_proxy, tokens_in  AS usdc FROM buys
      UNION ALL
      SELECT 'SELL' AS side, timestamp_, seller AS addr, market_proxy, tokens_out AS usdc FROM sells
      ORDER BY timestamp_ DESC LIMIT 8
    `),
    db.prepare(`
      SELECT COUNT(DISTINCT market_proxy) AS v FROM (
        SELECT market_proxy FROM buys
        UNION SELECT market_proxy FROM sells
        UNION SELECT market_proxy FROM resolutions
      )
    `),
    db.prepare(`SELECT COALESCE(SUM(tokens_in), 0) AS v FROM buys WHERE timestamp_ > CAST(strftime('%s','now') AS INTEGER) - 86400`),
    db.prepare(`SELECT COALESCE(SUM(tokens_out), 0) AS v FROM sells WHERE timestamp_ > CAST(strftime('%s','now') AS INTEGER) - 86400`),
    db.prepare(`SELECT COALESCE(SUM(market_creator_reward + market_creator_trading_fees), 0) AS v FROM resolutions`),
  ]);
  const v = (res) => res.results?.[0]?.v ?? 0;
  return {
    buy_vol:      v(r0),
    sell_vol:     v(r1),
    redm_vol:     v(r2),
    buy_n:        v(r3),
    sell_n:       v(r4),
    redm_n:       v(r5),
    traders:      v(r6),
    resolutions:  v(r7),
    last_buy:     v(r8) || null,
    recent:       r9.results || [],
    markets:      v(r10),
    buy_vol_24h:  v(r11),
    sell_vol_24h: v(r12),
    total_fees:   v(r13),
  };
}

// ── Recent USDC.e transfers ───────────────────────────────────────────────

async function fetchUsdceTransfers() {
  const r = await fetch(
    `https://gensyn-mainnet.explorer.alchemy.com/api/v2/tokens/${USDC_E}/transfers`,
    { headers: { 'User-Agent': 'chain-monitor/1.0' } }
  );
  const d = await r.json();
  return (d.items || []).slice(0, 12);
}

async function fetchUsdce24hVolume() {
  const cutoff = Date.now() / 1000 - 86400;
  let total = 0, url = `https://gensyn-mainnet.explorer.alchemy.com/api/v2/tokens/${USDC_E}/transfers`;
  for (let page = 0; page < 10; page++) {
    const r = await fetch(url, { headers: { 'User-Agent': 'chain-monitor/1.0' } });
    const d = await r.json();
    const items = d.items || [];
    let done = false;
    for (const t of items) {
      if (new Date(t.timestamp).getTime() / 1000 < cutoff) { done = true; break; }
      total += Number(t.total?.value || 0);
    }
    if (done || !d.next_page_params) break;
    const qs = new URLSearchParams(d.next_page_params).toString();
    url = `https://gensyn-mainnet.explorer.alchemy.com/api/v2/tokens/${USDC_E}/transfers?${qs}`;
  }
  return total;
}

// ── Goldsky → D1 sync (runs on cron) ─────────────────────────────────────

async function gql(query) {
  const r = await fetch(GOLDSKY_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'User-Agent': 'chain-monitor/1.0' },
    body: JSON.stringify({ query }),
  });
  return (await r.json()).data;
}

async function fetchAllSince(entity, fields, blockGt) {
  const rows = [];
  let lastId = '';
  while (true) {
    const idFilter = lastId ? `, id_gt: "${lastId}"` : '';
    const q = `{ ${entity}(first: 100, orderBy: block_number, orderDirection: asc,
      where: { block_number_gt: "${blockGt}"${idFilter} }) { ${fields} } }`;
    const batch = (await gql(q))[entity] || [];
    rows.push(...batch);
    if (batch.length < 100) break;
    lastId = batch[batch.length - 1].id;
  }
  return rows;
}

async function syncTable(db, tableName, entity, fields, makeStmt) {
  const cur   = await db.prepare('SELECT last_block FROM sync_log WHERE table_name=?').bind(tableName).first();
  const since = cur?.last_block ?? 0;
  const rows  = await fetchAllSince(entity, fields, since);
  if (!rows.length) return 0;

  // D1 caps batch at 100 statements
  const stmts = rows.map(r => makeStmt(db, r));
  for (let i = 0; i < stmts.length; i += 100) {
    await db.batch(stmts.slice(i, i + 100));
  }
  const lastBlock = parseInt(rows[rows.length - 1].block_number);
  await db.prepare('UPDATE sync_log SET last_block=?, last_synced=? WHERE table_name=?')
    .bind(lastBlock, new Date().toISOString(), tableName).run();
  return rows.length;
}

async function syncDelphi(env) {
  const db = env.DB;
  await ensureSchema(db);
  await syncTable(db, 'buys', 'gatewayBuys',
    'id block_number timestamp_ transactionHash_ marketProxy buyer outcomeIdx tokensIn sharesOut',
    (db, r) => db.prepare('INSERT OR IGNORE INTO buys VALUES (?,?,?,?,?,?,?,?,?)')
      .bind(r.id, +r.block_number, +r.timestamp_, r.transactionHash_, r.marketProxy, r.buyer, +r.outcomeIdx, +r.tokensIn, r.sharesOut));

  await syncTable(db, 'sells', 'gatewaySells',
    'id block_number timestamp_ transactionHash_ marketProxy seller outcomeIdx sharesIn tokensOut',
    (db, r) => db.prepare('INSERT OR IGNORE INTO sells VALUES (?,?,?,?,?,?,?,?,?)')
      .bind(r.id, +r.block_number, +r.timestamp_, r.transactionHash_, r.marketProxy, r.seller, +r.outcomeIdx, r.sharesIn, +r.tokensOut));

  await syncTable(db, 'redemptions', 'gatewayRedemptions',
    'id block_number timestamp_ transactionHash_ marketProxy redeemer sharesIn tokensOut',
    (db, r) => db.prepare('INSERT OR IGNORE INTO redemptions VALUES (?,?,?,?,?,?,?,?)')
      .bind(r.id, +r.block_number, +r.timestamp_, r.transactionHash_, r.marketProxy, r.redeemer, r.sharesIn, +r.tokensOut));

  await syncTable(db, 'liquidations', 'gatewayLiquidations',
    'id block_number timestamp_ transactionHash_ marketProxy liquidator outcomeIndices sharesIn totalTokensOut',
    (db, r) => db.prepare('INSERT OR IGNORE INTO liquidations VALUES (?,?,?,?,?,?,?,?,?)')
      .bind(r.id, +r.block_number, +r.timestamp_, r.transactionHash_, r.marketProxy, r.liquidator, r.outcomeIndices, r.sharesIn, +r.totalTokensOut));

  await syncTable(db, 'resolutions', 'gatewayWinnerSubmitteds',
    'id block_number timestamp_ transactionHash_ marketProxy winningOutcomeIdx marketCreatorReward refund marketCreatorTradingFeesCut',
    (db, r) => db.prepare('INSERT OR IGNORE INTO resolutions VALUES (?,?,?,?,?,?,?,?,?)')
      .bind(r.id, +r.block_number, +r.timestamp_, r.transactionHash_, r.marketProxy, +r.winningOutcomeIdx, +r.marketCreatorReward, +r.refund, +r.marketCreatorTradingFeesCut));
}

// ── USDC.e cache (module-level, 30s TTL) ──────────────────────────────────

let _usdceCache = { ts: 0, transfers: [], vol24h: null };

async function getUsdceData() {
  if (Date.now() - _usdceCache.ts < 30_000) return _usdceCache;
  const [t, v] = await Promise.allSettled([
    fetchUsdceTransfers(),
    fetchUsdce24hVolume(),
  ]);
  _usdceCache = {
    ts:        Date.now(),
    transfers: t.status === 'fulfilled' ? t.value : _usdceCache.transfers,
    vol24h:    v.status === 'fulfilled' ? v.value : _usdceCache.vol24h,
  };
  return _usdceCache;
}

// ── /api/data handler ─────────────────────────────────────────────────────

async function handleData(env) {
  const [blocks, stats, rpc, l1_eth, delphi, usdc] = await Promise.allSettled([
    getJson(BLOCKS_API),
    getJson(STATS_API),
    fetchRpc(env),
    fetchL1Eth(env),
    fetchDelphiStats(env.DB),
    getUsdceData(),
  ]);

  return Response.json({
    ts:              Date.now(),
    blocks:          blocks.status   === 'fulfilled' ? blocks.value   : [],
    stats:           stats.status    === 'fulfilled' ? stats.value    : {},
    rpc:             rpc.status      === 'fulfilled' ? rpc.value      : { error: String(rpc.reason) },
    l1_eth:          l1_eth.status   === 'fulfilled' ? l1_eth.value   : null,
    delphi:          delphi.status === 'fulfilled' ? delphi.value          : {},
    usdc_transfers:  usdc.status   === 'fulfilled' ? usdc.value.transfers  : [],
    usdc_vol_24h:    usdc.status   === 'fulfilled' ? usdc.value.vol24h     : null,
  }, {
    headers: {
      'Cache-Control': 'no-store, no-cache, must-revalidate',
      'Pragma': 'no-cache',
      'CDN-Cache-Control': 'no-store',
    },
  });
}

// ── Worker entry ──────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === '/api/data') return handleData(env);
    if (!env.ASSETS) return new Response('Not Found', { status: 404 });
    return env.ASSETS.fetch(request);   // serve public/index.html etc.
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(syncDelphi(env));
  },
};
