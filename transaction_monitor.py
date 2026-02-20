# Real-time Transaction Monitor
# Monitors for new transactions and sends notifications

import asyncio
import discord
from discord.ext import tasks
from datetime import datetime, timezone, timedelta
import sqlite3
import json
import os
from typing import Dict, List, Set

class TransactionMonitor:
    def __init__(self, bot, mercury_api, notification_channel_id: int):
        self.bot = bot
        self.mercury_api = mercury_api
        self.notification_channel_id = notification_channel_id
        self.last_checked_transactions: Set[str] = set()
        self.notification_settings = {
            'enabled': True,
            'min_amount': 0.0,  # Minimum amount to notify
            'include_credits': True,
            'include_debits': True,
            'exclude_categories': [],  # Categories to exclude
            'exclude_vendors': [],     # Vendors to exclude
            'notification_cooldown': 300  # Seconds between notifications for same vendor
        }
        self.last_notification_time: Dict[str, float] = {}
        
    async def start_monitoring(self):
        """Start the transaction monitoring loop"""
        self.monitor_transactions.start()
        print("🔄 Transaction monitoring started")
    
    async def stop_monitoring(self):
        """Stop the transaction monitoring loop"""
        self.monitor_transactions.cancel()
        print("⏹️ Transaction monitoring stopped")
    
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
        # Initialize with current transactions
        await self.initialize_transaction_cache()
    
    async def initialize_transaction_cache(self):
        """Initialize the cache with current transactions to avoid duplicate notifications"""
        try:
            # Get recent transactions (last 24 hours)
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
            # Get all accounts
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
                    
                    # Check if this is a new transaction
                    if tx_id not in self.last_checked_transactions:
                        self.last_checked_transactions.add(tx_id)
                        
                        # Add account info to transaction
                        tx["account_name"] = account_name
                        tx["account_type"] = "credit" if account in accounts.get("credit_accounts", []) else "core"
                        
                        new_transactions.append(tx)
            
            # Send notifications for new transactions
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
            # Check notification settings
            if not self.should_notify_transaction(tx):
                continue
            
            # Check cooldown for vendor
            vendor = self.get_vendor_name(tx)
            if not self.can_notify_vendor(vendor):
                continue
            
            # Create and send notification
            embed = await self.create_transaction_embed(tx)
            await channel.send(embed=embed)
            
            # Update cooldown
            self.last_notification_time[vendor] = datetime.now().timestamp()
    
    def should_notify_transaction(self, tx: Dict) -> bool:
        """Check if transaction should trigger notification based on settings"""
        if not self.notification_settings['enabled']:
            return False
        
        amount = abs(tx.get("amount", 0))
        if amount < self.notification_settings['min_amount']:
            return False
        
        # Check transaction type
        kind = tx.get("kind", "").lower()
        if "credit" in kind and not self.notification_settings['include_credits']:
            return False
        if "debit" in kind and not self.notification_settings['include_debits']:
            return False
        
        # Check excluded categories
        category = tx.get("mercuryCategory", "").lower()
        if category in [cat.lower() for cat in self.notification_settings['exclude_categories']]:
            return False
        
        # Check excluded vendors
        vendor = self.get_vendor_name(tx).lower()
        if vendor in [v.lower() for v in self.notification_settings['exclude_vendors']]:
            return False
        
        return True
    
    def can_notify_vendor(self, vendor: str) -> bool:
        """Check if enough time has passed since last notification for this vendor"""
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
        
        # Determine color and emoji based on transaction type
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
        
        embed.add_field(
            name="🏢 Vendor",
            value=vendor,
            inline=True
        )
        
        embed.add_field(
            name="🏦 Account",
            value=f"{account_name} ({account_type})",
            inline=True
        )
        
        embed.add_field(
            name="📅 Date",
            value=datetime.fromisoformat(tx.get("createdAt").replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M"),
            inline=True
        )
        
        # Add category if available
        category = tx.get("mercuryCategory")
        if category:
            embed.add_field(
                name="📂 Category",
                value=category.title(),
                inline=True
            )
        
        # Add description if available
        description = tx.get("bankDescription")
        if description and description != vendor:
            embed.add_field(
                name="📝 Description",
                value=description[:100] + "..." if len(description) > 100 else description,
                inline=False
            )
        
        # Add footer with transaction ID
        embed.set_footer(text=f"Transaction ID: {tx.get('id', 'Unknown')}")
        
        return embed
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Notification Settings Management
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def update_notification_settings(self, **kwargs):
        """Update notification settings"""
        self.notification_settings.update(kwargs)
        await self.save_notification_settings()
    
    async def save_notification_settings(self):
        """Save notification settings to file"""
        try:
            with open("notification_settings.json", "w") as f:
                json.dump(self.notification_settings, f, indent=2)
        except Exception as e:
            print(f"❌ Error saving notification settings: {e}")
    
    async def load_notification_settings(self):
        """Load notification settings from file"""
        try:
            if os.path.exists("notification_settings.json"):
                with open("notification_settings.json", "r") as f:
                    self.notification_settings = json.load(f)
        except Exception as e:
            print(f"❌ Error loading notification settings: {e}")
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Discord Commands for Settings
    # ─────────────────────────────────────────────────────────────────────────────
    
    @discord.ext.commands.command(help="Configure transaction notifications")
    async def notify_settings(self, ctx, setting: str = None, value: str = None):
        """Configure transaction notification settings"""
        if not setting:
            # Show current settings
            embed = discord.Embed(
                title="🔔 Notification Settings",
                color=0x0099ff
            )
            
            embed.add_field(
                name="Enabled",
                value="✅" if self.notification_settings['enabled'] else "❌",
                inline=True
            )
            embed.add_field(
                name="Min Amount",
                value=f"${self.notification_settings['min_amount']:,.2f}",
                inline=True
            )
            embed.add_field(
                name="Include Credits",
                value="✅" if self.notification_settings['include_credits'] else "❌",
                inline=True
            )
            embed.add_field(
                name="Include Debits",
                value="✅" if self.notification_settings['include_debits'] else "❌",
                inline=True
            )
            embed.add_field(
                name="Cooldown",
                value=f"{self.notification_settings['notification_cooldown']}s",
                inline=True
            )
            
            await ctx.send(embed=embed)
            return
        
        # Update specific setting
        if setting == "enabled":
            await self.update_notification_settings(enabled=value.lower() == "true")
        elif setting == "min_amount":
            try:
                amount = float(value)
                await self.update_notification_settings(min_amount=amount)
            except ValueError:
                await ctx.send("❌ Invalid amount. Use a number.")
                return
        elif setting == "include_credits":
            await self.update_notification_settings(include_credits=value.lower() == "true")
        elif setting == "include_debits":
            await self.update_notification_settings(include_debits=value.lower() == "true")
        elif setting == "cooldown":
            try:
                cooldown = int(value)
                await self.update_notification_settings(notification_cooldown=cooldown)
            except ValueError:
                await ctx.send("❌ Invalid cooldown. Use a number of seconds.")
                return
        else:
            await ctx.send("❌ Unknown setting. Use: enabled, min_amount, include_credits, include_debits, cooldown")
            return
        
        await ctx.send(f"✅ Updated {setting} to {value}")
    
    @discord.ext.commands.command(help="Toggle transaction notifications")
    async def toggle_notifications(self, ctx):
        """Toggle transaction notifications on/off"""
        new_state = not self.notification_settings['enabled']
        await self.update_notification_settings(enabled=new_state)
        
        status = "enabled" if new_state else "disabled"
        await ctx.send(f"🔔 Transaction notifications {status}") 