import os
import asyncio
import json
import time
import re
import sqlite3
import discord
from discord.ext import tasks, commands
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
import anthropic
from anthropic import BadRequestError, RateLimitError, APIStatusError

# ─────────────────────────────────────────────────────────────────────────────
#  Environment & Constants
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()
MERCURY_API_KEY   = os.getenv("MERCURY_API_KEY")
DISCORD_TOKEN     = os.getenv("DISCORD_TOKEN")
GCP_API_KEY       = os.getenv("GCP_API_KEY")       # retained but not used for Gemini

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")  # used for Claude via SDK

if not (MERCURY_API_KEY and DISCORD_TOKEN):
    raise RuntimeError("MERCURY_API_KEY and DISCORD_TOKEN must be set in .env")
if not ANTHROPIC_API_KEY:
    print("⚠️  ANTHROPIC_API_KEY not set – Claude replies will be disabled.")

MERCURY_BASE_URL = "https://api.mercury.com/api/v1"

# Anthropic (Claude) settings
CLAUDE_MODEL   = "claude-opus-4-20250514"      # or switch to a cheaper model if desired
# Instantiate a global Claude client (SDK picks up ANTHROPIC_API_KEY automatically)
claude_client = anthropic.Anthropic()

# ─────────────────────────────────────────────────────────────────────────────
#  SQLite DB setup (caching transactions)
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH = "transactions.db"

async def init_db():
    """Initialize SQLite and create/alter transactions table if it doesn't exist."""
    def _init():
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Create table if not exists
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                account_id TEXT,
                createdAt TEXT,
                amount REAL,
                kind TEXT,
                vendorName TEXT,
                counterpartyName TEXT,
                bankDescription TEXT,
                mercuryCategory TEXT
            )
        """)
        # Ensure columns exist (for legacy DBs)
        c.execute("PRAGMA table_info(transactions)")
        existing_cols = [row[1] for row in c.fetchall()]
        if 'vendorName' not in existing_cols:
            c.execute("ALTER TABLE transactions ADD COLUMN vendorName TEXT")
        if 'counterpartyName' not in existing_cols:
            c.execute("ALTER TABLE transactions ADD COLUMN counterpartyName TEXT")
        if 'bankDescription' not in existing_cols:
            c.execute("ALTER TABLE transactions ADD COLUMN bankDescription TEXT")
        if 'mercuryCategory' not in existing_cols:
            c.execute("ALTER TABLE transactions ADD COLUMN mercuryCategory TEXT")

        # Indexes for faster lookups
        c.execute("CREATE INDEX IF NOT EXISTS idx_createdAt ON transactions(createdAt)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_vendor_lower ON transactions(lower(vendorName))")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cpn_lower ON transactions(lower(counterpartyName))")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bdesc_lower ON transactions(lower(bankDescription))")

        conn.commit()
        conn.close()
    await asyncio.get_event_loop().run_in_executor(None, _init)

async def upsert_transactions(transactions):
    """
    Insert or replace a list of transaction dicts into SQLite.
    Each dict needs keys: id, account_id, createdAt, amount, kind,
    vendorName, counterpartyName, bankDescription, mercuryCategory.
    """
    def _upsert(batch):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for tx in batch:
            c.execute(
                """
                INSERT OR REPLACE INTO transactions
                (id, account_id, createdAt, amount, kind, vendorName,
                 counterpartyName, bankDescription, mercuryCategory)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx.get("id"),
                    tx.get("account_id"),
                    tx.get("createdAt"),
                    tx.get("amount"),
                    tx.get("kind"),
                    tx.get("vendorName"),
                    tx.get("counterpartyName"),
                    tx.get("bankDescription"),
                    tx.get("mercuryCategory"),
                )
            )
        conn.commit()
        conn.close()
    # Split into chunks to avoid blocking too long
    for i in range(0, len(transactions), 500):
        batch = transactions[i:i+500]
        await asyncio.get_event_loop().run_in_executor(None, _upsert, batch)

