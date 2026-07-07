#!/usr/bin/env python3
"""
Separate merged stock records using Transaction history.
Run inside Flask app context.
"""
import argparse
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from models import db, Stock, Transaction, Item
from sqlalchemy import func

# Use instance folder for database
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'instance', 'factory_stock.db')

# Create minimal Flask app with correct database URI
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)


def find_merged_groups():
    """Find merged groups by grouping Transaction fields directly (no Item join)."""
    # Group by all Transaction fields that define an item + zone + date
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

    # For each group, find the Item and matching Stock record
    results = []
    for g in groups:
        item = Item.query.filter_by(
            item_type=g.item_type,
            material=g.material,
            width_inches=g.width_inches,
            length_inches=g.length_inches,
            micron_label=g.micron_label,
            is_printed=g.is_printed,
            buyer_name=g.buyer_name
        ).first()

        if item:
            stock = Stock.query.filter(
                Stock.item_id == item.id,
                Stock.zone_code == g.zone_code,
                Stock.date_received == g.executed_date
            ).first()

            if stock:
                # Convert executed_date string to Python date object
                if isinstance(g.executed_date, str):
                    from datetime import datetime
                    date_received = datetime.strptime(g.executed_date, '%Y-%m-%d').date()
                else:
                    date_received = g.executed_date
                    
                results.append({
                    'item_id': item.id,
                    'item_type': g.item_type,
                    'material': g.material,
                    'width_inches': g.width_inches,
                    'length_inches': g.length_inches,
                    'micron_label': g.micron_label,
                    'is_printed': g.is_printed,
                    'buyer_name': g.buyer_name,
                    'zone_code': g.zone_code,
                    'date_received': date_received,
                    'txn_count': g.txn_count,
                    'total_pieces': g.total_pieces,
                    'total_kg': g.total_kg,
                    'stock_id': stock.id
                })

    return results


def get_transactions_for_group(item_type, material, width_inches, length_inches, micron_label, is_printed, buyer_name, zone_code, executed_date):
    """Get all IN transactions for a group ordered by executed_at."""
    return Transaction.query.filter(
        Transaction.transaction_type == 'IN',
        Transaction.item_type == item_type,
        Transaction.material == material,
        Transaction.width_inches == width_inches,
        Transaction.length_inches == length_inches,
        Transaction.micron_label == micron_label,
        Transaction.is_printed == is_printed,
        Transaction.buyer_name == buyer_name,
        Transaction.zone_code == zone_code,
        func.date(Transaction.executed_at) == executed_date
    ).order_by(Transaction.executed_at).all()


def get_matching_stock(item_id, zone_code, date_received):
    """Find the merged stock record for this group."""
    return Stock.query.filter(
        Stock.item_id == item_id,
        Stock.zone_code == zone_code,
        Stock.date_received == date_received
    ).first()


def print_dry_run_table(groups):
    """Print dry-run table showing what will be split."""
    print(f"\n{'OLD_STOCK_ID':<12} {'ITEM_ID':<8} {'ZONE':<8} {'DATE':<12} {'TXNS':<5} {'PCS':<6} {'KG':<8} {'NEW_STOCKS'}")
    print("-" * 100)
    for g in groups:
        txns = get_transactions_for_group(
            g['item_type'], g['material'], g['width_inches'], g['length_inches'],
            g['micron_label'], g['is_printed'], g['buyer_name'],
            g['zone_code'], g['date_received']
        )
        old_id = g['stock_id']
        new_stocks = ', '.join([f"#{t.id}({t.quantity_pieces:.0f}pc,{t.quantity_kg:.2f}kg)" for t in txns])
        print(f"{old_id:<12} {g['item_id']:<8} {g['zone_code']:<8} {g['date_received']:<12} {g['txn_count']:<5} {g['total_pieces']:<6.0f} {g['total_kg']:<8.2f} {new_stocks}")


