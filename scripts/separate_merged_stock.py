#!/usr/bin/env python3
"""
Separate merged stock records using Transaction history.
Run inside Flask app context.

NEW LOGIC: Iterate each Stock record, find matching IN Transactions.
Only split if:
  1. Stock.quantity_pieces > 1
  2. Matching IN Transactions > 1
  3. Sum of Transaction pieces/kg matches Stock pieces/kg (within 0.01 tolerance)
"""
import argparse
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from models import db, Stock, Transaction, Item, AuditLog
from sqlalchemy import func
from datetime import datetime


def create_app(db_path):
    """Create Flask app with given database path."""
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    return app


def find_merged_stocks():
    """
    Find Stock records that need splitting.
    Returns list of (stock, matching_transactions) tuples.
    """
    # Get all stock records with quantity_pieces > 1
    stocks = Stock.query.filter(Stock.quantity_pieces > 1).all()
    
    results = []
    for stock in stocks:
        item = db.session.get(Item, stock.item_id)
        if not item:
            continue
        
        # Find matching IN transactions (same logic as debug script)
        matching_txns = Transaction.query.filter(
            Transaction.transaction_type == 'IN',
            Transaction.item_type == item.item_type,
            Transaction.material == item.material,
            Transaction.width_inches == item.width_inches,
            Transaction.length_inches == item.length_inches,
            Transaction.micron_label == item.micron_label,
            Transaction.is_printed == item.is_printed,
            Transaction.buyer_name == item.buyer_name,
            Transaction.zone_code == stock.zone_code,
            func.date(Transaction.executed_at) == stock.date_received
        ).order_by(Transaction.executed_at).all()
        
        if len(matching_txns) <= 1:
            continue  # Not merged
        
        # Verify totals match
        txn_pieces = sum(t.quantity_pieces for t in matching_txns)
        txn_kg = sum(t.quantity_kg for t in matching_txns)
        
        pieces_diff = abs(txn_pieces - stock.quantity_pieces)
        kg_diff = abs(txn_kg - stock.quantity_kg)
        
        if pieces_diff > 0.01 or kg_diff > 0.01:
            print(f"SKIP Stock #{stock.id} (item={stock.item_id}, zone={stock.zone_code}, date={stock.date_received}): "
                  f"totals don't match (stock: {stock.quantity_pieces}pc/{stock.quantity_kg:.2f}kg, "
                  f"txns: {txn_pieces}pc/{txn_kg:.2f}kg, diff: {pieces_diff:.2f}pc/{kg_diff:.2f}kg)")
            continue
        
        results.append((stock, matching_txns))
    
    return results


def print_dry_run_table(results):
    """Print dry-run table showing what will be split."""
    print(f"\n{'OLD_STOCK_ID':<12} {'ITEM_ID':<8} {'ZONE':<8} {'DATE':<12} {'TXNS':<5} {'PCS':<6} {'KG':<8} {'NEW_STOCKS'}")
    print("-" * 100)
    for stock, txns in results:
        old_id = stock.id
        new_stocks = ', '.join([f"#{t.id}({t.quantity_pieces:.0f}pc,{t.quantity_kg:.2f}kg)" for t in txns])
        print(f"{old_id:<12} {stock.item_id:<8} {stock.zone_code:<8} {stock.date_received:<12} "
              f"{len(txns):<5} {stock.quantity_pieces:<6.0f} {stock.quantity_kg:<8.2f} {new_stocks}")