async def get_cached_transactions_for_month(year, month):
    """
    Return all transactions from the DB that occurred in the specified year-month.
    """
    def _fetch():
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        start = f"{year:04d}-{month:02d}-01T00:00:00+00:00"
        if month == 12:
            end_year, end_month = year + 1, 1
        else:
            end_year, end_month = year, month + 1
        end = f"{end_year:04d}-{end_month:02d}-01T00:00:00+00:00"
        c.execute(
            """
            SELECT id, account_id, createdAt, amount, kind, vendorName,
                   counterpartyName, bankDescription, mercuryCategory
            FROM transactions
            WHERE createdAt >= ? AND createdAt < ?
            ORDER BY createdAt ASC
            """,
            (start, end),
        )
        rows = c.fetchall()
        conn.close()
        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "account_id": row[1],
                "createdAt": row[2],
                "amount": row[3],
                "kind": row[4],
                "vendorName": row[5],
                "counterpartyName": row[6],
                "bankDescription": row[7],
                "mercuryCategory": row[8],
            })
        return result
    return await asyncio.get_event_loop().run_in_executor(None, _fetch)

async def get_max_createdAt():
    """
    Return the latest createdAt timestamp in the transactions table, or None if empty.
    """
    def _max():
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT MAX(createdAt) FROM transactions")
        row = c.fetchone()
        conn.close()
        return row[0] if row else None
    return await asyncio.get_event_loop().run_in_executor(None, _max)

# ─────────────────────────────────────────────────────────────────────────────
#  Discord setup
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────────────────────────────────────────
#  Mercury helpers
# ─────────────────────────────────────────────────────────────────────────────

_mercury_headers = {
    "Authorization": f"Bearer {MERCURY_API_KEY}",
    "Content-Type": "application/json",
}

async def fetch_mercury_accounts():
    """
    Fetch core, credit, and treasury accounts.
    """
    def _fetch():
        data = {}
        resp = __import__('requests').get(f"{MERCURY_BASE_URL}/accounts", headers=_mercury_headers, timeout=20)
        data["accounts"] = resp.json().get("accounts", []) if resp.ok else []
        cr = __import__('requests').get(f"{MERCURY_BASE_URL}/credit", headers=_mercury_headers, timeout=20)
        data["credit_accounts"] = cr.json().get("accounts", []) if cr.ok else []
        tr = __import__('requests').get(f"{MERCURY_BASE_URL}/treasury", headers=_mercury_headers, timeout=20)
        data["treasury_accounts"] = tr.json().get("accounts", []) if tr.ok else []
        return data
    return await asyncio.get_event_loop().run_in_executor(None, _fetch)

async def fetch_all_tx_for_account(acct_id, after=None):
    """
    Yield all transactions for an account, paging until exhausted.
    Uses blocking HTTP calls off-loop.
    """
    def _fetch_all(acc_id, aft):
        collected = []
        url = f"{MERCURY_BASE_URL}/account/{acc_id}/transactions"
        cursor = None
        while True:
            params = {"limit": 100}
            if cursor:
                params["before"] = cursor
            if aft:
                params["from"] = aft
            r = __import__('requests').get(url, headers=_mercury_headers, params=params, timeout=30)
            if r.status_code != 200:
                print(f"DEBUG → Error fetching {acc_id} page: {r.status_code} {r.text}")
                break
            batch = r.json().get("transactions", [])
            if not batch:
                break
            for tx in batch:
                tx["account_id"] = acc_id
                collected.append(tx)
            if aft and batch[-1]["createdAt"] < aft:
                break
            cursor = batch[-1].get("id")
            if not cursor:
                break
        return collected

    tx_list = await asyncio.get_event_loop().run_in_executor(None, _fetch_all, acct_id, after)
    return tx_list

async def initial_sync_all_accounts():
    """
    On first run: fetch all transactions for core+credit and cache.
    """
    print("DEBUG → Starting initial full sync of all Mercury transactions...")
    data = await fetch_mercury_accounts()
    core_accounts = data.get("accounts", [])
    credit_accounts = data.get("credit_accounts", [])
    for acct in core_accounts + credit_accounts:
        acct_id = acct.get("id")
        if not acct_id:
            continue
        print(f"DEBUG → Syncing full history for account {acct_id} …")
        txs = await fetch_all_tx_for_account(acct_id, after=None)
        batch = []
        for tx in txs:
            vendor_name = (
                tx.get("merchantName") or tx.get("counterpartyName") or
                tx.get("bankDescription") or (tx.get("cardDetails") or {}).get("merchantName") or
                tx.get("description")
            )
            batch.append({
                "id": tx["id"],
                "account_id": acct_id,
                "createdAt": tx.get("createdAt"),
                "amount": tx.get("amount"),
                "kind": tx.get("kind", "").lower(),
                "vendorName": vendor_name,
                "counterpartyName": tx.get("counterpartyName"),
                "bankDescription": tx.get("bankDescription"),
                "mercuryCategory": tx.get("mercuryCategory"),
            })
            if len(batch) >= 500:
                await upsert_transactions(batch)
                batch = []
        if batch:
            await upsert_transactions(batch)
        print(f"DEBUG → Completed full sync for account {acct_id}.")
    print("DEBUG → Initial full sync finished.")