def separate_merged_stock(dry_run=False, verbose=False):
    """Main separation logic."""
    groups = find_merged_groups()
    
    if not groups:
        print("No merged stock records found. All stock already separated.")
        return True

    print(f"Found {len(groups)} merged stock group(s) to separate.")
    
    if dry_run:
        print_dry_run_table(groups)
        print("\n[DRY RUN] No changes made. Run with --apply to execute.")
        return True

    total_separated = 0
    total_new_records = 0
    
    try:
        for g in groups:
            txns = get_transactions_for_group(
                g['item_type'], g['material'], g['width_inches'], g['length_inches'],
                g['micron_label'], g['is_printed'], g['buyer_name'],
                g['zone_code'], g['date_received']
            )
            stock = get_matching_stock(g['item_id'], g['zone_code'], g['date_received'])
            
            if not stock:
                if verbose:
                    print(f"  No matching stock for item_id={g['item_id']}, zone={g['zone_code']}, date={g['date_received']} - skipping")
                continue
            
            old_stock_id = stock.id
            old_qty_pieces = stock.quantity_pieces
            old_qty_kg = stock.quantity_kg
            
            if verbose:
                print(f"\nSeparating stock #{old_stock_id} (item={g['item_id']}, zone={g['zone_code']}, date={g['date_received']})")
                print(f"  Original: {old_qty_pieces}pcs, {old_qty_kg:.2f}kg")
                print(f"  Transactions: {len(txns)}")
            
            # Create new individual stock records from transactions
            new_stock_ids = []
            for t in txns:
                new_stock = Stock(
                    item_id=g['item_id'],
                    zone_code=g['zone_code'],
                    quantity_pieces=t.quantity_pieces,
                    quantity_kg=t.quantity_kg,
                    date_received=g['date_received']
                )
                db.session.add(new_stock)
                db.session.flush()  # Get new ID
                new_stock_ids.append(new_stock.id)
                
                # Update transaction note to reference split
                orig_notes = t.notes or ""
                t.notes = f"Split from stock #{old_stock_id} -> stock #{new_stock.id} | {orig_notes}"
                
                if verbose:
                    print(f"    Created stock #{new_stock.id}: {t.quantity_pieces}pc, {t.quantity_kg:.2f}kg (txn #{t.id})")
            
            # Delete old merged stock
            db.session.delete(stock)
            
            # Audit log (direct, without current_app.log_audit)
            from models import AuditLog
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
        return False


