# Setup Guide: Transaction Monitoring & 24/7 Operation

## 🚀 Quick Setup

### 1. **Add Environment Variable**
Add this to your `.env` file:
```bash
NOTIFICATION_CHANNEL_ID=your_discord_channel_id_here
```

To get your Discord channel ID:
1. Enable Developer Mode in Discord (User Settings > Advanced > Developer Mode)
2. Right-click on the channel where you want notifications
3. Click "Copy ID"
4. Paste it in your `.env` file

### 2. **Add Transaction Monitoring to Your Bot**

Add this code to your existing `main.py`:

```python
# Add after your imports
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Set

# Add after your environment variables
NOTIFICATION_CHANNEL_ID = int(os.getenv("NOTIFICATION_CHANNEL_ID", "0"))

# Add this class before your bot setup
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
        self.monitor_transactions.start()
        print("🔄 Transaction monitoring started")
    
    @tasks.loop(minutes=1)
    async def monitor_transactions(self):
        try:
            await self.check_for_new_transactions()
        except Exception as e:
            print(f"❌ Error in transaction monitoring: {e}")
    
    @monitor_transactions.before_loop
    async def before_monitor_transactions(self):
        await self.bot.wait_until_ready()
        await self.initialize_transaction_cache()
    
    async def initialize_transaction_cache(self):
        # Initialize with current transactions to avoid duplicate notifications
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(hours=24)
        
        accounts = await fetch_mercury_accounts()
        all_accounts = accounts.get("accounts", []) + accounts.get("credit_accounts", [])
        
        for account in all_accounts:
            account_id = account.get("id")
            if not account_id:
                continue
            
            transactions = await fetch_all_tx_for_account(account_id, after=start_date.isoformat())
            for tx in transactions:
                self.last_checked_transactions.add(tx.get("id"))
        
        print(f"📋 Initialized transaction cache with {len(self.last_checked_transactions)} transactions")
    
    async def check_for_new_transactions(self):
        accounts = await fetch_mercury_accounts()
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
            
            transactions = await fetch_all_tx_for_account(account_id, after=start_date.isoformat())
            
            for tx in transactions:
                tx_id = tx.get("id")
                
                if tx_id not in self.last_checked_transactions:
                    self.last_checked_transactions.add(tx_id)
                    tx["account_name"] = account_name
                    tx["account_type"] = "credit" if account in accounts.get("credit_accounts", []) else "core"
                    new_transactions.append(tx)
        
        if new_transactions:
            await self.send_transaction_notifications(new_transactions)
    
    async def send_transaction_notifications(self, transactions: List[Dict]):
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
        if vendor not in self.last_notification_time:
            return True
        
        last_time = self.last_notification_time[vendor]
        current_time = datetime.now().timestamp()
        cooldown = self.notification_settings['notification_cooldown']
        
        return (current_time - last_time) >= cooldown
    
    def get_vendor_name(self, tx: Dict) -> str:
        return (
            tx.get("merchantName") or 
            tx.get("counterpartyName") or 
            tx.get("bankDescription") or 
            (tx.get("cardDetails") or {}).get("merchantName") or 
            "Unknown Vendor"
        )
    
    async def create_transaction_embed(self, tx: Dict) -> discord.Embed:
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

# Add after bot initialization
transaction_monitor = None

# Modify your on_ready event
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

# Add new commands
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
```

### 3. **Run 24/7 with Keep Alive Script**

Use the `keep_alive.py` script to keep your bot running:

```bash
# Make it executable
chmod +x keep_alive.py

# Run it
python keep_alive.py
```

Or run it directly:
```bash
python keep_alive.py
```

## 🔧 Configuration Options

### **Notification Settings**
You can configure these settings in the `TransactionMonitor` class:

- `min_amount`: Minimum transaction amount to notify (default: $0.0)
- `include_credits`: Notify for incoming money (default: True)
- `include_debits`: Notify for outgoing money (default: True)
- `notification_cooldown`: Seconds between notifications for same vendor (default: 300)

### **Commands Available**
- `!status` - Check bot and monitoring status
- `!toggle_notifications` - Turn notifications on/off

## 🚀 Deployment Options

### **Option 1: Local Machine (Simple)**
```bash
# Run with keep alive script
python keep_alive.py
```

### **Option 2: Screen Session (Linux/Mac)**
```bash
# Create a screen session
screen -S mercury-bot

# Run the bot
python keep_alive.py

# Detach from screen (Ctrl+A, then D)
# To reattach: screen -r mercury-bot
```

### **Option 3: Systemd Service (Linux)**
```bash
# Create service file
sudo nano /etc/systemd/system/mercury-bot.service
```

Add this content:
```ini
[Unit]
Description=Mercury Bot Discord Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/mercury-bot
ExecStart=/usr/bin/python3 keep_alive.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
# Enable and start the service
sudo systemctl enable mercury-bot
sudo systemctl start mercury-bot

# Check status
sudo systemctl status mercury-bot

# View logs
sudo journalctl -u mercury-bot -f
```

### **Option 4: Docker**
```bash
# Build and run with Docker
docker build -t mercury-bot .
docker run -d --name mercury-bot --restart unless-stopped mercury-bot
```

## 📊 What You'll Get

### **Real-time Transaction Notifications**
- Instant notifications for every transaction
- Rich Discord embeds with transaction details
- Color-coded (green for credits, red for debits)
- Vendor, account, amount, and category information

### **24/7 Operation**
- Automatic restart on crashes
- Logging of all bot activity
- Graceful shutdown handling
- Multiple deployment options

### **Smart Features**
- Duplicate notification prevention
- Vendor cooldown to avoid spam
- Configurable minimum amounts
- Separate settings for credits/debits

## 🔍 Monitoring

### **Check Bot Status**
```bash
# View logs
tail -f mercury_bot.log

# Check if bot is running
ps aux | grep python

# Check Discord bot status
# Use !status command in Discord
```

### **Troubleshooting**
1. **Bot not starting**: Check your `.env` file and API keys
2. **No notifications**: Verify `NOTIFICATION_CHANNEL_ID` is correct
3. **Too many notifications**: Increase `notification_cooldown` or `min_amount`
4. **Bot crashes**: Check logs for error messages

## 🎯 Next Steps

Once you have transaction monitoring working, you can:

1. **Add more features** from the enhancement plan
2. **Customize notification settings** for your needs
3. **Set up monitoring** for the monitoring script
4. **Add more commands** for configuration

Your Mercury bot will now notify you of every transaction in real-time and run 24/7! 🎉 