async def incremental_sync():
    """
    On subsequent runs: fetch new transactions (createdAt > max) and cache.
    """
    latest_ts = await get_max_createdAt()
    print(f"DEBUG → Starting incremental sync since {latest_ts} …")
    data = await fetch_mercury_accounts()
    core_accounts = data.get("accounts", [])
    credit_accounts = data.get("credit_accounts", [])
    for acct in core_accounts + credit_accounts:
        acct_id = acct.get("id")
        if not acct_id:
            continue
        print(f"DEBUG → Syncing new txns for {acct_id} since {latest_ts} …")
        txs = await fetch_all_tx_for_account(acct_id, after=latest_ts)
        batch = []
        for tx in txs:
            vendor_name = (
                tx.get("merchantName") or tx.get("counterpartyName") or
                tx.get("bankDescription") or (tx.get("cardDetails") or {}).get("merchantName") or
                tx.get("description")
            )
            batch.append({
                "id": tx["id"],
                "account_id": acct_id,
                "createdAt": tx.get("createdAt"),
                "amount": tx.get("amount"),
                "kind": tx.get("kind", "").lower(),
                "vendorName": vendor_name,
                "counterpartyName": tx.get("counterpartyName"),
                "bankDescription": tx.get("bankDescription"),
                "mercuryCategory": tx.get("mercuryCategory"),
            })
            if len(batch) >= 500:
                await upsert_transactions(batch)
                batch = []
        if batch:
            await upsert_transactions(batch)
        print(f"DEBUG → Completed incremental sync for account {acct_id}.")
    print("DEBUG → Incremental sync finished.")

async def cache_transactions_daily():
    """
    Cache transactions for current month only.
    """
    data = await fetch_mercury_accounts()
    core_accounts = data.get("accounts", [])
    credit_accounts = data.get("credit_accounts", [])
    today = datetime.now(timezone.utc).date()
    year, month = today.year, today.month
    since = f"{year:04d}-{month:02d}-01T00:00:00+00:00"
    month_txns = []
    for acct in core_accounts + credit_accounts:
        acct_id = acct.get("id")
        if not acct_id:
            continue
        txs = await fetch_all_tx_for_account(acct_id, after=since)
        for tx in txs:
            created = tx.get("createdAt")
            if not created:
                continue
            tx_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if tx_dt.year == year and tx_dt.month == month:
                vendor_name = (
                    tx.get("merchantName") or tx.get("counterpartyName") or 
                    tx.get("bankDescription") or (tx.get("cardDetails") or {}).get("merchantName") or
                    tx.get("description")
                )
                month_txns.append({
                    "id": tx.get("id"),
                    "account_id": acct_id,
                    "createdAt": tx.get("createdAt"),
                    "amount": tx.get("amount"),
                    "kind": tx.get("kind", "").lower(),
                    "vendorName": vendor_name,
                    "counterpartyName": tx.get("counterpartyName"),
                    "bankDescription": tx.get("bankDescription"),
                    "mercuryCategory": tx.get("mercuryCategory"),
                })
    if month_txns:
        await upsert_transactions(month_txns)
    print(f"DEBUG → Cached {len(month_txns)} current-month transactions into SQLite.")

async def fetch_transactions_for_arbitrary_month(year, month):
    """
    On-demand: fetch all core+credit transactions for given year/month (uncached).
    Returns list of dicts.
    """
    data = await fetch_mercury_accounts()
    core_accounts = data.get("accounts", [])
    credit_accounts = data.get("credit_accounts", [])
    since = f"{year:04d}-{month:02d}-01T00:00:00+00:00"
    month_txns = []
    for acct in core_accounts + credit_accounts:
        acct_id = acct.get("id")
        if not acct_id:
            continue
        txs = await fetch_all_tx_for_account(acct_id, after=since)
        for tx in txs:
            created = tx.get("createdAt")
            if not created:
                continue
            tx_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if tx_dt.year == year and tx_dt.month == month:
                vendor_name = (
                    tx.get("merchantName") or tx.get("counterpartyName") or
                    tx.get("bankDescription") or (tx.get("cardDetails") or {}).get("merchantName") or
                    tx.get("description")
                )
                month_txns.append({
                    "id": tx.get("id"),
                    "account_id": acct_id,
                    "createdAt": tx.get("createdAt"),
                    "amount": tx.get("amount"),
                    "kind": tx.get("kind", "").lower(),
                    "vendorName": vendor_name,
                    "counterpartyName": tx.get("counterpartyName"),
                    "bankDescription": tx.get("bankDescription"),
                    "mercuryCategory": tx.get("mercuryCategory"),
                })
    print(f"DEBUG → Fetched {len(month_txns)} transactions for month {month:02d} of {year}.")
    return month_txns

