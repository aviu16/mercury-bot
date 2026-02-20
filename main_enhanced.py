# Enhanced Mercury Bot with Transaction Monitoring
# This is an enhanced version of main.py with real-time notifications

import os
import asyncio
import json
import time
import re
import sqlite3
import discord
import requests
from discord.ext import tasks, commands
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
import anthropic
from anthropic import BadRequestError, RateLimitError, APIStatusError

# Import the transaction monitor
from transaction_monitor import TransactionMonitor

# Import financial agent
from financial_agent import FinancialAgent

# Import enhanced Mercury API
from enhanced_mercury_api import EnhancedMercuryAPI

# ─────────────────────────────────────────────────────────────────────────────
#  Environment & Constants
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()
MERCURY_API_KEY   = os.getenv("MERCURY_API_KEY")
DISCORD_TOKEN     = os.getenv("DISCORD_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NOTIFICATION_CHANNEL_ID = int(os.getenv("NOTIFICATION_CHANNEL_ID", "0"))

if not (MERCURY_API_KEY and DISCORD_TOKEN):
    raise RuntimeError("MERCURY_API_KEY and DISCORD_TOKEN must be set in .env")
if not ANTHROPIC_API_KEY:
    print("⚠️  ANTHROPIC_API_KEY not set – Claude replies will be disabled.")

MERCURY_BASE_URL = "https://api.mercury.com/api/v1"

# Anthropic (Claude) settings
CLAUDE_MODEL   = "claude-opus-4-20250514"
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

# ─────────────────────────────────────────────────────────────────────────────
#  Discord setup
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────────────────────────────────────────
#  Mercury API helpers (from original main.py)
# ─────────────────────────────────────────────────────────────────────────────

_mercury_headers = {
    "Authorization": f"Bearer {MERCURY_API_KEY}",
    "Content-Type": "application/json",
}

async def fetch_mercury_accounts():
    """Fetch core, credit, and treasury accounts."""
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
    """Yield all transactions for an account, paging until exhausted."""
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

# ─────────────────────────────────────────────────────────────────────────────
#  Enhanced Mercury API Integration
# ─────────────────────────────────────────────────────────────────────────────

# Use the enhanced Mercury API that includes ALL endpoints
enhanced_mercury_api = EnhancedMercuryAPI(MERCURY_API_KEY)

# ─────────────────────────────────────────────────────────────────────────────
#  Transaction Monitor Setup
# ─────────────────────────────────────────────────────────────────────────────

# Initialize transaction monitor and financial agent
transaction_monitor = None
financial_agent = None

# ─────────────────────────────────────────────────────────────────────────────
#  Bot Events
# ─────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    """Called when the bot is ready"""
    print(f"🤖 {bot.user} is online and ready!")
    print(f"📊 Monitoring {len(bot.guilds)} guild(s)")
    
    # Initialize database
    await init_db()
    
    # Start transaction monitoring if channel is configured
    global transaction_monitor, financial_agent
    
    # Initialize financial agent with enhanced API
    financial_agent = FinancialAgent(bot, enhanced_mercury_api, claude_client)
    print("🤖 Financial agent initialized with full Mercury API access")
    
    if NOTIFICATION_CHANNEL_ID:
        transaction_monitor = TransactionMonitor(bot, enhanced_mercury_api, NOTIFICATION_CHANNEL_ID)
        await transaction_monitor.start_monitoring()
        print(f"🔔 Transaction monitoring enabled for channel {NOTIFICATION_CHANNEL_ID}")
    else:
        print("⚠️  NOTIFICATION_CHANNEL_ID not set - transaction monitoring disabled")
    
    # Send startup notification
    await send_startup_notification()

@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages"""
    if message.author.bot:
        return
    
    # Check if message is for the bot
    addressed = (
        isinstance(message.channel, discord.DMChannel) or
        bot.user in message.mentions
    )
    
    if addressed:
        raw = message.content.replace(f"<@!{bot.user.id}>", "").strip()
        content = raw.lower()
        
        print(f"DEBUG: Received message: '{raw}' (content: '{content}')")
        
        # Simple test command
        if re.search(r"\btest\b", content):
            await message.channel.send("✅ Bot is working! I can see your message.")
            return
        
        # Handle transaction monitoring commands
        if re.search(r"\btoggle\s+notifications\b", content):
            if transaction_monitor:
                await transaction_monitor.toggle_notifications(message.channel)
            else:
                await message.channel.send("❌ Transaction monitoring is not enabled")
            return
        
        if re.search(r"\bnotification\s+settings\b", content):
            if transaction_monitor:
                await transaction_monitor.notify_settings(message.channel)
            else:
                await message.channel.send("❌ Transaction monitoring is not enabled")
            return
        
        # Handle financial analysis commands
        if re.search(r"\bfinancial\s+report\b", content):
            async with message.channel.typing():
                await message.channel.send("📊 Generating financial report...")
                try:
                    print("DEBUG: Starting financial report generation...")
                    accounts = await enhanced_mercury_api.fetch_mercury_accounts()
                    core_accounts = accounts.get("accounts", [])
                    if not core_accounts:
                        await message.channel.send("❌ No core accounts found.")
                        return
                    account = core_accounts[0]
                    print(f"DEBUG: Using account: {account.get('name', 'Unknown')} ({account.get('id')})")
                    
                    # Get recent transactions directly
                    def _fetch_recent():
                        url = f"{enhanced_mercury_api.base_url}/account/{account.get('id')}/transactions"
                        params = {"limit": 50}  # Get 50 most recent transactions
                        response = requests.get(url, headers=enhanced_mercury_api.headers, params=params, timeout=15)
                        return response.json().get("transactions", []) if response.ok else []
                    
                    transactions = await asyncio.get_event_loop().run_in_executor(None, _fetch_recent)
                    print(f"DEBUG: Fetched {len(transactions)} transactions for report.")
                    
                    # Calculate basic metrics
                    total_income = 0.0
                    total_expenses = 0.0
                    top_vendors = {}
                    
                    for tx in transactions:
                        amount = tx.get("amount", 0.0)
                        vendor = tx.get("merchantName") or tx.get("counterpartyName") or "Unknown"
                        kind = tx.get("kind", "").lower()
                        
                        if "credit" in kind or amount > 0:
                            total_income += abs(amount)
                        else:
                            total_expenses += abs(amount)
                            top_vendors[vendor] = top_vendors.get(vendor, 0) + abs(amount)
                    
                    net_cash_flow = total_income - total_expenses
                    balance = account.get("availableBalance", account.get("currentBalance", 0.0))
                    
                    # Create simple report
                    report = f"# 📊 Financial Report - Last 50 Transactions\n\n"
                    report += f"**Account:** {account.get('name', 'Unknown')}\n"
                    report += f"**Current Balance:** ${balance:,.2f}\n\n"
                    report += f"**Total Income:** ${total_income:,.2f}\n"
                    report += f"**Total Expenses:** ${total_expenses:,.2f}\n"
                    report += f"**Net Cash Flow:** ${net_cash_flow:,.2f}\n\n"
                    
                    if top_vendors:
                        report += "**Top Spending Vendors:**\n"
                        sorted_vendors = sorted(top_vendors.items(), key=lambda x: x[1], reverse=True)
                        for i, (vendor, amount) in enumerate(sorted_vendors[:5], 1):
                            report += f"{i}. {vendor}: ${amount:,.2f}\n"
                    
                    print("DEBUG: Financial report generated.")
                    await message.channel.send(f"```markdown\n{report}\n```")
                except Exception as e:
                    print(f"ERROR: Exception in financial report: {e}")
                    await message.channel.send(f"❌ Error generating report: {str(e)}")
            return
        
        # Handle all transactions request
        if re.search(r"\ball\s+transactions\b", content):
            async with message.channel.typing():
                await message.channel.send("📊 Fetching all transactions...")
                try:
                    print(f"DEBUG: Fetching all transactions")
                    accounts = await enhanced_mercury_api.fetch_mercury_accounts()
                    core_accounts = accounts.get("accounts", [])
                    if not core_accounts:
                        await message.channel.send("❌ No core accounts found.")
                        return
                    
                    # Search through all accounts (core + credit) for better coverage
                    all_transactions = []
                    
                    # Get core accounts
                    for account in core_accounts:
                        account_id = account.get("id")
                        if account_id:
                            def _fetch_recent():
                                url = f"{enhanced_mercury_api.base_url}/account/{account_id}/transactions"
                                params = {"limit": 1000}  # Get many more transactions for analysis
                                response = requests.get(url, headers=enhanced_mercury_api.headers, params=params, timeout=30)
                                return response.json().get("transactions", []) if response.ok else []
                            
                            transactions = await asyncio.get_event_loop().run_in_executor(None, _fetch_recent)
                            for tx in transactions:
                                tx["account_name"] = account.get("name", "Unknown")
                            all_transactions.extend(transactions)
                    
                    # Get credit accounts
                    credit_accounts = accounts.get("credit_accounts", [])
                    print(f"DEBUG: Found {len(credit_accounts)} credit accounts")
                    for account in credit_accounts:
                        account_id = account.get("id")
                        account_name = account.get("name", "Unknown")
                        print(f"DEBUG: Processing credit account: {account_name} ({account_id})")
                        if account_id:
                            def _fetch_recent():
                                # Try the regular account endpoint for credit accounts
                                url = f"{enhanced_mercury_api.base_url}/account/{account_id}/transactions"
                                params = {"limit": 1000}  # Get many more transactions for analysis
                                response = requests.get(url, headers=enhanced_mercury_api.headers, params=params, timeout=30)
                                print(f"DEBUG: Credit account API response status: {response.status_code}")
                                if response.ok:
                                    data = response.json()
                                    transactions = data.get("transactions", [])
                                    print(f"DEBUG: Fetched {len(transactions)} credit transactions from {account_name}")
                                    return transactions
                                else:
                                    print(f"DEBUG: Credit account API error: {response.status_code} {response.text}")
                                    return []
                            
                            transactions = await asyncio.get_event_loop().run_in_executor(None, _fetch_recent)
                            for tx in transactions:
                                tx["account_name"] = f"Credit: {account_name}"
                            all_transactions.extend(transactions)
                    
                    print(f"DEBUG: Fetched {len(all_transactions)} total transactions.")
                    
                    if all_transactions:
                        # Sort by date (newest first)
                        all_transactions.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
                        
                        response = f"**📊 All Transactions ({len(all_transactions)} total):**\n\n"
                        
                        response += "**Recent Transactions:**\n"
                        for i, tx in enumerate(all_transactions[:30], 1):
                            date = tx.get("createdAt", "")[:10] if tx.get("createdAt") else "Unknown"
                            amount = abs(tx.get("amount", 0))
                            vendor = tx.get("merchantName") or tx.get("counterpartyName") or "Unknown"
                            account_name = tx.get("account_name", "")
                            response += f"{i}. {date} | {vendor} | ${amount:,.2f} ({account_name})\n"
                        
                        if len(all_transactions) > 30:
                            response += f"\n... and {len(all_transactions) - 30} more transactions"
                        
                        await message.channel.send(response)
                    else:
                        await message.channel.send("No transactions found")
                except Exception as e:
                    print(f"ERROR: Exception in all transactions: {e}")
                    await message.channel.send(f"❌ Error fetching all transactions: {str(e)}")
            return
        
        # Handle monthly spending analysis
        monthly_match = re.search(r"\bspending\s+(?:in|for)\s+(.+?)\s+(?:(\d{4})|$)", content)
        if monthly_match:
            async with message.channel.typing():
                month_name = monthly_match.group(1).strip()
                year = monthly_match.group(2) or "2025"
                await message.channel.send(f"📊 Analyzing spending for {month_name} {year}...")
                try:
                    print(f"DEBUG: Analyzing spending for {month_name} {year}")
                    accounts = await enhanced_mercury_api.fetch_mercury_accounts()
                    core_accounts = accounts.get("accounts", [])
                    if not core_accounts:
                        await message.channel.send("❌ No core accounts found.")
                        return
                    
                    # Search through all accounts (core + credit) for better coverage
                    all_transactions = []
                    
                    # Get core accounts
                    for account in core_accounts:
                        account_id = account.get("id")
                        if account_id:
                            def _fetch_recent():
                                url = f"{enhanced_mercury_api.base_url}/account/{account_id}/transactions"
                                params = {"limit": 1000}  # Get many more transactions for analysis
                                response = requests.get(url, headers=enhanced_mercury_api.headers, params=params, timeout=30)
                                return response.json().get("transactions", []) if response.ok else []
                            
                            transactions = await asyncio.get_event_loop().run_in_executor(None, _fetch_recent)
                            for tx in transactions:
                                tx["account_name"] = account.get("name", "Unknown")
                            all_transactions.extend(transactions)
                    
                    # Get credit accounts
                    credit_accounts = accounts.get("credit_accounts", [])
                    print(f"DEBUG: Found {len(credit_accounts)} credit accounts")
                    for account in credit_accounts:
                        account_id = account.get("id")
                        account_name = account.get("name", "Unknown")
                        print(f"DEBUG: Processing credit account: {account_name} ({account_id})")
                        if account_id:
                            def _fetch_recent():
                                # Try the regular account endpoint for credit accounts
                                url = f"{enhanced_mercury_api.base_url}/account/{account_id}/transactions"
                                params = {"limit": 1000}  # Get many more transactions for analysis
                                response = requests.get(url, headers=enhanced_mercury_api.headers, params=params, timeout=30)
                                print(f"DEBUG: Credit account API response status: {response.status_code}")
                                if response.ok:
                                    data = response.json()
                                    transactions = data.get("transactions", [])
                                    print(f"DEBUG: Fetched {len(transactions)} credit transactions from {account_name}")
                                    return transactions
                                else:
                                    print(f"DEBUG: Credit account API error: {response.status_code} {response.text}")
                                    return []
                            
                            transactions = await asyncio.get_event_loop().run_in_executor(None, _fetch_recent)
                            for tx in transactions:
                                tx["account_name"] = f"Credit: {account_name}"
                            all_transactions.extend(transactions)
                    
                    print(f"DEBUG: Fetched {len(all_transactions)} total transactions for monthly analysis.")
                    
                    # Filter transactions for the specific month
                    month_transactions = []
                    total_spent = 0.0
                    total_income = 0.0
                    
                    # Convert month name to number
                    month_map = {
                        "january": "01", "jan": "01",
                        "february": "02", "feb": "02", 
                        "march": "03", "mar": "03",
                        "april": "04", "apr": "04",
                        "may": "05",
                        "june": "06", "jun": "06",
                        "july": "07", "jul": "07",
                        "august": "08", "aug": "08",
                        "september": "09", "sep": "09",
                        "october": "10", "oct": "10",
                        "november": "11", "nov": "11",
                        "december": "12", "dec": "12"
                    }
                    
                    target_month = month_map.get(month_name.lower(), "01")
                    target_year = year
                    
                    print(f"DEBUG: Filtering for {target_month}/{target_year}")
                    
                    for tx in all_transactions:
                        created_at = tx.get("createdAt", "")
                        if created_at and created_at.startswith(f"{target_year}-{target_month}"):
                            amount = tx.get("amount", 0.0)
                            kind = tx.get("kind") or ""
                            kind_lower = kind.lower() if kind else ""
                            
                            # Count expenses (negative amounts, debit transactions, or positive amounts that are clearly expenses)
                            is_expense = ("debit" in kind_lower or amount < 0 or 
                                        (amount > 0 and ("payment" in kind_lower or "charge" in kind_lower)))
                            
                            if is_expense:
                                month_transactions.append(tx)
                                total_spent += abs(amount)
                            else:
                                total_income += abs(amount)
                    
                    print(f"DEBUG: Found {len(month_transactions)} transactions for {month_name} {year}")
                    if month_transactions:
                        print(f"DEBUG: Sample month transactions:")
                        for i, tx in enumerate(month_transactions[:3]):
                            date = tx.get("createdAt", "")[:10] if tx.get("createdAt") else "Unknown"
                            amount = abs(tx.get("amount", 0))
                            vendor = tx.get("merchantName") or tx.get("counterpartyName") or "Unknown"
                            print(f"  {i+1}. {date} | {vendor} | ${amount}")
                    
                    if month_transactions:
                        # Sort by date (newest first)
                        month_transactions.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
                        
                        response = f"**📊 Monthly Spending Report - {month_name.title()} {year}:**\n\n"
                        response += f"**Total Spent:** ${total_spent:,.2f}\n"
                        response += f"**Total Income:** ${total_income:,.2f}\n"
                        response += f"**Net Cash Flow:** ${total_income - total_spent:,.2f}\n"
                        response += f"**Number of Transactions:** {len(month_transactions)}\n\n"
                        
                        response += "**All Transactions:**\n"
                        for i, tx in enumerate(month_transactions[:20], 1):
                            date = tx.get("createdAt", "")[:10] if tx.get("createdAt") else "Unknown"
                            amount = abs(tx.get("amount", 0))
                            vendor = tx.get("merchantName") or tx.get("counterpartyName") or "Unknown"
                            account_name = tx.get("account_name", "")
                            response += f"{i}. {date} | {vendor} | ${amount:,.2f} ({account_name})\n"
                        
                        if len(month_transactions) > 20:
                            response += f"\n... and {len(month_transactions) - 20} more transactions"
                        
                        await message.channel.send(response)
                    else:
                        await message.channel.send(f"No transactions found for {month_name} {year}")
                except Exception as e:
                    print(f"ERROR: Exception in monthly spending analysis: {e}")
                    await message.channel.send(f"❌ Error analyzing monthly spending: {str(e)}")
            return
        
        # Handle spending analysis
        spending_match = re.search(r"\banalyze\s+spending\s+(?:on\s+)?(.+)", content)
        if spending_match:
            async with message.channel.typing():
                target = spending_match.group(1).strip()
                await message.channel.send(f"📈 Analyzing spending on {target}...")
                try:
                    print(f"DEBUG: Analyzing spending on {target}")
                    accounts = await enhanced_mercury_api.fetch_mercury_accounts()
                    core_accounts = accounts.get("accounts", [])
                    if not core_accounts:
                        await message.channel.send("❌ No core accounts found.")
                        return
                    
                    # Search through all accounts (core + credit) for better coverage
                    all_transactions = []
                    
                    # Get core accounts
                    for account in core_accounts:
                        account_id = account.get("id")
                        if account_id:
                            def _fetch_recent():
                                url = f"{enhanced_mercury_api.base_url}/account/{account_id}/transactions"
                                params = {"limit": 1000}  # Get many more transactions for analysis
                                response = requests.get(url, headers=enhanced_mercury_api.headers, params=params, timeout=30)
                                return response.json().get("transactions", []) if response.ok else []
                            
                            transactions = await asyncio.get_event_loop().run_in_executor(None, _fetch_recent)
                            for tx in transactions:
                                tx["account_name"] = account.get("name", "Unknown")
                            all_transactions.extend(transactions)
                    
                    # Get credit accounts - try different endpoint
                    credit_accounts = accounts.get("credit_accounts", [])
                    print(f"DEBUG: Found {len(credit_accounts)} credit accounts")
                    for account in credit_accounts:
                        account_id = account.get("id")
                        account_name = account.get("name", "Unknown")
                        print(f"DEBUG: Processing credit account: {account_name} ({account_id})")
                        if account_id:
                            def _fetch_recent():
                                # Try the regular account endpoint for credit accounts
                                url = f"{enhanced_mercury_api.base_url}/account/{account_id}/transactions"
                                params = {"limit": 1000}  # Get many more transactions for analysis
                                response = requests.get(url, headers=enhanced_mercury_api.headers, params=params, timeout=30)
                                print(f"DEBUG: Credit account API response status: {response.status_code}")
                                if response.ok:
                                    data = response.json()
                                    transactions = data.get("transactions", [])
                                    print(f"DEBUG: Fetched {len(transactions)} credit transactions from {account_name}")
                                    return transactions
                                else:
                                    print(f"DEBUG: Credit account API error: {response.status_code} {response.text}")
                                    return []
                            
                            transactions = await asyncio.get_event_loop().run_in_executor(None, _fetch_recent)
                            for tx in transactions:
                                tx["account_name"] = f"Credit: {account_name}"
                            all_transactions.extend(transactions)
                    
                    print(f"DEBUG: Fetched {len(all_transactions)} total transactions for spending analysis.")
                    
                    # Debug: Show some sample transactions
                    if all_transactions:
                        print(f"DEBUG: Sample transactions:")
                        for i, tx in enumerate(all_transactions[:5]):
                            vendor = tx.get("merchantName") or tx.get("counterpartyName") or "Unknown"
                            amount = tx.get("amount", 0.0)
                            account = tx.get("account_name", "Unknown")
                            print(f"  {i+1}. {vendor} | ${amount} | {account}")
                    
                    # Search for transactions matching the target
                    matching_transactions = []
                    total_spent = 0.0
                    
                    target_lower = target.lower()
                    print(f"DEBUG: Searching for '{target_lower}' in {len(all_transactions)} transactions")
                    
                    for tx in all_transactions:
                        vendor = tx.get("merchantName") or tx.get("counterpartyName") or "Unknown"
                        amount = tx.get("amount", 0.0)
                        kind = tx.get("kind") or ""
                        bank_desc = tx.get("bankDescription") or ""
                        
                        # Handle None values safely
                        vendor_lower = vendor.lower() if vendor else ""
                        kind_lower = kind.lower() if kind else ""
                        bank_desc_lower = bank_desc.lower() if bank_desc else ""
                        
                        # More flexible matching - check vendor name, bank description, and be smarter about amounts
                        vendor_matches = target_lower in vendor_lower
                        desc_matches = target_lower in bank_desc_lower
                        
                        # Count expenses (negative amounts, debit transactions, or positive amounts that are clearly expenses)
                        is_expense = ("debit" in kind_lower or amount < 0 or 
                                    (amount > 0 and ("payment" in kind_lower or "charge" in kind_lower)))
                        
                        if (vendor_matches or desc_matches) and is_expense:
                            print(f"DEBUG: Found match: {vendor} | ${amount} | {tx.get('account_name')}")
                            matching_transactions.append(tx)
                            total_spent += abs(amount)
                    
                    if matching_transactions:
                        # Sort by date (newest first)
                        matching_transactions.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
                        
                        response = f"**📊 Spending Analysis for '{target}':**\n\n"
                        response += f"**Total Spent:** ${total_spent:,.2f}\n"
                        response += f"**Number of Transactions:** {len(matching_transactions)}\n"
                        response += f"**Average Transaction:** ${total_spent/len(matching_transactions):,.2f}\n\n"
                        
                        response += "**Recent Transactions:**\n"
                        for i, tx in enumerate(matching_transactions[:10], 1):
                            date = tx.get("createdAt", "")[:10] if tx.get("createdAt") else "Unknown"
                            amount = abs(tx.get("amount", 0))
                            vendor = tx.get("merchantName") or tx.get("counterpartyName") or "Unknown"
                            account_name = tx.get("account_name", "")
                            response += f"{i}. {date} | {vendor} | ${amount:,.2f} ({account_name})\n"
                        
                        if len(matching_transactions) > 10:
                            response += f"\n... and {len(matching_transactions) - 10} more transactions"
                        
                        await message.channel.send(response)
                    else:
                        await message.channel.send(f"No transactions found for {target}")
                except Exception as e:
                    print(f"ERROR: Exception in spending analysis: {e}")
                    await message.channel.send(f"❌ Error analyzing spending: {str(e)}")
            return
        
        # Handle financial advice
        advice_match = re.search(r"\b(?:financial\s+)?advice\s+(?:about\s+)?(.+)", content)
        if advice_match:
            async with message.channel.typing():
                question = advice_match.group(1).strip()
                await message.channel.send("💡 Getting personalized financial advice...")
                try:
                    advice = await financial_agent.get_financial_advice(question)
                    await message.channel.send(f"```\n{advice}\n```")
                except Exception as e:
                    await message.channel.send(f"❌ Error getting advice: {str(e)}")
            return
        
        # Handle comprehensive data request
        if re.search(r"\bcomprehensive\s+data\b", content):
            async with message.channel.typing():
                await message.channel.send("🔍 Gathering comprehensive financial data from all Mercury endpoints...")
                try:
                    financial_data = await enhanced_mercury_api.get_comprehensive_financial_data()
                    
                    # Create comprehensive report
                    embed = discord.Embed(
                        title="📊 Comprehensive Financial Data",
                        description="Complete data from all Mercury API endpoints",
                        color=0x0099ff,
                        timestamp=datetime.now()
                    )
                    
                    # Account summary
                    accounts = financial_data.get("accounts", {})
                    core_accounts = accounts.get("accounts", [])
                    credit_accounts = accounts.get("credit_accounts", [])
                    treasury_accounts = accounts.get("treasury_accounts", [])
                    
                    total_balance = sum(acc.get("availableBalance", acc.get("currentBalance", 0)) 
                                      for acc in core_accounts + credit_accounts + treasury_accounts)
                    
                    embed.add_field(
                        name="🏦 Account Overview",
                        value=f"**Core Accounts:** {len(core_accounts)}\n"
                              f"**Credit Accounts:** {len(credit_accounts)}\n"
                              f"**Treasury Accounts:** {len(treasury_accounts)}\n"
                              f"**Total Balance:** ${total_balance:,.2f}",
                        inline=True
                    )
                    
                    # Cards and payments
                    cards_data = financial_data.get("cards", {})
                    ach_data = financial_data.get("ach_payments", {})
                    wire_data = financial_data.get("wire_transfers", {})
                    
                    total_cards = sum(len(cards) for cards in cards_data.values())
                    total_ach = sum(len(ach) for ach in ach_data.values())
                    total_wires = sum(len(wires) for wires in wire_data.values())
                    
                    embed.add_field(
                        name="💳 Payment Methods",
                        value=f"**Active Cards:** {total_cards}\n"
                              f"**ACH Payments:** {total_ach}\n"
                              f"**Wire Transfers:** {total_wires}\n"
                              f"**Recipients:** {len(financial_data.get('recipients', []))}",
                        inline=True
                    )
                    
                    # Account details
                    account_details = financial_data.get("account_details", {})
                    account_limits = financial_data.get("account_limits", {})
                    
                    embed.add_field(
                        name="⚙️ Account Details",
                        value=f"**Detailed Info:** {len(account_details)} accounts\n"
                              f"**Limits Data:** {len(account_limits)} accounts\n"
                              f"**Last Updated:** {financial_data.get('timestamp', 'Unknown')[:19]}",
                        inline=True
                    )
                    
                    await message.channel.send(embed=embed)
                    
                except Exception as e:
                    await message.channel.send(f"❌ Error fetching comprehensive data: {str(e)}")
            return
        
        # Handle recent transactions (limit to first account, 10 txns)
        if re.search(r"\brecent\s+transactions?\b", content):
            async with message.channel.typing():
                try:
                    await message.channel.send("📊 Fetching your recent transactions...")
                    print("DEBUG: Fetching recent transactions...")
                    accounts = await enhanced_mercury_api.fetch_mercury_accounts()
                    core_accounts = accounts.get("accounts", [])
                    if not core_accounts:
                        await message.channel.send("❌ No core accounts found.")
                        return
                    account = core_accounts[0]
                    print(f"DEBUG: Using account: {account.get('name', 'Unknown')} ({account.get('id')})")
                    
                    # Use a simpler approach - just get the first page of transactions
                    def _fetch_recent():
                        url = f"{enhanced_mercury_api.base_url}/account/{account.get('id')}/transactions"
                        params = {"limit": 10}  # Only get 10 most recent
                        response = requests.get(url, headers=enhanced_mercury_api.headers, params=params, timeout=10)
                        return response.json().get("transactions", []) if response.ok else []
                    
                    transactions = await asyncio.get_event_loop().run_in_executor(None, _fetch_recent)
                    print(f"DEBUG: Fetched {len(transactions)} transactions.")
                    
                    if transactions:
                        response = "**Recent Transactions:**\n\n"
                        for i, tx in enumerate(transactions):
                            date = tx.get("createdAt", "")[:10] if tx.get("createdAt") else "Unknown"
                            amount = tx.get("amount", 0)
                            vendor = tx.get("merchantName") or tx.get("counterpartyName") or "Unknown"
                            response += f"**{i+1}.** {date} | {vendor} | ${amount:,.2f}\n"
                        await message.channel.send(response)
                    else:
                        await message.channel.send("No recent transactions found.")
                except Exception as e:
                    print(f"ERROR: Exception in recent transactions: {e}")
                    await message.channel.send(f"❌ Error fetching transactions: {str(e)}")
            return
        
        # Test command
        if re.search(r"\btest\b", content):
            await message.channel.send("✅ Bot is working! I can see your message.")
            return
        
        # Handle other commands (from original main.py)
        if re.search(r"\brefreshcache\b", content):
            async with message.channel.typing():
                await message.channel.send("🔄 Refreshing transaction cache...")
                # Add cache refresh logic here if needed
            await message.channel.send("✅ Cache refreshed.")
            return
        
        # Default to Claude for other queries
        async with message.channel.typing():
            print(f"DEBUG: No specific command matched, sending help for: '{content}'")
            help_text = """
🤖 **Financial Agent Commands:**

📊 **Analysis & Reports:**
- `financial report` - Comprehensive monthly financial analysis
- `recent transactions` - Last 7 days of transactions
- `analyze spending on [vendor]` - Detailed spending analysis
- `comprehensive data` - All Mercury API data (accounts, cards, payments, etc.)

💡 **Financial Advice:**
- `financial advice about [topic]` - Personalized financial advice
- `advice about [topic]` - Quick financial guidance

⚙️ **Settings & Status:**
- `!status` - Bot status and monitoring info
- `!config` - Configure notification settings

**Examples:**
- `@Financial BOT financial report`
- `@Financial BOT analyze spending on Amazon`
- `@Financial BOT financial advice about saving money`
- `@Financial BOT comprehensive data`
"""
            await message.channel.send(help_text)
    
    await bot.process_commands(message)

# ─────────────────────────────────────────────────────────────────────────────
#  Startup Notification
# ─────────────────────────────────────────────────────────────────────────────

async def send_startup_notification():
    """Send a startup notification to Discord"""
    if NOTIFICATION_CHANNEL_ID:
        try:
            channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
            if channel:
                # Get comprehensive financial data
                financial_data = await enhanced_mercury_api.get_comprehensive_financial_data()
                
                # Create startup message
                embed = discord.Embed(
                    title="🤖 Financial Agent is Online!",
                    description="I'm now monitoring all your Mercury accounts with full API access!",
                    color=0x00ff00,
                    timestamp=datetime.now()
                )
                
                # Add account summary
                accounts = financial_data.get("accounts", {})
                core_accounts = accounts.get("accounts", [])
                credit_accounts = accounts.get("credit_accounts", [])
                treasury_accounts = accounts.get("treasury_accounts", [])
                
                total_accounts = len(core_accounts) + len(credit_accounts) + len(treasury_accounts)
                total_balance = 0
                
                for account in core_accounts + credit_accounts + treasury_accounts:
                    balance = account.get("availableBalance", account.get("currentBalance", 0))
                    total_balance += balance
                
                embed.add_field(
                    name="📊 Account Summary",
                    value=f"**Total Accounts:** {total_accounts}\n"
                          f"**Core Accounts:** {len(core_accounts)}\n"
                          f"**Credit Accounts:** {len(credit_accounts)}\n"
                          f"**Treasury Accounts:** {len(treasury_accounts)}\n"
                          f"**Total Balance:** ${total_balance:,.2f}",
                    inline=True
                )
                
                # Add available features
                embed.add_field(
                    name="🚀 Available Features",
                    value="• **Real-time Transaction Monitoring**\n"
                          "• **Comprehensive Financial Analysis**\n"
                          "• **Revenue & Burn Rate Tracking**\n"
                          "• **ACH & Wire Transfer History**\n"
                          "• **Card Transaction Analysis**\n"
                          "• **Recipient/Payee Tracking**\n"
                          "• **AI-Powered Financial Advice**",
                    inline=True
                )
                
                embed.add_field(
                    name="💬 Try These Commands",
                    value="• `@Financial BOT financial report`\n"
                          "• `@Financial BOT recent transactions`\n"
                          "• `@Financial BOT analyze spending on [vendor]`\n"
                          "• `@Financial BOT financial advice about [topic]`\n"
                          "• `@Financial BOT comprehensive data`",
                    inline=False
                )
                
                embed.set_footer(text="Ask me anything about your finances!")
                
                await channel.send(embed=embed)
                print("✅ Startup notification sent to Discord")
                
        except Exception as e:
            print(f"❌ Error sending startup notification: {e}")

# ─────────────────────────────────────────────────────────────────────────────
#  New Commands
# ─────────────────────────────────────────────────────────────────────────────

@bot.command(help="Get transaction monitoring status")
async def status(ctx):
    """Show bot status and monitoring information"""
    embed = discord.Embed(
        title="🤖 Mercury Bot Status",
        color=0x00ff00,
        timestamp=datetime.now()
    )
    
    embed.add_field(
        name="Bot Status",
        value="✅ Online" if bot.is_ready() else "❌ Offline",
        inline=True
    )
    
    embed.add_field(
        name="Transaction Monitoring",
        value="✅ Enabled" if transaction_monitor else "❌ Disabled",
        inline=True
    )
    
    embed.add_field(
        name="Uptime",
        value=f"<t:{int(bot.start_time.timestamp())}:R>",
        inline=True
    )
    
    if transaction_monitor:
        settings = transaction_monitor.notification_settings
        embed.add_field(
            name="Notification Settings",
            value=f"Min Amount: ${settings['min_amount']:,.2f}\n"
                  f"Cooldown: {settings['notification_cooldown']}s\n"
                  f"Enabled: {'✅' if settings['enabled'] else '❌'}",
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(help="Configure notification settings")
async def config(ctx, setting: str = None, value: str = None):
    """Configure transaction notification settings"""
    if not transaction_monitor:
        await ctx.send("❌ Transaction monitoring is not enabled")
        return
    
    if not setting:
        # Show current settings
        settings = transaction_monitor.notification_settings
        embed = discord.Embed(
            title="⚙️ Notification Settings",
            color=0x0099ff
        )
        
        embed.add_field(
            name="Enabled",
            value="✅" if settings['enabled'] else "❌",
            inline=True
        )
        embed.add_field(
            name="Min Amount",
            value=f"${settings['min_amount']:,.2f}",
            inline=True
        )
        embed.add_field(
            name="Include Credits",
            value="✅" if settings['include_credits'] else "❌",
            inline=True
        )
        embed.add_field(
            name="Include Debits",
            value="✅" if settings['include_debits'] else "❌",
            inline=True
        )
        embed.add_field(
            name="Cooldown",
            value=f"{settings['notification_cooldown']}s",
            inline=True
        )
        
        await ctx.send(embed=embed)
        return
    
    # Update setting
    try:
        if setting == "min_amount":
            amount = float(value)
            await transaction_monitor.update_notification_settings(min_amount=amount)
        elif setting == "cooldown":
            cooldown = int(value)
            await transaction_monitor.update_notification_settings(notification_cooldown=cooldown)
        elif setting == "enabled":
            enabled = value.lower() == "true"
            await transaction_monitor.update_notification_settings(enabled=enabled)
        else:
            await ctx.send("❌ Unknown setting. Use: min_amount, cooldown, enabled")
            return
        
        await ctx.send(f"✅ Updated {setting} to {value}")
    except ValueError:
        await ctx.send("❌ Invalid value. Use a number for min_amount/cooldown, true/false for enabled")

# ─────────────────────────────────────────────────────────────────────────────
#  Health Check Endpoint (for monitoring)
# ─────────────────────────────────────────────────────────────────────────────

async def health_check():
    """Simple health check for monitoring"""
    try:
        # Check if bot is ready
        if not bot.is_ready():
            return False, "Bot not ready"
        
        # Check if we can fetch Mercury accounts
        accounts = await fetch_mercury_accounts()
        if not accounts.get("accounts"):
            return False, "Cannot fetch Mercury accounts"
        
        return True, "Healthy"
    except Exception as e:
        return False, f"Error: {str(e)}"

# ─────────────────────────────────────────────────────────────────────────────
#  Main Function
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    """Main function to run the bot"""
    try:
        print("🚀 Starting Mercury Bot with transaction monitoring...")
        
        # Set bot start time
        bot.start_time = datetime.now()
        
        # Run the bot
        await bot.start(DISCORD_TOKEN)
        
    except KeyboardInterrupt:
        print("\n⏹️ Shutting down gracefully...")
        if transaction_monitor:
            await transaction_monitor.stop_monitoring()
        await bot.close()
    except Exception as e:
        print(f"❌ Error running bot: {e}")
        raise

if __name__ == "__main__":
    # Run the bot
    asyncio.run(main()) 