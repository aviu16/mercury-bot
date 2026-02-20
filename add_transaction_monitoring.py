# Add Transaction Monitoring to Existing Mercury Bot
# This script shows how to add real-time transaction notifications

import os
import asyncio
import discord
from discord.ext import tasks
from datetime import datetime, timezone, timedelta
import json
from typing import Dict, List, Set

# Add this to your existing main.py

class TransactionMonitor:
    def __init__(self, bot, mercury_api, notification_channel_id: int):
        self.bot = bot
        self.mercury_api = mercury_api
        self.notification_channel_id = notification_channel_id
        self.last_checked_transactions: Set[str] = set()
        self.notification_settings = {
            'enabled': True,
            'min_amount': 0.0,
            'include_credits': True,
            'include_debits': True,
            'notification_cooldown': 300
        }
        self.last_notification_time: Dict[str, float] = {}
        
    async def start_monitoring(self):
        """Start the transaction monitoring loop"""
        self.monitor_transactions.start()
        print("🔄 Transaction monitoring started")
    
    @tasks.loop(minutes=1)  # Check every minute
    async def monitor_transactions(self):
        """Monitor for new transactions and send notifications"""
        try:
            await self.check_for_new_transactions()
        except Exception as e:
            print(f"❌ Error in transaction monitoring: {e}")
    
    @monitor_transactions.before_loop
    async def before_monitor_transactions(self):
        """Wait for bot to be ready before starting monitoring"""
        await self.bot.wait_until_ready()
        await self.initialize_transaction_cache()
    
    async def initialize_transaction_cache(self):
        """Initialize the cache with current transactions"""
        try:
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(hours=24)
            
            accounts = await self.mercury_api.fetch_mercury_accounts()
            all_accounts = accounts.get("accounts", []) + accounts.get("credit_accounts", [])
            
            for account in all_accounts:
                account_id = account.get("id")
                if not account_id:
                    continue
                
                transactions = await self.mercury_api.fetch_all_tx_for_account(
                    account_id, after=start_date.isoformat()
                )
                
                for tx in transactions:
                    self.last_checked_transactions.add(tx.get("id"))
            
            print(f"📋 Initialized transaction cache with {len(self.last_checked_transactions)} transactions")
            
        except Exception as e:
            print(f"❌ Error initializing transaction cache: {e}")
    
    async def check_for_new_transactions(self):
        """Check for new transactions and send notifications"""
        try:
            accounts = await self.mercury_api.fetch_mercury_accounts()
            all_accounts = accounts.get("accounts", []) + accounts.get("credit_accounts", [])
            
            new_transactions = []
            
            for account in all_accounts:
                account_id = account.get("id")
                account_name = account.get("name", account.get("nickname", "Unknown"))
                
                if not account_id:
                    continue
                
                # Get recent transactions (last 5 minutes)
                end_date = datetime.now(timezone.utc)
                start_date = end_date - timedelta(minutes=5)
                
                transactions = await self.mercury_api.fetch_all_tx_for_account(
                    account_id, after=start_date.isoformat()
                )
                
                for tx in transactions:
                    tx_id = tx.get("id")
                    
                    if tx_id not in self.last_checked_transactions:
                        self.last_checked_transactions.add(tx_id)
                        tx["account_name"] = account_name
                        tx["account_type"] = "credit" if account in accounts.get("credit_accounts", []) else "core"
                        new_transactions.append(tx)
            
            if new_transactions:
                await self.send_transaction_notifications(new_transactions)
                
        except Exception as e:
            print(f"❌ Error checking for new transactions: {e}")
    
    async def send_transaction_notifications(self, transactions: List[Dict]):
        """Send notifications for new transactions"""
        channel = self.bot.get_channel(self.notification_channel_id)
        if not channel:
            print(f"❌ Notification channel {self.notification_channel_id} not found")
            return
        
        for tx in transactions:
            if not self.should_notify_transaction(tx):
                continue
            
            vendor = self.get_vendor_name(tx)
            if not self.can_notify_vendor(vendor):
                continue
            
            embed = await self.create_transaction_embed(tx)
            await channel.send(embed=embed)
            
            self.last_notification_time[vendor] = datetime.now().timestamp()
    
    def should_notify_transaction(self, tx: Dict) -> bool:
        """Check if transaction should trigger notification"""
        if not self.notification_settings['enabled']:
            return False
        
        amount = abs(tx.get("amount", 0))
        if amount < self.notification_settings['min_amount']:
            return False
        
        kind = tx.get("kind", "").lower()
        if "credit" in kind and not self.notification_settings['include_credits']:
            return False
        if "debit" in kind and not self.notification_settings['include_debits']:
            return False
        
        return True
    
    def can_notify_vendor(self, vendor: str) -> bool:
        """Check cooldown for vendor"""
        if vendor not in self.last_notification_time:
            return True
        
        last_time = self.last_notification_time[vendor]
        current_time = datetime.now().timestamp()
        cooldown = self.notification_settings['notification_cooldown']
        
        return (current_time - last_time) >= cooldown
    
    def get_vendor_name(self, tx: Dict) -> str:
        """Extract vendor name from transaction"""
        return (
            tx.get("merchantName") or 
            tx.get("counterpartyName") or 
            tx.get("bankDescription") or 
            (tx.get("cardDetails") or {}).get("merchantName") or 
            "Unknown Vendor"
        )
    
    async def create_transaction_embed(self, tx: Dict) -> discord.Embed:
        """Create a rich embed for transaction notification"""
        amount = tx.get("amount", 0)
        kind = tx.get("kind", "").lower()
        vendor = self.get_vendor_name(tx)
        account_name = tx.get("account_name", "Unknown Account")
        account_type = tx.get("account_type", "core")
        
        if "credit" in kind:
            color = 0x00ff00  # Green
            emoji = "💰"
            action = "Received"
        else:
            color = 0xff0000  # Red
            emoji = "💸"
            action = "Spent"
        
        embed = discord.Embed(
            title=f"{emoji} New Transaction",
            description=f"**{action}** ${abs(amount):,.2f}",
            color=color,
            timestamp=datetime.now()
        )
        
        embed.add_field(name="🏢 Vendor", value=vendor, inline=True)
        embed.add_field(name="🏦 Account", value=f"{account_name} ({account_type})", inline=True)
        embed.add_field(
            name="📅 Date", 
            value=datetime.fromisoformat(tx.get("createdAt").replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M"),
            inline=True
        )
        
        category = tx.get("mercuryCategory")
        if category:
            embed.add_field(name="📂 Category", value=category.title(), inline=True)
        
        description = tx.get("bankDescription")
        if description and description != vendor:
            embed.add_field(
                name="📝 Description",
                value=description[:100] + "..." if len(description) > 100 else description,
                inline=False
            )
        
        embed.set_footer(text=f"Transaction ID: {tx.get('id', 'Unknown')}")
        return embed

# Add these lines to your existing main.py:

# 1. Add to your environment variables:
# NOTIFICATION_CHANNEL_ID=your_discord_channel_id

# 2. Add after bot initialization:
# transaction_monitor = None

# 3. Add to your on_ready event:
"""
@bot.event
async def on_ready():
    print(f"🤖 {bot.user} is online and ready!")
    
    # Initialize database
    await init_db()
    
    # Start transaction monitoring if channel is configured
    global transaction_monitor
    if NOTIFICATION_CHANNEL_ID:
        transaction_monitor = TransactionMonitor(bot, {
            'fetch_mercury_accounts': fetch_mercury_accounts,
            'fetch_all_tx_for_account': fetch_all_tx_for_account
        }, NOTIFICATION_CHANNEL_ID)
        await transaction_monitor.start_monitoring()
        print(f"🔔 Transaction monitoring enabled for channel {NOTIFICATION_CHANNEL_ID}")
    else:
        print("⚠️  NOTIFICATION_CHANNEL_ID not set - transaction monitoring disabled")
"""

# 4. Add new commands:
"""
@bot.command(help="Get transaction monitoring status")
async def status(ctx):
    embed = discord.Embed(title="🤖 Mercury Bot Status", color=0x00ff00)
    embed.add_field(name="Transaction Monitoring", 
                   value="✅ Enabled" if transaction_monitor else "❌ Disabled", inline=True)
    await ctx.send(embed=embed)

@bot.command(help="Toggle transaction notifications")
async def toggle_notifications(ctx):
    if transaction_monitor:
        new_state = not transaction_monitor.notification_settings['enabled']
        transaction_monitor.notification_settings['enabled'] = new_state
        status = "enabled" if new_state else "disabled"
        await ctx.send(f"🔔 Transaction notifications {status}")
    else:
        await ctx.send("❌ Transaction monitoring is not enabled")
""" 