# ─────────────────────────────────────────────────────────────────────────────
#  Build context for Claude
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_mercury_data():
    """
    Fetch live Mercury data (for context): accounts + sample txns.
    """
    def _fetch_data():
        data = {}
        resp = __import__('requests').get(f"{MERCURY_BASE_URL}/accounts", headers=_mercury_headers, timeout=20)
        data["accounts"] = resp.json().get("accounts", []) if resp.ok else []
        data["core_transactions"] = []
        for acct in data["accounts"]:
            acct_id = acct.get("id")
            if not acct_id:
                continue
            txr = __import__('requests').get(f"{MERCURY_BASE_URL}/account/{acct_id}/transactions", headers=_mercury_headers, timeout=20)
            if txr.ok:
                for tx in txr.json().get("transactions", []):
                    tx.update(account_id=acct_id, account_type="core")
                    data["core_transactions"].append(tx)
        cr = __import__('requests').get(f"{MERCURY_BASE_URL}/credit", headers=_mercury_headers, timeout=20)
        data["credit_accounts"] = cr.json().get("accounts", []) if cr.ok else []
        data["credit_transactions"] = []
        for c in data["credit_accounts"]:
            cid = c.get("id")
            if not cid:
                continue
            txr = __import__('requests').get(f"{MERCURY_BASE_URL}/account/{cid}/transactions", headers=_mercury_headers, timeout=20)
            if txr.ok:
                for tx in txr.json().get("transactions", []):
                    tx.update(account_id=cid, account_type="credit")
                    data["credit_transactions"].append(tx)
        tr = __import__('requests').get(f"{MERCURY_BASE_URL}/treasury", headers=_mercury_headers, timeout=20)
        data["treasury_accounts"] = tr.json().get("accounts", []) if tr.ok else []
        return data
    return await asyncio.get_event_loop().run_in_executor(None, _fetch_data)

async def build_full_context(data: dict) -> str:
    """
    Build ~300–400 token context: balances, 30-day spend, today’s debits.
    """
    lines = []
    core = data.get("accounts", [])
    credit = data.get("credit_accounts", [])
    treas = data.get("treasury_accounts", [])
    tot_core = sum(a.get("availableBalance", a.get("currentBalance", 0)) for a in core)
    tot_credit = sum(a.get("currentBalance", 0) for a in credit)
    tot_treas = sum(t.get("availableBalance", t.get("currentBalance", 0)) for t in treas)
    net_cash = tot_core + tot_treas + tot_credit
    lines.append(f"Net cash: ${net_cash:.2f}")
    for a in core:
        bal = a.get("availableBalance", a.get("currentBalance", 0))
        lines.append(f"Core '{a.get('name', a.get('nickname','Unnamed'))}' balance: ${bal:.2f}")
    for c in credit:
        bal = c.get("currentBalance", 0)
        lines.append(f"Credit '{c.get('name','Unnamed')}' balance: ${bal:.2f}")
    for t in treas:
        bal = t.get("availableBalance", t.get("currentBalance", 0))
        lines.append(f"Treasury '{t.get('name','Unnamed')}' balance: ${bal:.2f}")
    # 30-day category totals (core only)
    cat_totals = {}
    today = datetime.now(timezone.utc).date()
    month_ago = today - timedelta(days=29)
    for tx in data.get("core_transactions", []):
        kind = tx.get("kind", "").lower()
        if "debit" not in kind:
            continue
        created = tx.get("createdAt")
        if not created:
            continue
        tx_date = datetime.fromisoformat(created.replace("Z", "+00:00")).date()
        if month_ago <= tx_date <= today:
            cat = tx.get("mercuryCategory", "uncategorized")
            cat_totals[cat] = cat_totals.get(cat, 0) + abs(tx.get("amount", 0))
    if cat_totals:
        top5 = sorted(cat_totals.items(), key=lambda kv: kv[1], reverse=True)[:8]
        lines.append("30-day spend by category:")
        for cat, amt in top5:
            lines.append(f"  • {cat}: ${amt:.2f}")
    # Today’s debits
    def list_today(transactions, label):
        today_tx = []
        for tx in transactions:
            created = tx.get("createdAt")
            if not created:
                continue
            tx_date = datetime.fromisoformat(created.replace("Z", "+00:00")).date()
            kind = tx.get("kind", "@").lower()
            if tx_date == today and ("debit" in kind or "creditcardtransaction" in kind):
                name = tx.get("counterpartyName") or tx.get("bankDescription") or "txn"
                amt = abs(tx.get("amount", 0))
                today_tx.append((name, amt))
        if today_tx:
            lines.append(f"Today's {label}:")
            for name, amt in today_tx[:10]:
                lines.append(f"  - {name}: ${amt:.2f}")
    list_today(data.get("core_transactions", []), "core debits")
    list_today(data.get("credit_transactions", []), "credit card charges")
    return "\n".join(lines)[:4000]

