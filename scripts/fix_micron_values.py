#!/usr/bin/env python3
"""
Fix incorrect micron values for rolls.
Maps: 8 -> 40/80, 6 -> 30/60, 12 -> 60/120
Only applies to rolls (not sheets).
"""
import argparse
import sys
import os
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from models import db, Item, Transaction, AuditLog, Stock
from sqlalchemy import func

MICRON_MAPPING = {
    '1': '5/10',
    '2': '10/20',
    '3': '15/30',
    '4': '20/40',
    '5': '25/50',
    '6': '30/60',
    '7': '35/70',
    '8': '40/80',
    '9': '45/90',
    '10': '50/100',
    '11': '55/110',
    '12': '60/120',
    '13': '65/130',
    '14': '70/140',
    '15': '75/150',
    '16': '80/160',
    '17': '85/170',
    '18': '90/180',
    '19': '95/190',
    '20': '100/200'
}

# Only roll item types
ROLL_TYPES = ['roll']

def create_app(db_path):
    """Create Flask app with given database path."""
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    return app


def find_items_to_fix():
    """Find all roll items with incorrect micron values."""
    items = Item.query.filter(
        Item.item_type.in_(ROLL_TYPES),
        Item.micron_label.in_(MICRON_MAPPING.keys())
    ).all()
    return items


def find_matching_transactions(item, old_micron):
    """Find matching IN transactions for an item (matching by old micron value)."""
    return Transaction.query.filter(
        Transaction.transaction_type == 'IN',
        Transaction.item_type == item.item_type,
        Transaction.material == item.material,
        Transaction.width_inches == item.width_inches,
        Transaction.length_inches == item.length_inches,
        Transaction.micron_label == old_micron,
        Transaction.is_printed == item.is_printed,
        Transaction.buyer_name == item.buyer_name,
    ).all()


def print_dry_run_table(items_to_fix):
    """Print dry-run table showing what will be changed."""
    print(f"\n{'ITEM_ID':<8} {'DISPLAY_NAME':<45} {'OLD':<8} {'NEW':<8} {'STOCK':<6} {'PCS':<6} {'TXNS':<6}")
    print("-" * 95)
    
    total_items = 0
    total_stock = 0
    total_pcs = 0
    total_txns = 0
    
    for item in items_to_fix:
        new_micron = MICRON_MAPPING[item.micron_label]
        
        # Count stock records
        stock_recs = db.session.query(func.count(Stock.id)).filter(Stock.item_id == item.id).scalar() or 0
        # Total pieces in stock
        stock_pcs = db.session.query(func.sum(Stock.quantity_pieces)).filter(Stock.item_id == item.id).scalar() or 0
        # Matching transactions
        txns = find_matching_transactions(item, item.micron_label)
        txn_count = len(txns)
        
        print(f"{item.id:<8} {item.display_name:<45} {item.micron_label:<8} {new_micron:<8} {stock_recs:<6} {int(stock_pcs):<6} {txn_count:<6}")
        
        total_items += 1
        total_stock += stock_recs
        total_pcs += stock_pcs
        total_txns += txn_count
    
    print("-" * 95)
    print(f"TOTAL: {total_items} items, {total_stock} stock records, {int(total_pcs)} pcs, {total_txns} transactions")


def fix_micron_values(dry_run=False, verbose=False):
    """Main fix logic."""
    items_to_fix = find_items_to_fix()
    
    if not items_to_fix:
        print("No items with incorrect micron values found.")
        return True
    
    print(f"Found {len(items_to_fix)} roll item(s) with incorrect micron values.")
    
    if dry_run:
        print_dry_run_table(items_to_fix)
        print("\n[DRY RUN] No changes made. Run with --apply to execute.")
        return True
    
    total_items = 0
    total_txns_updated = 0
    
    try:
        for item in items_to_fix:
            old_micron = item.micron_label
            new_micron = MICRON_MAPPING[old_micron]
            
            if verbose:
                print(f"\nFixing Item #{item.id}: {item.display_name}")
                print(f"  {old_micron} -> {new_micron}")
            
            # Find matching transactions
            txns = find_matching_transactions(item, old_micron)
            txn_count = len(txns)
            
            # Update item
            item.micron_label = new_micron
            
            # Update transactions
            for txn in txns:
                txn.micron_label = new_micron
            
            # Audit log
            audit = AuditLog(
                table_name='item',
                record_id=item.id,
                action='UPDATE',
                old_values=str({'micron_label': old_micron}),
                new_values=str({'micron_label': new_micron, 'transactions_updated': txn_count}),
                changed_by=1  # System user
            )
            db.session.add(audit)
            
            total_items += 1
            total_txns_updated += txn_count
            
            if verbose:
                print(f"  Updated {txn_count} transaction(s)")
        
        db.session.commit()
        print(f"\nSuccess! Fixed {total_items} item(s), updated {total_txns_updated} transaction(s).")
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_fix():
    """Verify all incorrect micron values have been fixed."""
    print("\nVerifying fix...")
    
    # Check for remaining items with old micron values (rolls only)
    remaining = Item.query.filter(
        Item.item_type.in_(ROLL_TYPES),
        Item.micron_label.in_(MICRON_MAPPING.keys())
    ).all()
    
    if remaining:
        print(f"WARNING: {len(remaining)} roll item(s) still have old micron values:")
        for item in remaining:
            print(f"  Item #{item.id}: {item.display_name} - micron='{item.micron_label}'")
        return False
    else:
        print("OK: No roll items with old micron values (8, 6, 12) remain.")
    
    # Show summary of corrected items
    corrected = Item.query.filter(
        Item.item_type.in_(ROLL_TYPES),
        Item.micron_label.in_(MICRON_MAPPING.values())
    ).all()
    
    print(f"\nItems with corrected micron values: {len(corrected)}")
    for item in corrected:
        print(f"  Item #{item.id}: {item.display_name} - micron='{item.micron_label}'")
    
    # Check audit logs
    audit_count = AuditLog.query.filter(
        AuditLog.table_name == 'item',
        AuditLog.action == 'UPDATE'
    ).filter(
        AuditLog.new_values.like('%micron_label%')
    ).count()
    
    print(f"\nAudit log entries for micron updates: {audit_count}")
    
    return len(remaining) == 0


def main():
    parser = argparse.ArgumentParser(description='Fix incorrect micron values for rolls')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without applying')
    parser.add_argument('--apply', action='store_true', help='Apply fixes')
    parser.add_argument('--verify', action='store_true', help='Verify fix was applied')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    parser.add_argument('--db-path', type=str, help='Path to SQLite database file')
    
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
            success = verify_fix()
            return 0 if success else 1
        
        if args.dry_run:
            items_to_fix = find_items_to_fix()
            if items_to_fix:
                print_dry_run_table(items_to_fix)
            else:
                print("No items with incorrect micron values found.")
            return 0
        
        if args.apply:
            success = fix_micron_values(dry_run=False, verbose=args.verbose)
            return 0 if success else 1
    
    return 1


if __name__ == '__main__':
    sys.exit(main())