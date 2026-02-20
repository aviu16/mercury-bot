# Enhanced Mercury API Integration - ALL ENDPOINTS
# Complete Mercury API integration for comprehensive financial analysis

import os
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

class EnhancedMercuryAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.mercury.com/api/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Core Account Information (Enhanced)
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def fetch_mercury_accounts(self):
        """Fetch core, credit, and treasury accounts with enhanced details."""
        def _fetch():
            data = {}
            # Core accounts
            resp = requests.get(f"{self.base_url}/accounts", headers=self.headers, timeout=20)
            data["accounts"] = resp.json().get("accounts", []) if resp.ok else []
            
            # Credit accounts
            cr = requests.get(f"{self.base_url}/credit", headers=self.headers, timeout=20)
            data["credit_accounts"] = cr.json().get("accounts", []) if cr.ok else []
            
            # Treasury accounts
            tr = requests.get(f"{self.base_url}/treasury", headers=self.headers, timeout=20)
            data["treasury_accounts"] = tr.json().get("accounts", []) if cr.ok else []
            
            return data
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    async def get_account_details(self, account_id: str) -> Dict:
        """Get detailed account information including routing numbers, limits, etc."""
        def _fetch():
            url = f"{self.base_url}/account/{account_id}"
            response = requests.get(url, headers=self.headers, timeout=20)
            return response.json() if response.ok else {}
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    async def get_account_limits(self, account_id: str) -> Dict:
        """Get account limits and capabilities"""
        def _fetch():
            url = f"{self.base_url}/account/{account_id}/limits"
            response = requests.get(url, headers=self.headers, timeout=20)
            return response.json() if response.ok else {}
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Enhanced Transaction Analysis
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def fetch_all_tx_for_account(self, acct_id, after=None):
        """Yield all transactions for an account, paging until exhausted."""
        def _fetch_all(acc_id, aft):
            collected = []
            url = f"{self.base_url}/account/{acc_id}/transactions"
            cursor = None
            while True:
                params = {"limit": 100}
                if cursor:
                    params["before"] = cursor
                if aft:
                    params["from"] = aft
                r = requests.get(url, headers=self.headers, params=params, timeout=30)
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
    
    async def get_transactions_with_filters(self, account_id: str, 
                                          start_date: str = None,
                                          end_date: str = None,
                                          min_amount: float = None,
                                          max_amount: float = None,
                                          transaction_type: str = None,
                                          category: str = None) -> List[Dict]:
        """Get transactions with advanced filtering"""
        def _fetch():
            url = f"{self.base_url}/account/{account_id}/transactions"
            params = {}
            
            if start_date:
                params["from"] = start_date
            if end_date:
                params["to"] = end_date
            if min_amount:
                params["min_amount"] = min_amount
            if max_amount:
                params["max_amount"] = max_amount
            if transaction_type:
                params["type"] = transaction_type
            if category:
                params["category"] = category
                
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            return response.json().get("transactions", []) if response.ok else []
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Recipients & Payees Management
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def get_recipients(self) -> List[Dict]:
        """Get all saved recipients/payees"""
        def _fetch():
            url = f"{self.base_url}/recipients"
            response = requests.get(url, headers=self.headers, timeout=20)
            return response.json().get("recipients", []) if response.ok else []
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    async def get_recipient_transactions(self, recipient_id: str) -> List[Dict]:
        """Get all transactions with a specific recipient"""
        def _fetch():
            url = f"{self.base_url}/recipient/{recipient_id}/transactions"
            response = requests.get(url, headers=self.headers, timeout=20)
            return response.json().get("transactions", []) if response.ok else []
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  ACH Payments & Transfers
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def create_ach_payment(self, account_id: str, recipient_id: str, 
                               amount: float, description: str = "") -> Dict:
        """Create an ACH payment"""
        def _fetch():
            url = f"{self.base_url}/account/{account_id}/ach"
            data = {
                "recipient_id": recipient_id,
                "amount": amount,
                "description": description
            }
            response = requests.post(url, headers=self.headers, json=data, timeout=30)
            return response.json() if response.ok else {}
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    async def get_ach_payments(self, account_id: str) -> List[Dict]:
        """Get all ACH payments for an account"""
        def _fetch():
            url = f"{self.base_url}/account/{account_id}/ach"
            response = requests.get(url, headers=self.headers, timeout=20)
            return response.json().get("ach", []) if response.ok else []
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Wire Transfers
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def create_wire_transfer(self, account_id: str, wire_data: Dict) -> Dict:
        """Create a wire transfer"""
        def _fetch():
            url = f"{self.base_url}/account/{account_id}/wires"
            response = requests.post(url, headers=self.headers, json=wire_data, timeout=30)
            return response.json() if response.ok else {}
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    async def get_wire_transfers(self, account_id: str) -> List[Dict]:
        """Get all wire transfers for an account"""
        def _fetch():
            url = f"{self.base_url}/account/{account_id}/wires"
            response = requests.get(url, headers=self.headers, timeout=20)
            return response.json().get("wires", []) if response.ok else []
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Card Management
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def get_cards(self, account_id: str) -> List[Dict]:
        """Get all cards associated with an account"""
        def _fetch():
            url = f"{self.base_url}/account/{account_id}/cards"
            response = requests.get(url, headers=self.headers, timeout=20)
            return response.json().get("cards", []) if response.ok else []
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    async def get_card_transactions(self, card_id: str) -> List[Dict]:
        """Get all transactions for a specific card"""
        def _fetch():
            url = f"{self.base_url}/card/{card_id}/transactions"
            response = requests.get(url, headers=self.headers, timeout=20)
            return response.json().get("transactions", []) if response.ok else []
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Treasury Management
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def get_treasury_transactions(self, treasury_id: str) -> List[Dict]:
        """Get transactions for treasury accounts"""
        def _fetch():
            url = f"{self.base_url}/treasury/{treasury_id}/transactions"
            response = requests.get(url, headers=self.headers, timeout=20)
            return response.json().get("transactions", []) if response.ok else []
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    
    # ─────────────────────────────────────────────────────────────────────────────
    #  Advanced Analytics
    # ─────────────────────────────────────────────────────────────────────────────
    
    async def calculate_revenue(self, account_ids: List[str], 
                              start_date: str, end_date: str) -> Dict:
        """Calculate revenue from credits in specified period"""
        total_revenue = 0
        revenue_by_account = {}
        
        for account_id in account_ids:
            transactions = await self.get_transactions_with_filters(
                account_id, start_date, end_date, transaction_type="credit"
            )
            
            account_revenue = sum(tx.get("amount", 0) for tx in transactions)
            revenue_by_account[account_id] = account_revenue
            total_revenue += account_revenue
        
        return {
            "total_revenue": total_revenue,
            "revenue_by_account": revenue_by_account,
            "period": {"start": start_date, "end": end_date}
        }
    
    async def calculate_burn_rate(self, account_ids: List[str], 
                                start_date: str, end_date: str) -> Dict:
        """Calculate burn rate from debits in specified period"""
        total_burn = 0
        burn_by_account = {}
        burn_by_category = {}
        
        for account_id in account_ids:
            transactions = await self.get_transactions_with_filters(
                account_id, start_date, end_date, transaction_type="debit"
            )
            
            account_burn = sum(abs(tx.get("amount", 0)) for tx in transactions)
            burn_by_account[account_id] = account_burn
            total_burn += account_burn
            
            # Categorize burn
            for tx in transactions:
                category = tx.get("mercuryCategory", "uncategorized")
                amount = abs(tx.get("amount", 0))
                burn_by_category[category] = burn_by_category.get(category, 0) + amount
        
        return {
            "total_burn": total_burn,
            "burn_by_account": burn_by_account,
            "burn_by_category": burn_by_category,
            "period": {"start": start_date, "end": end_date}
        }
    
    async def get_comprehensive_financial_data(self) -> Dict:
        """Get comprehensive financial data from all endpoints"""
        try:
            # Get all accounts
            accounts_data = await self.fetch_mercury_accounts()
            
            # Get account details for each account
            account_details = {}
            account_limits = {}
            cards_data = {}
            ach_data = {}
            wire_data = {}
            
            all_accounts = (accounts_data.get("accounts", []) + 
                          accounts_data.get("credit_accounts", []) + 
                          accounts_data.get("treasury_accounts", []))
            
            for account in all_accounts:
                account_id = account.get("id")
                if account_id:
                    # Get detailed account info
                    details = await self.get_account_details(account_id)
                    account_details[account_id] = details
                    
                    # Get account limits
                    limits = await self.get_account_limits(account_id)
                    account_limits[account_id] = limits
                    
                    # Get cards
                    cards = await self.get_cards(account_id)
                    cards_data[account_id] = cards
                    
                    # Get ACH payments
                    ach_payments = await self.get_ach_payments(account_id)
                    ach_data[account_id] = ach_payments
                    
                    # Get wire transfers
                    wires = await self.get_wire_transfers(account_id)
                    wire_data[account_id] = wires
            
            # Get recipients
            recipients = await self.get_recipients()
            
            return {
                "accounts": accounts_data,
                "account_details": account_details,
                "account_limits": account_limits,
                "cards": cards_data,
                "ach_payments": ach_data,
                "wire_transfers": wire_data,
                "recipients": recipients,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            print(f"Error getting comprehensive data: {e}")
            return {} 