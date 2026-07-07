#!/usr/bin/env python3
"""
Debug script to understand IN transaction grouping on production.
Run this on production server to see what's happening.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from models import db, Stock, Transaction, Item
from sqlalchemy import func

# Use instance folder for database
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'instance', 'factory_stock.db')

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)


def debug_transactions():
    """Show all IN transactions and how they would group."""
    with app.app_context():
        print(f"Database: {DB_PATH}")
        print(f"Stock count: {Stock.query.count()}")
        print(f"Transaction count: {Transaction.query.count()}")
        print(f"Item count: {Item.query.count()}")
        
        # Show all IN transactions with their grouping keys
        print("\n=== ALL IN TRANSACTIONS ===")
        txns = Transaction.query.filter_by(transaction_type='IN').order_by(Transaction.executed_at).all()
        for t in txns:
            print(f"TXN #{t.id}: type={t.item_type} mat={t.material} w={t.width_inches} l={t.length_inches} "
                  f"mic={t.micron_label} printed={t.is_printed} buyer={t.buyer_name} "
                  f"zone={t.zone_code} pcs={t.quantity_pieces} kg={t.quantity_kg} "
                  f"date={func.date(t.executed_at)} time={t.executed_at}")
        
        # Try grouping by the key fields (without joining to Item)
        print("\n=== GROUPING BY TRANSACTION FIELDS (no Item join) ===")
        groups = db.session.query(
            Transaction.item_type,
            Transaction.material,
            Transaction.width_inches,
            Transaction.length_inches,
            Transaction.micron_label,
            Transaction.is_printed,
            Transaction.buyer_name,
            Transaction.zone_code,
            func.date(Transaction.executed_at).label('executed_date'),
            func.count(Transaction.id).label('txn_count'),
            func.sum(Transaction.quantity_pieces).label('total_pieces'),
            func.sum(Transaction.quantity_kg).label('total_kg'),
        ).filter(
            Transaction.transaction_type == 'IN'
        ).group_by(
            Transaction.item_type,
            Transaction.material,
            Transaction.width_inches,
            Transaction.length_inches,
            Transaction.micron_label,
            Transaction.is_printed,
            Transaction.buyer_name,
            Transaction.zone_code,
            func.date(Transaction.executed_at)
        ).having(
            func.count(Transaction.id) > 1
        ).all()
        
        if groups:
            print(f"Found {len(groups)} merged groups:")
            for g in groups:
                print(f"  {g.item_type}|{g.material}|{g.width_inches}|{g.length_inches}|{g.micron_label}|{g.is_printed}|{g.buyer_name}|{g.zone_code}|{g.executed_date}: {g.txn_count} txns, {g.total_pieces}pcs, {g.total_kg:.2f}kg")
        else:
            print("No merged groups found with this grouping.")
        
        # Show stock records
        print("\n=== STOCK RECORDS ===")
        stocks = Stock.query.all()
        for s in stocks:
            item = db.session.get(Item, s.item_id)
            item_str = f"{item.item_type}|{item.material}|{item.width_inches}|{item.length_inches}|{item.micron_label}|{item.is_printed}|{item.buyer_name}" if item else "NO ITEM"
            print(f"  Stock #{s.id}: item_id={s.item_id} ({item_str}) zone={s.zone_code} pcs={s.quantity_pieces} kg={s.quantity_kg:.2f} date={s.date_received}")
        
        # Check if stock item_ids match transaction groupings
        print("\n=== STOCK vs TRANSACTION GROUPING CHECK ===")
        for s in stocks:
            item = db.session.get(Item, s.item_id)
            if item:
                matching_txns = Transaction.query.filter(
                    Transaction.transaction_type == 'IN',
                    Transaction.item_type == item.item_type,
                    Transaction.material == item.material,
                    Transaction.width_inches == item.width_inches,
                    Transaction.length_inches == item.length_inches,
                    Transaction.micron_label == item.micron_label,
                    Transaction.is_printed == item.is_printed,
                    Transaction.zone_code == s.zone_code,
                    func.date(Transaction.executed_at) == s.date_received
                ).count()
                if matching_txns > 1:
                    print(f"  MERGED: Stock #{s.id} (item={item.id}) has {matching_txns} IN transactions for same zone/date")
                elif matching_txns == 1:
                    print(f"  OK: Stock #{s.id} has 1 IN transaction")
                else:
                    print(f"  ORPHAN: Stock #{s.id} has 0 matching IN transactions")


if __name__ == '__main__':
    debug_transactions()