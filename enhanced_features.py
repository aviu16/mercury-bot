# Enhanced Features for Mercury Bot
# This file contains new features and improvements

import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timezone, timedelta
import matplotlib.pyplot as plt
import io
import json

class EnhancedMercuryBot:
    def __init__(self, bot, mercury_api):
        self.bot = bot
        self.mercury_api = mercury_api
        
    # ─────────────────────────────────────────────────────────────────────────────
    #  Enhanced Discord Commands
    # ─────────────────────────────────────────────────────────────────────────────
    
    @commands.command(help="Get comprehensive financial summary")
    async def summary(self, ctx, period="month"):
        """Get detailed financial summary for specified period"""
        await ctx.trigger_typing()
        
        # Calculate period dates
        end_date = datetime.now(timezone.utc)
        if period == "week":
            start_date = end_date - timedelta(days=7)
        elif period == "month":
            start_date = end_date - timedelta(days=30)
        elif period == "quarter":
            start_date = end_date - timedelta(days=90)
        else:
            await ctx.send("❌ Invalid period. Use: week, month, or quarter")
            return
        
        # Get account IDs
        accounts = await self.mercury_api.fetch_mercury_accounts()
        account_ids = [acc["id"] for acc in accounts.get("accounts", [])]
        
        # Calculate metrics
        revenue_data = await self.mercury_api.calculate_revenue(
            account_ids, start_date.isoformat(), end_date.isoformat()
        )
        burn_data = await self.mercury_api.calculate_burn_rate(
            account_ids, start_date.isoformat(), end_date.isoformat()
        )
        runway_data = await self.mercury_api.calculate_runway(account_ids)
        
        # Create rich embed
        embed = discord.Embed(
            title=f"📊 Financial Summary - {period.capitalize()}",
            color=0x00ff00 if revenue_data["total_revenue"] > burn_data["total_burn"] else 0xff0000,
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name="💰 Revenue",
            value=f"${revenue_data['total_revenue']:,.2f}",
            inline=True
        )
        embed.add_field(
            name="🔥 Burn Rate",
            value=f"${burn_data['total_burn']:,.2f}",
            inline=True
        )
        embed.add_field(
            name="📈 Net Cash Flow",
            value=f"${revenue_data['total_revenue'] - burn_data['total_burn']:,.2f}",
            inline=True
        )
        embed.add_field(
            name="⏰ Runway",
            value=f"{runway_data['runway_months']:.1f} months",
            inline=True
        )
        embed.add_field(
            name="💳 Total Cash",
            value=f"${runway_data['total_cash']:,.2f}",
            inline=True
        )
        embed.add_field(
            name="📅 Monthly Burn",
            value=f"${runway_data['monthly_burn_rate']:,.2f}",
            inline=True
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(help="Calculate runway based on current burn rate")
    async def runway(self, ctx):
        """Show current runway analysis"""
        await ctx.trigger_typing()
        
        accounts = await self.mercury_api.fetch_mercury_accounts()
        account_ids = [acc["id"] for acc in accounts.get("accounts", [])]
        
        runway_data = await self.mercury_api.calculate_runway(account_ids)
        
        # Create runway visualization
        fig, ax = plt.subplots(figsize=(10, 6))
        
        months = list(range(1, int(runway_data['runway_months']) + 2))
        cash_remaining = []
        
        for month in months:
            remaining = runway_data['total_cash'] - (runway_data['monthly_burn_rate'] * (month - 1))
            cash_remaining.append(max(0, remaining))
        
        ax.plot(months, cash_remaining, 'b-', linewidth=2, label='Cash Remaining')
        ax.fill_between(months, cash_remaining, alpha=0.3, color='blue')
        ax.axhline(y=0, color='red', linestyle='--', label='Zero Cash')
        ax.set_xlabel('Months')
        ax.set_ylabel('Cash ($)')
        ax.set_title('Runway Projection')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        # Save plot to buffer
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=300, bbox_inches='tight')
        buf.seek(0)
        plt.close()
        
        # Create embed with chart
        embed = discord.Embed(
            title="⏰ Runway Analysis",
            description=f"Based on current burn rate of ${runway_data['monthly_burn_rate']:,.2f}/month",
            color=0x00ff00 if runway_data['runway_months'] > 12 else 0xffa500 if runway_data['runway_months'] > 6 else 0xff0000
        )
        
        embed.add_field(
            name="Current Cash",
            value=f"${runway_data['total_cash']:,.2f}",
            inline=True
        )
        embed.add_field(
            name="Monthly Burn",
            value=f"${runway_data['monthly_burn_rate']:,.2f}",
            inline=True
        )
        embed.add_field(
            name="Runway",
            value=f"{runway_data['runway_months']:.1f} months ({runway_data['runway_days']:.0f} days)",
            inline=True
        )
        
        file = discord.File(buf, filename='runway.png')
        embed.set_image(url="attachment://runway.png")
        
        await ctx.send(embed=embed, file=file)
    
    @commands.command(help="Revenue analysis for specified period")
    async def revenue(self, ctx, period="month"):
        """Show detailed revenue breakdown"""
        await ctx.trigger_typing()
        
        end_date = datetime.now(timezone.utc)
        if period == "week":
            start_date = end_date - timedelta(days=7)
        elif period == "month":
            start_date = end_date - timedelta(days=30)
        elif period == "quarter":
            start_date = end_date - timedelta(days=90)
        else:
            await ctx.send("❌ Invalid period. Use: week, month, or quarter")
            return
        
        accounts = await self.mercury_api.fetch_mercury_accounts()
        account_ids = [acc["id"] for acc in accounts.get("accounts", [])]
        
        revenue_data = await self.mercury_api.calculate_revenue(
            account_ids, start_date.isoformat(), end_date.isoformat()
        )
        
        embed = discord.Embed(
            title=f"💰 Revenue Analysis - {period.capitalize()}",
            description=f"Period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
            color=0x00ff00
        )
        
        embed.add_field(
            name="Total Revenue",
            value=f"${revenue_data['total_revenue']:,.2f}",
            inline=False
        )
        
        # Show revenue by account
        for account_id, amount in revenue_data['revenue_by_account'].items():
            if amount > 0:
                account_name = next((acc['name'] for acc in accounts.get("accounts", []) if acc['id'] == account_id), account_id)
                embed.add_field(
                    name=f"📊 {account_name}",
                    value=f"${amount:,.2f}",
                    inline=True
                )
        
        await ctx.send(embed=embed)
    
    @commands.command(help="Burn rate analysis for specified period")
    async def burn(self, ctx, period="month"):
        """Show detailed burn rate analysis"""
        await ctx.trigger_typing()
        
        end_date = datetime.now(timezone.utc)
        if period == "week":
            start_date = end_date - timedelta(days=7)
        elif period == "month":
            start_date = end_date - timedelta(days=30)
        elif period == "quarter":
            start_date = end_date - timedelta(days=90)
        else:
            await ctx.send("❌ Invalid period. Use: week, month, or quarter")
            return
        
        accounts = await self.mercury_api.fetch_mercury_accounts()
        account_ids = [acc["id"] for acc in accounts.get("accounts", [])]
        
        burn_data = await self.mercury_api.calculate_burn_rate(
            account_ids, start_date.isoformat(), end_date.isoformat()
        )
        
        embed = discord.Embed(
            title=f"🔥 Burn Rate Analysis - {period.capitalize()}",
            description=f"Period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
            color=0xff0000
        )
        
        embed.add_field(
            name="Total Burn",
            value=f"${burn_data['total_burn']:,.2f}",
            inline=False
        )
        
        # Show top spending categories
        sorted_categories = sorted(burn_data['burn_by_category'].items(), 
                                 key=lambda x: x[1], reverse=True)[:5]
        
        for category, amount in sorted_categories:
            percentage = (amount / burn_data['total_burn']) * 100
            embed.add_field(
                name=f"📊 {category.title()}",
                value=f"${amount:,.2f} ({percentage:.1f}%)",
                inline=True
            )
        
        await ctx.send(embed=embed)
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Smart Alerts & Notifications
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def check_low_balance_alerts(self, channel):
        """Check for low balance alerts"""
        accounts = await self.mercury_api.fetch_mercury_accounts()
        
        for account in accounts.get("accounts", []):
            balance = account.get("availableBalance", 0)
            name = account.get("name", "Unknown Account")
            
            # Customizable thresholds
            if balance < 1000:  # Critical
                embed = discord.Embed(
                    title="🚨 Critical Low Balance Alert",
                    description=f"Account: {name}",
                    color=0xff0000
                )
                embed.add_field(name="Current Balance", value=f"${balance:,.2f}")
                await channel.send(embed=embed)
            elif balance < 5000:  # Warning
                embed = discord.Embed(
                    title="⚠️ Low Balance Warning",
                    description=f"Account: {name}",
                    color=0xffa500
                )
                embed.add_field(name="Current Balance", value=f"${balance:,.2f}")
                await channel.send(embed=embed)
    
    async def check_runway_alerts(self, channel):
        """Check for runway alerts"""
        accounts = await self.mercury_api.fetch_mercury_accounts()
        account_ids = [acc["id"] for acc in accounts.get("accounts", [])]
        
        runway_data = await self.mercury_api.calculate_runway(account_ids)
        runway_months = runway_data['runway_months']
        
        if runway_months < 3:  # Critical
            embed = discord.Embed(
                title="🚨 Critical Runway Alert",
                description="Your runway is critically low!",
                color=0xff0000
            )
            embed.add_field(name="Runway", value=f"{runway_months:.1f} months")
            embed.add_field(name="Monthly Burn", value=f"${runway_data['monthly_burn_rate']:,.2f}")
            await channel.send(embed=embed)
        elif runway_months < 6:  # Warning
            embed = discord.Embed(
                title="⚠️ Runway Warning",
                description="Your runway is getting low",
                color=0xffa500
            )
            embed.add_field(name="Runway", value=f"{runway_months:.1f} months")
            embed.add_field(name="Monthly Burn", value=f"${runway_data['monthly_burn_rate']:,.2f}")
            await channel.send(embed=embed)
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Advanced Analytics
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def generate_spending_trends(self, ctx, months=6):
        """Generate spending trend analysis"""
        await ctx.trigger_typing()
        
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=months * 30)
        
        accounts = await self.mercury_api.fetch_mercury_accounts()
        account_ids = [acc["id"] for acc in accounts.get("accounts", [])]
        
        # Get monthly data
        monthly_data = []
        for i in range(months):
            month_start = end_date - timedelta(days=(i + 1) * 30)
            month_end = end_date - timedelta(days=i * 30)
            
            burn_data = await self.mercury_api.calculate_burn_rate(
                account_ids, month_start.isoformat(), month_end.isoformat()
            )
            
            monthly_data.append({
                'month': month_start.strftime('%Y-%m'),
                'burn': burn_data['total_burn']
            })
        
        # Create trend chart
        months_list = [data['month'] for data in reversed(monthly_data)]
        burn_list = [data['burn'] for data in reversed(monthly_data)]
        
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(months_list, burn_list, 'r-o', linewidth=2, markersize=8)
        ax.set_xlabel('Month')
        ax.set_ylabel('Monthly Burn ($)')
        ax.set_title('Spending Trends')
        ax.grid(True, alpha=0.3)
        plt.xticks(rotation=45)
        
        # Save plot
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=300, bbox_inches='tight')
        buf.seek(0)
        plt.close()
        
        # Calculate trend
        if len(burn_list) > 1:
            trend = (burn_list[-1] - burn_list[0]) / len(burn_list)
            trend_text = f"Trend: ${trend:,.2f}/month"
            color = 0x00ff00 if trend < 0 else 0xff0000
        else:
            trend_text = "Insufficient data for trend analysis"
            color = 0x808080
        
        embed = discord.Embed(
            title="📈 Spending Trends",
            description=trend_text,
            color=color
        )
        
        file = discord.File(buf, filename='trends.png')
        embed.set_image(url="attachment://trends.png")
        
        await ctx.send(embed=embed, file=file) 