# ─────────────────────────────────────────────────────────────────────────────
#  Vendor-specific spend query
# ─────────────────────────────────────────────────────────────────────────────

async def get_vendor_spend(vendor: str, year=None, month=None):
    """
    Returns (rows, total_amount) for all transactions where vendorName,
    counterpartyName, or bankDescription contains vendor (case-insensitive).
    If year/month given, filter by createdAt prefix = 'YYYY-MM'.
    If no cached rows found for that period, fetch on-demand and cache.
    """
    def _query(v, yr, mo):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        clauses = []
        params = []
        pattern = f"%{v.lower()}%"
        clauses.append("(lower(vendorName) LIKE ? OR lower(counterpartyName) LIKE ? OR lower(bankDescription) LIKE ?)")
        params.extend([pattern, pattern, pattern])
        if yr and mo:
            prefix = f"{yr:04d}-{mo:02d}"
            clauses.append("substr(createdAt,1,7) = ?")
            params.append(prefix)
        where_sql = " AND ".join(clauses)
        query = f"""
            SELECT createdAt, vendorName, amount
            FROM transactions
            WHERE {where_sql}
            ORDER BY createdAt ASC
        """
        c.execute(query, params)
        rows = c.fetchall()
        conn.close()
        return rows
    rows = await asyncio.get_event_loop().run_in_executor(None, _query, vendor, year, month)
    if year and month and not rows:
        fetched = await fetch_transactions_for_arbitrary_month(year, month)
        await upsert_transactions(fetched)
        rows = await asyncio.get_event_loop().run_in_executor(None, _query, vendor, year, month)
    total = sum(abs(r[2]) for r in rows)
    return rows, total

# ─────────────────────────────────────────────────────────────────────────────
#  Dump transactions as CSV to Discord channel
# ─────────────────────────────────────────────────────────────────────────────

async def dump_to_discord(rows, channel):
    """
    Send a list of transaction tuples to Discord as CSV.
    Tuples: (createdAt, vendorName, amount)
    """
    if not rows:
        await channel.send("No transactions for that period/vendor.")
        return
    header = "date,amount,vendor"
    csv_lines = [f"{r[0][:10]},{abs(r[2]):.2f},{(r[1] or 'Unknown').replace(',', ' ')}" for r in rows]
    blob = header + "\n" + "\n".join(csv_lines)
    if len(blob) > 1800:
        path = "/tmp/transactions.csv"
        with open(path, "w") as f:
            f.write(blob)
        await channel.send(file=discord.File(path, filename="transactions.csv"))
    else:
        for chunk in chunk_message(blob):
            await channel.send(f"```csv\n{chunk}\n```")

# ─────────────────────────────────────────────────────────────────────────────
#  Claude helper (via SDK) with retry/backoff for 429s
# ─────────────────────────────────────────────────────────────────────────────

def ask_claude_via_sdk(prompt: str, context: str = "") -> str:
    """
    Ask Claude via Anthropic SDK (v1). Retries on 429, returns plain string.
    """
    system_prompt = (
        "You are FinBot, a proactive cash-flow advisor. "
        "Use the figures in the context exactly—do not invent numbers. "
        "When answering, cite the relevant dollar amounts you see."
    )

    # ONLY user-role messages now
    messages = [
        {
            "role": "user",
            "content": f"--- Context ---\n{context}\n\n--- Question ---\n{prompt}",
        }
    ]

    backoff = 2
    for _ in range(4):
        try:
            resp = claude_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=512,
                temperature=0.7,
                system=system_prompt,   # top-level system field
                messages=messages,
            )

            blocks = resp.content or []
            return blocks[0].text.strip() if blocks else "(no text returned)"

        except RateLimitError:               # 429
            time.sleep(backoff)
            backoff *= 2
            continue

        except BadRequestError as e:         # 400, incl. “credit balance too low”
            return f"Claude API error: {e}"

        except APIStatusError as e:           # other 4xx/5xx errors
            return f"Claude API status error: {e}"

        except Exception as e:                # catch-all for anything unexpected
            return f"Claude SDK error: {e}"

    return "(Claude error: too many retries)"

