# test_transactions.py

from datetime import datetime
from main import (
    init_db,
    cache_current_month_transactions,
    fetch_transactions_for_arbitrary_month
)

# 1) Initialize the DB and cache current month
init_db()
cache_current_month_transactions()

# 2) Fetch May 2025 transactions
may2025 = fetch_transactions_for_arbitrary_month(2025, 5)
print(f"Fetched {len(may2025)} transactions for May 2025")

# 3) Print first few lines to confirm
for tx in may2025[:5]:
    date = tx["createdAt"]
    desc = tx.get("counterpartyName") or tx.get("bankDescription") or "–"
    amt = tx["amount"]
    print(date, desc, amt)