def verify_separation():
    """Verify Stock count == IN Transaction count per item/zone/date."""
    print("\nVerifying separation...")
    
    # Check stock groups (by Stock.item_id, zone_code, date_received)
    stock_groups = db.session.query(
        Stock.item_id,
        Stock.zone_code,
        Stock.date_received,
        func.count(Stock.id).label('stock_count'),
        func.sum(Stock.quantity_pieces).label('total_pieces'),
    ).group_by(
        Stock.item_id,
        Stock.zone_code,
        Stock.date_received
    ).having(
        func.count(Stock.id) > 1
    ).all()
    
    # Check transaction groups (by Transaction fields, matching find_merged_groups logic)
    txn_groups = db.session.query(
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
    
    issues = 0
    
    if stock_groups:
        print(f"  WARNING: {len(stock_groups)} stock group(s) still have multiple records:")
        for g in stock_groups:
            print(f"    item={g.item_id}, zone={g.zone_code}, date={g.date_received}: {g.stock_count} records, {g.total_pieces}pcs")
        issues += len(stock_groups)
    else:
        print("  OK: No stock groups with multiple records.")
    
    if txn_groups:
        print(f"  INFO: {len(txn_groups)} transaction group(s) with multiple IN transactions:")
        for g in txn_groups:
            print(f"    {g.item_type}|{g.material}|{g.width_inches}|{g.length_inches}|{g.micron_label}|{g.is_printed}|{g.buyer_name}|{g.zone_code}|{g.executed_date}: {g.txn_count} txns, {g.total_pieces}pcs")
    else:
        print("  OK: No transaction groups with multiple IN transactions.")
    
    # Verify counts match: compare Stock (by item_id) with Transaction (by specs -> item_id)
    stock_by_group = db.session.query(
        Stock.item_id,
        Stock.zone_code,
        Stock.date_received,
        func.count(Stock.id).label('stock_count'),
        func.sum(Stock.quantity_pieces).label('stock_pieces'),
        func.sum(Stock.quantity_kg).label('stock_kg'),
    ).group_by(
        Stock.item_id,
        Stock.zone_code,
        Stock.date_received
    ).all()
    
    # Get Transaction groups, then join to Item to get item_id
    txn_by_group_raw = db.session.query(
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
        func.sum(Transaction.quantity_pieces).label('txn_pieces'),
        func.sum(Transaction.quantity_kg).label('txn_kg'),
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
    ).all()
    
    # Map transaction groups to item_ids
    txn_by_group = {}
    for t in txn_by_group_raw:
        item = Item.query.filter_by(
            item_type=t.item_type,
            material=t.material,
            width_inches=t.width_inches,
            length_inches=t.length_inches,
            micron_label=t.micron_label,
            is_printed=t.is_printed,
            buyer_name=t.buyer_name
        ).first()
        if item:
            key = (item.id, t.zone_code, t.executed_date)
            txn_by_group[key] = type('obj', (object,), {
                'item_id': item.id,
                'zone_code': t.zone_code,
                'executed_date': t.executed_date,
                'txn_count': t.txn_count,
                'txn_pieces': t.txn_pieces,
                'txn_kg': t.txn_kg
            })()
    
    stock_dict = {(s.item_id, s.zone_code, s.date_received): s for s in stock_by_group}
    txn_dict = {(t.item_id, t.zone_code, t.executed_date): t for t in txn_by_group.values()}
    
    all_keys = set(stock_dict.keys()) | set(txn_dict.keys())
    mismatch = 0
    
    for key in all_keys:
        s = stock_dict.get(key)
        t = txn_dict.get(key)
        
        if s and t:
            if s.stock_count != t.txn_count:
                print(f"  MISMATCH {key}: stock_count={s.stock_count} vs txn_count={t.txn_count}")
                mismatch += 1
            if abs(s.stock_pieces - t.txn_pieces) > 0.01:
                print(f"  MISMATCH {key}: stock_pieces={s.stock_pieces} vs txn_pieces={t.txn_pieces}")
                mismatch += 1
            if abs(s.stock_kg - t.txn_kg) > 0.01:
                print(f"  MISMATCH {key}: stock_kg={s.stock_kg:.2f} vs txn_kg={t.txn_kg:.2f}")
                mismatch += 1
        elif s and not t:
            print(f"  ORPHAN STOCK {key}: {s.stock_count} records but no IN transactions")
            mismatch += 1
        elif t and not s:
            print(f"  MISSING STOCK {key}: {t.txn_count} IN transactions but no stock records")
            mismatch += 1
    
    if mismatch == 0:
        print("\n  VERIFICATION PASSED: All stock counts match transaction counts.")
    else:
        print(f"\n  VERIFICATION FAILED: {mismatch} mismatch(es) found.")
    
    return mismatch == 0


def main():
    parser = argparse.ArgumentParser(description='Separate merged stock records using transaction-based IN records')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without applying')
    parser.add_argument('--apply', action='store_true', help='Apply separation')
    parser.add_argument('--verify', action='store_true', help='Verify separation results')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    if not (args.dry_run or args.apply or args.verify):
        parser.print_help()
        return 1
    
    with app.app_context():
        if args.verify:
            success = verify_separation()
            return 0 if success else 1
        
        if args.dry_run:
            groups = find_merged_groups()
            if groups:
                print_dry_run_table(groups)
            else:
                print("No merged stock records found.")
            return 0
        
        if args.apply:
            success = separate_merged_stock(dry_run=False, verbose=args.verbose)
            return 0 if success else 1
    
    return 1


if __name__ == '__main__':
    sys.exit(main())