def separate_merged_stock(dry_run=False, verbose=False):
    """Main separation logic."""
    results = find_merged_stocks()
    
    if not results:
        print("No merged stock records found. All stock already separated.")
        return True

    print(f"Found {len(results)} merged stock record(s) to separate.")
    
    if dry_run:
        print_dry_run_table(results)
        print("\n[DRY RUN] No changes made. Run with --apply to execute.")
        return True

    total_separated = 0
    total_new_records = 0
    
    try:
        for stock, txns in results:
            old_stock_id = stock.id
            old_qty_pieces = stock.quantity_pieces
            old_qty_kg = stock.quantity_kg
            
            if verbose:
                print(f"\nSeparating stock #{old_stock_id} (item={stock.item_id}, zone={stock.zone_code}, date={stock.date_received})")
                print(f"  Original: {old_qty_pieces}pcs, {old_qty_kg:.2f}kg")
                print(f"  Transactions: {len(txns)}")
            
            # Create new individual stock records from transactions
            new_stock_ids = []
            for t in txns:
                new_stock = Stock(
                    item_id=stock.item_id,
                    zone_code=stock.zone_code,
                    quantity_pieces=t.quantity_pieces,
                    quantity_kg=t.quantity_kg,
                    date_received=stock.date_received
                )
                db.session.add(new_stock)
                db.session.flush()  # Get new ID
                new_stock_ids.append(new_stock.id)
                
                # Update transaction note to reference split
                orig_notes = t.notes or ""
                t.notes = f"Split from stock #{old_stock_id} -> stock #{new_stock.id} | {orig_notes}"
                
                if verbose:
                    print(f"    Created stock #{new_stock.id}: {t.quantity_pieces}pc, {t.quantity_kg:.2f}kg (txn #{t.id})")
            
            # Delete THE specific old merged stock record
            db.session.delete(stock)
            
            # Audit log
            audit = AuditLog(
                table_name='stock',
                record_id=old_stock_id,
                action='DELETE',
                old_values=str({'quantity_pieces': old_qty_pieces, 'quantity_kg': old_qty_kg}),
                new_values=str({'split_into': len(new_stock_ids), 'new_stock_ids': new_stock_ids}),
                changed_by=1  # System user
            )
            db.session.add(audit)
            
            total_separated += 1
            total_new_records += len(new_stock_ids)
            
            if verbose:
                print(f"  Deleted stock #{old_stock_id}, created {len(new_stock_ids)} new records: {new_stock_ids}")
        
        db.session.commit()
        print(f"\nSuccess! Separated {total_separated} merged stock record(s) into {total_new_records} individual record(s).")
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_totals_preserved():
    """Verify total pieces and kg across all stock hasn't changed."""
    print("\nVerifying global totals preserved...")
    
    # Total pieces and kg in stock
    stock_total_pieces = db.session.query(func.sum(Stock.quantity_pieces)).scalar() or 0
    stock_total_kg = db.session.query(func.sum(Stock.quantity_kg)).scalar() or 0
    
    # Total pieces and kg in IN transactions
    txn_total_pieces = db.session.query(func.sum(Transaction.quantity_pieces)).filter(
        Transaction.transaction_type == 'IN'
    ).scalar() or 0
    txn_total_kg = db.session.query(func.sum(Transaction.quantity_kg)).filter(
        Transaction.transaction_type == 'IN'
    ).scalar() or 0
    
    pieces_diff = abs(stock_total_pieces - txn_total_pieces)
    kg_diff = abs(stock_total_kg - txn_total_kg)
    
    print(f"  Stock total: {stock_total_pieces:.2f}pcs, {stock_total_kg:.2f}kg")
    print(f"  IN Txns total: {txn_total_pieces:.2f}pcs, {txn_total_kg:.2f}kg")
    print(f"  Difference: {pieces_diff:.2f}pcs, {kg_diff:.2f}kg")
    
    if pieces_diff <= 0.01 and kg_diff <= 0.01:
        print("  OK: Global totals preserved!")
        return True
    else:
        print("  WARNING: Global totals changed!")
        return False


def verify_no_merged_stocks():
    """Verify no stock records have quantity_pieces > 1 with matching IN transactions > 1."""
    print("\nVerifying no merged stocks remain...")
    
    # Find stocks with qty > 1 that still have >1 matching IN transaction
    stocks = Stock.query.filter(Stock.quantity_pieces > 1).all()
    merged_remaining = 0
    
    for stock in stocks:
        item = db.session.get(Item, stock.item_id)
        if not item:
            continue
        
        matching_txns = Transaction.query.filter(
            Transaction.transaction_type == 'IN',
            Transaction.item_type == item.item_type,
            Transaction.material == item.material,
            Transaction.width_inches == item.width_inches,
            Transaction.length_inches == item.length_inches,
            Transaction.micron_label == item.micron_label,
            Transaction.is_printed == item.is_printed,
            Transaction.buyer_name == item.buyer_name,
            Transaction.zone_code == stock.zone_code,
            func.date(Transaction.executed_at) == stock.date_received
        ).count()
        
        if matching_txns > 1:
            print(f"  STILL MERGED: Stock #{stock.id} (item={stock.item_id}, zone={stock.zone_code}, date={stock.date_received}): {matching_txns} IN txns, {stock.quantity_pieces}pcs")
            merged_remaining += 1
    
    if merged_remaining == 0:
        print("  OK: No merged stocks remain!")
        return True
    else:
        print(f"  WARNING: {merged_remaining} merged stock(s) remain!")
        return False


def main():
    parser = argparse.ArgumentParser(description='Separate merged stock records using transaction-based IN records')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without applying')
    parser.add_argument('--apply', action='store_true', help='Apply separation')
    parser.add_argument('--verify', action='store_true', help='Verify separation results (totals + no merged)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    parser.add_argument('--db-path', type=str, help='Path to SQLite database file (default: instance/factory_stock.db relative to script)')
    
    args = parser.parse_args()
    
    if not (args.dry_run or args.apply or args.verify):
        parser.print_help()
        return 1
    
    # Determine database path
    if args.db_path:
        db_path = args.db_path
    else:
        BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(BASE_DIR, 'instance', 'factory_stock.db')
    
    print(f"Using database: {db_path}")
    
    # Create app with the specified database
    app = create_app(db_path)
    
    with app.app_context():
        if args.verify:
            ok1 = verify_totals_preserved()
            ok2 = verify_no_merged_stocks()
            return 0 if (ok1 and ok2) else 1
        
        if args.dry_run:
            results = find_merged_stocks()
            if results:
                print_dry_run_table(results)
            else:
                print("No merged stock records found.")
            return 0
        
        if args.apply:
            success = separate_merged_stock(dry_run=False, verbose=args.verbose)
            return 0 if success else 1
    
    return 1


if __name__ == '__main__':
    sys.exit(main())