# ─────────────────────────────────────────────────────────────────────────────
#  Discord event handlers & commands
# ─────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (id={bot.user.id})")
    await init_db()
    if await get_max_createdAt() is None:
        await initial_sync_all_accounts()
    else:
        await incremental_sync()
    cache_transactions_daily_task.start()
    twice_daily_summary.start()

@tasks.loop(hours=24)
async def cache_transactions_daily_task():
    """
    At 00:05 UTC each day, run incremental sync then cache current-month subset.
    """
    now = datetime.now(timezone.utc)
    next_run = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    delay = (next_run - now).total_seconds()
    await asyncio.sleep(delay)
    await incremental_sync()
    await cache_transactions_daily()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    addressed = (
        isinstance(message.channel, discord.DMChannel) or
        bot.user in message.mentions
    )
    if addressed:
        raw = message.content.replace(f"<@!{bot.user.id}>", "").strip()
        content = raw.lower()
        if re.search(r"\brefreshcache\b", content):
            async with message.channel.typing():
                await incremental_sync()
                await cache_transactions_daily()
            await message.channel.send("✅ Cache fully refreshed.")
            return

        # “how much did I spend on <vendor> this month?”
        m1 = re.search(r"how\s+much\s+did\s+.*\s+spend\s+on\s+(.+?)\s+this\s+month", content, re.I)
        if m1:
            vendor = m1.group(1).strip()
            year, month = datetime.now(timezone.utc).year, datetime.now(timezone.utc).month
            rows, total = await get_vendor_spend(vendor, year, month)
            if not rows:
                await message.channel.send(f"No charges to '{vendor}' found for {year}-{month:02d}.")
            else:
                text = f"Transactions for '{vendor}' in {year}-{month:02d}:\n"
                for createdAt, vname, amt in rows:
                    date_str = createdAt[:10]
                    text += f"- {date_str}: {vname} → ${abs(amt):.2f}\n"
                text += f"\nTotal spent on '{vendor}' in {year}-{month:02d}: **${total:.2f}**"
                await message.channel.send(text)
            return

        # “how much did I spend on <vendor> in <Month> <Year>?”
        m2 = re.search(r"how\s+much\s+did\s+.*\s+spend\s+on\s+(.+?)\s+(?:in\s+month\s+of\s+|in\s+)([a-z]+)\s+(\d{4})", content, re.I)
        if m2:
            vendor = m2.group(1).strip()
            month_name = m2.group(2).capitalize()
            year = int(m2.group(3))
            try:
                month_num = datetime.strptime(month_name, "%B").month
            except ValueError:
                await message.channel.send(f"❌ Unrecognized month: {month_name}")
                return
            rows, total = await get_vendor_spend(vendor, year, month_num)
            if not rows:
                await message.channel.send(f"No charges to '{vendor}' found in {month_name} {year}.")
            else:
                text = f"Transactions for '{vendor}' in {month_name} {year}:\n"
                for createdAt, vname, amt in rows:
                    date_str = createdAt[:10]
                    text += f"- {date_str}: {vname} → ${abs(amt):.2f}\n"
                text += f"\nTotal spent on '{vendor}' in {month_name} {year}: **${total:.2f}**"
                await message.channel.send(text)
            return

        # “List all charges to <vendor>” (current month)
        m3 = re.search(r"list\s+all\s+charges\s+to\s+(.+)", content, re.I)
        if m3:
            vendor_name = m3.group(1).strip()
            year, month = datetime.now(timezone.utc).year, datetime.now(timezone.utc).month
            rows, _ = await get_vendor_spend(vendor_name, year, month)
            await dump_to_discord(rows, message.channel)
            return

        # “Give me all transaction details for this month”
        if re.search(r"give\s+me\s+all\s+transaction[s]?\s+details\s+for\s+this\s+month", content, re.I):
            year, month = datetime.now(timezone.utc).year, datetime.now(timezone.utc).month
            rows = await get_cached_transactions_for_month(year, month)
            await dump_to_discord([(r["createdAt"], r["vendorName"], r["amount"]) for r in rows], message.channel)
            return

        # “Give me all transaction details for today”
        if re.search(r"give\s+me\s+all\s+transaction[s]?\s+details\s+for\s+today", content, re.I):
            today_str = datetime.now(timezone.utc).date().isoformat()
            year, month = datetime.now(timezone.utc).year, datetime.now(timezone.utc).month
            all_rows = await get_cached_transactions_for_month(year, month)
            today_rows = [r for r in all_rows if r["createdAt"][:10] == today_str]
            await dump_to_discord([(r["createdAt"], r["vendorName"], r["amount"]) for r in today_rows], message.channel)
            return

        # “Give me all transaction details for month of <MonthName> [YYYY]”
        m4 = re.search(
            r"(?:please\s+)?give\s+me\s+all\s+transaction[s]?\s+details\s+for\s+month\s+of\s+([a-z]+)(?:\s+(\d{4}))?", content, re.I
        )
        if m4:
            month_name = m4.group(1).capitalize()
            year_str = m4.group(2)
            try:
                month_dt = datetime.strptime(month_name, "%B")
                month_num = month_dt.month
            except ValueError:
                await message.channel.send(f"❌ Unrecognized month name: {month_name}")
                return
            year = int(year_str) if year_str else datetime.now(timezone.utc).year
            cached = await get_cached_transactions_for_month(year, month_num)
            if not cached:
                fetched = await fetch_transactions_for_arbitrary_month(year, month_num)
                await upsert_transactions(fetched)
                cached = fetched
            await dump_to_discord([(r["createdAt"], r["vendorName"], r["amount"]) for r in cached], message.channel)
            return

        # Otherwise, fallback to Claude
        async with message.channel.typing():
            data = await fetch_mercury_data()
            context = await build_full_context(data)
            reply = await asyncio.get_event_loop().run_in_executor(
                None, ask_claude_via_sdk, raw, context
            )
        for chunk in chunk_message(reply):
            await message.channel.send(chunk)

    await bot.process_commands(message)

