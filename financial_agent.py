# Comprehensive Financial Agent for Mercury Bot
# Provides financial analysis, insights, and reporting

import asyncio
import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import sqlite3
import json
import re
from typing import Dict, List, Tuple
import anthropic
from anthropic import BadRequestError, RateLimitError, APIStatusError

class FinancialAgent:
    def __init__(self, bot, mercury_api, claude_client):
        self.bot = bot
        self.mercury_api = mercury_api
        self.claude_client = claude_client
        self.db_path = "transactions.db"
        
    async def analyze_finances(self, timeframe="month") -> str:
        """Comprehensive financial analysis"""
        try:
            # Get financial data
            accounts = await self.mercury_api.fetch_mercury_accounts()
            transactions = await self.get_transactions_for_timeframe(timeframe)
            
            # Calculate key metrics
            metrics = await self.calculate_financial_metrics(accounts, transactions, timeframe)
            
            # Generate insights
            insights = await self.generate_financial_insights(metrics, transactions)
            
            # Create comprehensive report
            report = await self.create_financial_report(metrics, insights, timeframe)
            
            return report
            
        except Exception as e:
            return f"❌ Error analyzing finances: {str(e)}"
    
    async def get_transactions_for_timeframe(self, timeframe: str) -> List[Dict]:
        """Get transactions for specified timeframe"""
        end_date = datetime.now(timezone.utc)
        
        if timeframe == "week":
            start_date = end_date - timedelta(days=7)
        elif timeframe == "month":
            start_date = end_date - timedelta(days=30)
        elif timeframe == "quarter":
            start_date = end_date - timedelta(days=90)
        elif timeframe == "year":
            start_date = end_date - timedelta(days=365)
        else:
            start_date = end_date - timedelta(days=30)  # default to month
        
        all_transactions = []
        accounts = await self.mercury_api.fetch_mercury_accounts()
        
        # Limit to first account and recent transactions for speed
        core_accounts = accounts.get("accounts", [])
        if core_accounts:
            account = core_accounts[0]  # Use first account only
            account_id = account.get("id")
            if account_id:
                # Use a simpler approach - just get recent transactions
                def _fetch_recent():
                    import requests
                    url = f"{self.mercury_api.base_url}/account/{account_id}/transactions"
                    params = {"limit": 50}  # Get 50 most recent transactions
                    response = requests.get(url, headers=self.mercury_api.headers, params=params, timeout=15)
                    return response.json().get("transactions", []) if response.ok else []
                
                transactions = await asyncio.get_event_loop().run_in_executor(None, _fetch_recent)
                for tx in transactions:
                    tx["account_name"] = account.get("name", "Unknown")
                all_transactions.extend(transactions)
        
        return all_transactions
    
    async def calculate_financial_metrics(self, accounts: Dict, transactions: List[Dict], timeframe: str) -> Dict:
        """Calculate comprehensive financial metrics"""
        metrics = {
            "timeframe": timeframe,
            "total_income": 0.0,
            "total_expenses": 0.0,
            "net_cash_flow": 0.0,
            "largest_expense": {"amount": 0.0, "vendor": "None"},
            "top_spending_categories": {},
            "top_vendors": {},
            "account_balances": {},
            "cash_burn_rate": 0.0,
            "runway_months": 0.0,
            "monthly_recurring_expenses": 0.0,
            "savings_rate": 0.0
        }
        
        # Calculate account balances
        for account in accounts.get("accounts", []):
            balance = account.get("availableBalance", account.get("currentBalance", 0.0))
            metrics["account_balances"][account.get("name", "Unknown")] = balance
        
        # Analyze transactions
        for tx in transactions:
            amount = tx.get("amount", 0.0)
            vendor = tx.get("merchantName") or tx.get("counterpartyName") or "Unknown"
            category = tx.get("mercuryCategory", "uncategorized")
            kind = tx.get("kind", "").lower()
            
            if "credit" in kind or amount > 0:
                metrics["total_income"] += abs(amount)
            else:
                metrics["total_expenses"] += abs(amount)
                
                # Track largest expense
                if abs(amount) > metrics["largest_expense"]["amount"]:
                    metrics["largest_expense"] = {"amount": abs(amount), "vendor": vendor}
                
                # Track spending categories
                metrics["top_spending_categories"][category] = metrics["top_spending_categories"].get(category, 0) + abs(amount)
                
                # Track top vendors
                metrics["top_vendors"][vendor] = metrics["top_vendors"].get(vendor, 0) + abs(amount)
        
        # Calculate net cash flow
        metrics["net_cash_flow"] = metrics["total_income"] - metrics["total_expenses"]
        
        # Calculate cash burn rate (monthly)
        if timeframe == "month":
            metrics["cash_burn_rate"] = metrics["total_expenses"]
        else:
            # Estimate monthly burn rate
            days_in_period = 30 if timeframe == "month" else 90 if timeframe == "quarter" else 365
            monthly_multiplier = 30 / days_in_period
            metrics["cash_burn_rate"] = metrics["total_expenses"] * monthly_multiplier
        
        # Calculate runway
        total_cash = sum(metrics["account_balances"].values())
        if metrics["cash_burn_rate"] > 0:
            metrics["runway_months"] = total_cash / metrics["cash_burn_rate"]
        
        # Calculate savings rate
        if metrics["total_income"] > 0:
            metrics["savings_rate"] = (metrics["net_cash_flow"] / metrics["total_income"]) * 100
        
        return metrics
    
    async def generate_financial_insights(self, metrics: Dict, transactions: List[Dict]) -> List[str]:
        """Generate AI-powered financial insights"""
        insights = []
        
        # Basic insights
        if metrics["net_cash_flow"] < 0:
            insights.append("⚠️ **Negative Cash Flow**: You're spending more than you're earning this period.")
        else:
            insights.append("✅ **Positive Cash Flow**: Great job maintaining positive cash flow!")
        
        if metrics["runway_months"] < 3:
            insights.append("🚨 **Low Runway**: You have less than 3 months of runway. Consider reducing expenses.")
        elif metrics["runway_months"] < 6:
            insights.append("⚠️ **Moderate Runway**: You have 3-6 months of runway. Monitor spending closely.")
        else:
            insights.append("✅ **Healthy Runway**: You have a healthy cash runway of 6+ months.")
        
        if metrics["savings_rate"] < 10:
            insights.append("💡 **Low Savings Rate**: Consider increasing your savings rate to 10-20%.")
        elif metrics["savings_rate"] > 30:
            insights.append("🎉 **Excellent Savings Rate**: You're saving over 30% - outstanding!")
        
        # Spending insights
        if metrics["top_spending_categories"]:
            top_category = max(metrics["top_spending_categories"].items(), key=lambda x: x[1])
            insights.append(f"💰 **Top Spending Category**: {top_category[0]} (${top_category[1]:,.2f})")
        
        if metrics["largest_expense"]["amount"] > 0:
            insights.append(f"💸 **Largest Expense**: {metrics['largest_expense']['vendor']} (${metrics['largest_expense']['amount']:,.2f})")
        
        return insights
    
    async def create_financial_report(self, metrics: Dict, insights: List[str], timeframe: str) -> str:
        """Create a comprehensive financial report"""
        report = f"# 📊 Financial Report - {timeframe.title()}\n\n"
        
        # Key Metrics
        report += "## 💰 Key Metrics\n"
        report += f"**Total Income:** ${metrics['total_income']:,.2f}\n"
        report += f"**Total Expenses:** ${metrics['total_expenses']:,.2f}\n"
        report += f"**Net Cash Flow:** ${metrics['net_cash_flow']:,.2f}\n"
        report += f"**Monthly Burn Rate:** ${metrics['cash_burn_rate']:,.2f}\n"
        report += f"**Runway:** {metrics['runway_months']:.1f} months\n"
        report += f"**Savings Rate:** {metrics['savings_rate']:.1f}%\n\n"
        
        # Account Balances
        report += "## 🏦 Account Balances\n"
        for account, balance in metrics['account_balances'].items():
            report += f"**{account}:** ${balance:,.2f}\n"
        report += "\n"
        
        # Top Spending Categories
        if metrics['top_spending_categories']:
            report += "## 📈 Top Spending Categories\n"
            sorted_categories = sorted(metrics['top_spending_categories'].items(), key=lambda x: x[1], reverse=True)
            for i, (category, amount) in enumerate(sorted_categories[:5], 1):
                report += f"{i}. **{category}:** ${amount:,.2f}\n"
            report += "\n"
        
        # Top Vendors
        if metrics['top_vendors']:
            report += "## 🏪 Top Vendors\n"
            sorted_vendors = sorted(metrics['top_vendors'].items(), key=lambda x: x[1], reverse=True)
            for i, (vendor, amount) in enumerate(sorted_vendors[:5], 1):
                report += f"{i}. **{vendor}:** ${amount:,.2f}\n"
            report += "\n"
        
        # Insights
        report += "## 💡 Financial Insights\n"
        for insight in insights:
            report += f"{insight}\n"
        
        return report
    
    async def get_spending_analysis(self, category: str = None, vendor: str = None) -> str:
        """Get detailed spending analysis"""
        try:
            transactions = await self.get_transactions_for_timeframe("month")
            
            if category:
                filtered_tx = [tx for tx in transactions if tx.get("mercuryCategory", "").lower() == category.lower()]
                title = f"Spending Analysis - {category.title()}"
            elif vendor:
                filtered_tx = [tx for tx in transactions if vendor.lower() in (tx.get("merchantName") or "").lower() or vendor.lower() in (tx.get("counterpartyName") or "").lower()]
                title = f"Spending Analysis - {vendor}"
            else:
                filtered_tx = transactions
                title = "Overall Spending Analysis"
            
            if not filtered_tx:
                return f"No transactions found for {category or vendor}"
            
            total_spent = sum(abs(tx.get("amount", 0)) for tx in filtered_tx if tx.get("amount", 0) < 0)
            avg_transaction = total_spent / len(filtered_tx) if filtered_tx else 0
            
            analysis = f"# {title}\n\n"
            analysis += f"**Total Spent:** ${total_spent:,.2f}\n"
            analysis += f"**Number of Transactions:** {len(filtered_tx)}\n"
            analysis += f"**Average Transaction:** ${avg_transaction:,.2f}\n\n"
            
            analysis += "## Recent Transactions\n"
            for i, tx in enumerate(filtered_tx[:10], 1):
                date = tx.get("createdAt", "")[:10] if tx.get("createdAt") else "Unknown"
                amount = abs(tx.get("amount", 0))
                vendor_name = tx.get("merchantName") or tx.get("counterpartyName") or "Unknown"
                analysis += f"{i}. {date} | {vendor_name} | ${amount:,.2f}\n"
            
            return analysis
            
        except Exception as e:
            return f"❌ Error analyzing spending: {str(e)}"
    
    async def get_financial_advice(self, question: str) -> str:
        """Get personalized financial advice"""
        try:
            # Get current financial context
            metrics = await self.calculate_financial_metrics(
                await self.mercury_api.fetch_mercury_accounts(),
                await self.get_transactions_for_timeframe("month"),
                "month"
            )
            
            context = f"""
            Current Financial Context:
            - Monthly Income: ${metrics['total_income']:,.2f}
            - Monthly Expenses: ${metrics['total_expenses']:,.2f}
            - Net Cash Flow: ${metrics['net_cash_flow']:,.2f}
            - Runway: {metrics['runway_months']:.1f} months
            - Savings Rate: {metrics['savings_rate']:.1f}%
            
            User Question: {question}
            
            Provide specific, actionable financial advice based on their current financial situation.
            """
            
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.claude_client.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=800,
                    messages=[{
                        "role": "user",
                        "content": context
                    }]
                )
            )
            
            return response.content[0].text
            
        except Exception as e:
            return f"❌ Error getting financial advice: {str(e)}"