@tasks.loop(hours=12)
async def twice_daily_summary():
    await bot.wait_until_ready()
    ch_id = int(os.getenv("DISCORD_CHANNEL_ID", 0))
    if not ch_id:
        return
    channel = bot.get_channel(ch_id)
    if not channel:
        return
    data = await fetch_mercury_data()
    today = datetime.now(timezone.utc).date()
    spent_today = 0.0
    spent_today_credit = 0.0
    seven_days_ago = today - timedelta(days=6)
    spent_last_week = 0.0
    core_accounts = data.get("accounts", [])
    credit_accounts = data.get("credit_accounts", [])
    treasury_accounts = data.get("treasury_accounts", [])
    total_core = sum(acct.get("availableBalance", acct.get("currentBalance", 0.0)) for acct in core_accounts)
    total_credit = sum(acct.get("currentBalance", 0.0) for acct in credit_accounts)
    total_treasury = sum(acct.get("availableBalance", acct.get("currentBalance", 0.0)) for acct in treasury_accounts)
    net_cash = total_core + total_treasury + total_credit
    lines = [f"**Net Cash Position (all accounts combined): ${net_cash:.2f}**\n"]
    for acct in core_accounts:
        bal = acct.get("availableBalance", acct.get("currentBalance", 0.0))
        if bal < 2000.0:
            name = acct.get("name", acct.get("nickname", "Unnamed"))
            lines.append(f":rotating_light: **Low Balance Alert:** {name} is only ${bal:.2f} available.")
    if any(acct.get("availableBalance", acct.get("currentBalance", 0.0)) < 2000.0 for acct in core_accounts):
        lines.append("")
    core_tx_list = data.get("core_transactions", [])
    thirty_days_ago = today - timedelta(days=29)
    category_totals = {}
    for tx in core_tx_list:
        date_str = tx.get("createdAt")
        if not date_str:
            continue
        try:
            tx_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except:
            continue
        kind = tx.get("kind", tx.get("type", "")).lower()
        amount = abs(tx.get("amount", 0.0))
        category = tx.get("mercuryCategory") or "uncategorized"
        if "debit" in kind:
            if seven_days_ago <= tx_date <= today:
                spent_last_week += amount
            if tx_date == today:
                spent_today += amount
            if thirty_days_ago <= tx_date <= today:
                category_totals[category] = category_totals.get(category, 0.0) + amount
    for tx in data.get("credit_transactions", []):
        date_str = tx.get("createdAt")
        if not date_str:
            continue
        try:
            tx_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except:
            continue
        kind = tx.get("kind", tx.get("type", "")).lower()
        amount = abs(tx.get("amount", 0.0))
        if "creditcardtransaction" in kind and tx_date == today:
            spent_today_credit += amount
    avg_last_week = (spent_last_week / 7.0) if spent_last_week else 0.0
    lines.append(f"**Daily Spending Summary for {today.isoformat()}**")
    lines.append(f"- Checking/Savings spent: ${spent_today:.2f} (7-day avg: ${avg_last_week:.2f})")
    lines.append(f"- Credit card spent: ${spent_today_credit:.2f} today")
    if spent_today > avg_last_week:
        diff = spent_today - avg_last_week
        lines.append(f":warning: You spent ${diff:.2f} more than your 7-day average on core accounts.")
    else:
        diff = avg_last_week - spent_today
        lines.append(f":white_check_mark: You spent ${diff:.2f} less than your 7-day average on core accounts.")
    lines.append("\n**Last 30 Days Spending by Category (Core Accounts):**")
    if category_totals:
        for cat, total in sorted(category_totals.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- {cat}: ${total:.2f}")
    else:
        lines.append("No debit transactions in the last 30 days.")
    lines.append("\n**Today's Core Transactions:**")
    core_tx_today = [
        tx for tx in core_tx_list
        if (
            datetime.fromisoformat(tx.get("createdAt").replace("Z", "+00:00")).date() == today
            and "debit" in tx.get("kind", "").lower()
        )
    ]
    if core_tx_today:
        for tx in core_tx_today:
            desc = tx.get("counterpartyName") or tx.get("bankDescription") or "Transaction"
            amt = abs(tx.get("amount", 0.0))
            cat = tx.get("mercuryCategory") or "uncategorized"
            lines.append(f"- {desc}: ${amt:.2f} (Category: {cat})")
    else:
        lines.append("No core debit transactions today.")
    lines.append("\n**Today's Credit Card Transactions:**")
    credit_tx_today = [
        tx for tx in data.get("credit_transactions", [])
        if (
            datetime.fromisoformat(tx.get("createdAt").replace("Z", "+00:00")).date() == today
            and "creditcardtransaction" in tx.get("kind", "").lower()
        )
    ]
    if credit_tx_today:
        for tx in credit_tx_today:
            desc = tx.get("counterpartyName") or tx.get("bankDescription") or "Credit Charge"
            amt = abs(tx.get("amount", 0.0))
            lines.append(f"- {desc}: ${amt:.2f}")
    else:
        lines.append("No credit transactions today.")
    lines.append("\n**Mercury Account Balances & Cards:**")
    for acct in data.get("accounts", []):
        acct_id = acct.get("id")
        name = acct.get("name", acct.get("nickname", "Unnamed"))
        bal = acct.get("availableBalance", acct.get("currentBalance", 0.0))
        lines.append(f"- {name} ({acct_id}): ${bal:.2f}")
    lines.append("\n**Credit Account Balances & Cards:**")
    for c in data.get("credit_accounts", []):
        c_id = c.get("id")
        name = c.get("name", "Credit Account")
        bal = c.get("availableBalance", c.get("currentBalance", 0.0))
        lines.append(f"- {name} ({c_id}): ${bal:.2f}")
    lines.append("\n**Treasury Account Balances (T-bills):**")
    for t in data.get("treasury_accounts", []):
        t_id = t.get("id")
        name = t.get("name", "Treasury Account")
        bal = t.get("availableBalance", t.get("currentBalance", 0.0))
        lines.append(f"- {name} ({t_id}): ${bal:.2f}")
    await channel.send("\n".join(lines))

@bot.command(help="Dump the raw Mercury JSON")
async def finance(ctx):
    await ctx.trigger_typing()
    data = await fetch_mercury_data()
    pretty = json.dumps(data, indent=2)
    for chunk in chunk_message(pretty):
        await ctx.send(f"```json\n{chunk}\n```")

# ─────────────────────────────────────────────────────────────────────────────
#  Formatting helper
# ─────────────────────────────────────────────────────────────────────────────

def chunk_message(msg: str, limit: int = 1900):
    """
    Splits a long string into ≤limit-character chunks on line boundaries.
    """
    lines = msg.splitlines(keepends=True)
    out = []
    chunk = ""
    for ln in lines:
        if len(chunk) + len(ln) > limit:
            out.append(chunk)
            chunk = ln
        else:
            chunk += ln
    if chunk:
        out.append(chunk)
    return out

# ─────────────────────────────────────────────────────────────────────────────
#  Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
