from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from flask import current_app
from models import db, Item, Stock, Transaction, Zone, User
from datetime import datetime
from sqlalchemy import or_, func
import re

stock_bp = Blueprint('stock', __name__)

def sanitize_input(text, max_length=200):
    """Sanitize user input to prevent XSS"""
    if not text:
        return ''
    # Remove potentially dangerous characters
    text = re.sub(r'[<>\"\'&]', '', text)
    return text[:max_length]

def validate_float(value, min_val=0, max_val=1000000):
    """Validate and convert to float"""
    try:
        val = float(value)
        if val < min_val or val > max_val:
            return None
        return val
    except (ValueError, TypeError):
        return None

def validate_int(value, min_val=0, max_val=1000000):
    """Validate and convert to int"""
    try:
        val = int(value)
        if val < min_val or val > max_val:
            return None
        return val
    except (ValueError, TypeError):
        return None

@stock_bp.route('/')
@login_required
def stock_directory():
    # Get filter parameters
    search = request.args.get('search', '')
    material = request.args.get('material', 'all')
    item_type = request.args.get('type', 'all')
    
    # Build query
    query = Stock.query.join(Item).join(Zone)
    
    # Exclude items with 0 quantity
    query = query.filter(Stock.quantity_pieces > 0)
    
    # --- UPDATED SEARCH LOGIC ---
    if search:
        search_term = search.lower()
        query = query.filter(
            or_(
                func.lower(Item.buyer_name).like(f'%{search_term}%'),
                func.lower(Item.print_details).like(f'%{search_term}%'),
                func.lower(Item.material).like(f'%{search_term}%'),
                func.lower(Item.micron_label).like(f'%{search_term}%'),
                func.lower(Zone.code).like(f'%{search_term}%'),
                # Search by width and length (cast to string for decimal search)
                db.cast(Item.width_inches, db.String).like(f'%{search_term}%'),
                db.cast(Item.length_inches, db.String).like(f'%{search_term}%'),
                # NEW: Cast float columns to String so we can search decimals like "50.5"
                db.cast(Stock.quantity_pieces, db.String).like(f'%{search_term}%'),
                db.cast(Stock.quantity_kg, db.String).like(f'%{search_term}%')
            )
        )
    # ----------------------------
    
    if material != 'all':
        query = query.filter(Item.material == material)
    
    if item_type != 'all':
        query = query.filter(Item.item_type == item_type)
    
    # Order by newest first
    stocks = query.order_by(Stock.date_received.desc()).all()
    
    # Get unique materials and types for filters
    materials = db.session.query(Item.material).distinct().all()
    types = db.session.query(Item.item_type).distinct().all()
    
    # Calculate total stock weight (across all stock, regardless of filters)
    total_weight_kg = db.session.query(func.sum(Stock.quantity_kg)).filter(Stock.quantity_pieces > 0).scalar() or 0
    
    return render_template('dashboard.html', 
                         stocks=stocks, 
                         materials=[m[0] for m in materials],
                         types=[t[0] for t in types],
                         selected_material=material,
                         selected_type=item_type,
                         search_term=search,
                         total_weight_kg=total_weight_kg)

@stock_bp.route('/in', methods=['GET', 'POST'])
@login_required
def stock_in():
    if current_user.role != 'store_keeper':
        flash('Unauthorized access', 'error')
        return redirect(url_for('stock.stock_directory'))
    
    if request.method == 'POST':
        # Get and validate form data
        material = sanitize_input(request.form.get('material', ''), 20)
        item_type = sanitize_input(request.form.get('type', ''), 20)
        width = validate_float(request.form.get('width', 0), 0, 10000)
        length = validate_float(request.form.get('length', 0), 0, 10000)
        micron_label = sanitize_input(request.form.get('micron', ''), 20)
        weight = validate_float(request.form.get('weight'), 0.01, 100000)
        quantity = validate_float(request.form.get('quantity'), 0.01, 100000)
        is_printed = request.form.get('printed') == 'on'
        print_details = sanitize_input(request.form.get('print_details', ''), 200) if is_printed else None
        buyer_name = sanitize_input(request.form.get('buyer', ''), 100) if is_printed else None
        zone_code = sanitize_input(request.form.get('zone', ''), 10)
        date_received = datetime.now().date()
        
        # Validate required fields
        if not all([material, item_type, micron_label, weight is not None, quantity is not None, zone_code]):
            flash('All required fields must be filled', 'error')
            zones = Zone.query.order_by(Zone.code).all()
            return render_template('stock_in.html', zones=zones)
        
        if item_type not in ['roll', 'sheet', 'bag']:
            flash('Invalid item type', 'error')
            zones = Zone.query.order_by(Zone.code).all()
            return render_template('stock_in.html', zones=zones)
        
        if material not in ['PE', 'HDPE', 'PP']:
            flash('Invalid material', 'error')
            zones = Zone.query.order_by(Zone.code).all()
            return render_template('stock_in.html', zones=zones)
        
        # Validate zone exists
        if not Zone.query.get(zone_code):
            flash('Invalid zone', 'error')
            zones = Zone.query.order_by(Zone.code).all()
            return render_template('stock_in.html', zones=zones)
        
        # Validate dimensions based on item type
        if item_type in ['roll', 'sheet'] and width <= 0:
            flash('Width is required for rolls and sheets', 'error')
            zones = Zone.query.order_by(Zone.code).all()
            return render_template('stock_in.html', zones=zones)
        
        if item_type == 'bag' and length <= 0:
            flash('Length is required for bags', 'error')
            zones = Zone.query.order_by(Zone.code).all()
            return render_template('stock_in.html', zones=zones)
        
        # Find or create item
        item = Item.query.filter_by(
            item_type=item_type,
            material=material,
            width_inches=width if item_type != 'bag' else length,
            length_inches=length if item_type == 'bag' else None,
            micron_label=micron_label,
            is_printed=is_printed,
            buyer_name=buyer_name
        ).first()
        
        if not item:
            item = Item(
                item_type=item_type,
                material=material,
                width_inches=width if item_type != 'bag' else length,
                length_inches=length if item_type == 'bag' else None,
                micron_label=micron_label,
                is_printed=is_printed,
                print_details=print_details,
                buyer_name=buyer_name
            )
            db.session.add(item)
            db.session.flush()  # Get item ID
        
        # Create or update stock record
        existing_stock = Stock.query.filter_by(
            item_id=item.id,
            zone_code=zone_code,
            date_received=date_received
        ).first()
        
        if existing_stock:
            existing_stock.quantity_pieces += quantity
            existing_stock.quantity_kg += weight
        else:
            stock = Stock(
                item_id=item.id,
                zone_code=zone_code,
                quantity_pieces=quantity,
                quantity_kg=weight,
                date_received=date_received
            )
            db.session.add(stock)
        
        # Log transaction
        transaction = Transaction(
            transaction_type='IN',
            item_type=item_type,
            material=material,
            width_inches=width if item_type != 'bag' else length,
            length_inches=length if item_type == 'bag' else None,
            micron_label=micron_label,
            is_printed=is_printed,
            buyer_name=buyer_name,
            zone_code=zone_code,
            quantity_pieces=quantity,
            quantity_kg=weight,
            user_id=current_user.id,
            notes=f"Stock IN - {item.display_name}"
        )
        db.session.add(transaction)
        
        db.session.commit()
        
        # Audit log
        current_app.log_audit('stock', item.id, 'INSERT', 
                             None, {'quantity': quantity, 'weight': weight, 'zone': zone_code})
        
        flash('Stock added successfully', 'success')
        return redirect(url_for('stock.stock_directory'))
    
    # GET request - show form
    zones = Zone.query.order_by(Zone.code).all()
    return render_template('stock_in.html', zones=zones)

@stock_bp.route('/audit')
@login_required
def audit_log():
    trans_type = request.args.get('type', 'all')
    
        # Build query (Removed Item join because Transaction already has all item details)
    query = Transaction.query.join(User, Transaction.user_id == User.id)
    
    if trans_type != 'all':
        query = query.filter(Transaction.transaction_type == trans_type)
    
    transactions = query.order_by(Transaction.executed_at.desc()).all()
    types = ['IN', 'OUT', 'RETURN']
    
    return render_template('audit_log.html', 
                         transactions=transactions,
                         types=types,
                         selected_type=trans_type)

@stock_bp.route('/supervisor-usage')
@login_required
def supervisor_usage():
    if current_user.role != 'owner':
        flash('Unauthorized access', 'error')
        return redirect(url_for('stock.stock_directory'))
    
    from datetime import timedelta
    
    # Get week parameter (default to current week)
    week_offset = request.args.get('week', 0, type=int)
    today = datetime.now().date()
    start_of_week = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    end_of_week = start_of_week + timedelta(days=6)
    
    # Query OUT transactions by supervisors for the selected week
    usage_data = db.session.query(
        User.username,
        User.id,
        func.sum(Transaction.quantity_kg).label('total_kg'),
        func.count(Transaction.id).label('transaction_count')
    ).join(Transaction, Transaction.user_id == User.id)\
    .filter(
        User.role == 'supervisor',
        Transaction.transaction_type == 'OUT',
        func.date(Transaction.executed_at) >= start_of_week,
        func.date(Transaction.executed_at) <= end_of_week
    ).group_by(User.id, User.username).all()
    
    # Also get all supervisors for those with 0 usage
    all_supervisors = User.query.filter_by(role='supervisor', is_active=True).all()
    supervisor_dict = {s.id: s.username for s in all_supervisors}
    
    # Build complete list including supervisors with 0 usage
    result = []
    for sup_id, username in supervisor_dict.items():
        match = next((u for u in usage_data if u.id == sup_id), None)
        if match:
            result.append({
                'username': username,
                'total_kg': float(match.total_kg),
                'transaction_count': match.transaction_count
            })
        else:
            result.append({
                'username': username,
                'total_kg': 0.0,
                'transaction_count': 0
            })
    
    # Sort by total kg descending
    result.sort(key=lambda x: x['total_kg'], reverse=True)
    
    # Calculate grand total
    grand_total_kg = sum(r['total_kg'] for r in result)
    
    return render_template('supervisor_usage.html',
                         usage_data=result,
                         grand_total_kg=grand_total_kg,
                         week_offset=week_offset,
                         start_of_week=start_of_week,
                         end_of_week=end_of_week)

@stock_bp.route('/out/<int:item_id>', methods=['GET', 'POST'])
@login_required
def stock_out(item_id):
    # Only Store Keepers and Owners can do this
    if current_user.role not in ['store_keeper', 'owner']:
        flash('Permission denied.', 'error')
        return redirect(url_for('stock.stock_directory'))

    item = db.session.get(Item, item_id)
    if not item:
        flash('Item not found.', 'error')
        return redirect(url_for('stock.stock_directory'))

    if request.method == 'POST':
        quantity_to_deduct = validate_float(request.form.get('quantity_pieces', 0), 0.01, 100000)
        notes = sanitize_input(request.form.get('notes', ''), 500)

        if quantity_to_deduct is None:
            flash('Please enter a valid quantity.', 'error')
            return redirect(request.url)
        
        if not notes:
            flash('Receiver name is required.', 'error')
            return redirect(request.url)

        # Get available stock for this item, oldest first (FIFO)
        available_stocks = Stock.query.filter_by(item_id=item.id)\
            .filter(Stock.quantity_pieces > 0)\
            .order_by(Stock.date_received.asc()).all()

        total_available = sum(s.quantity_pieces for s in available_stocks)
        if total_available < quantity_to_deduct:
            flash(f'Not enough stock! Available: {total_available:.2f}, Requested: {quantity_to_deduct:.2f}', 'error')
            return redirect(request.url)

        # Deduct stock
        remaining = quantity_to_deduct
        
        for stock in available_stocks:
            if remaining <= 0:
                break
            
            deduct_amount = min(stock.quantity_pieces, remaining)
            stock.quantity_pieces -= deduct_amount
            remaining -= deduct_amount
            
            # Calculate proportional KG to deduct so your weights stay accurate
            original_pieces = stock.quantity_pieces + deduct_amount
            if original_pieces > 0:
                kg_ratio = stock.quantity_kg / original_pieces
                deduct_kg = deduct_amount * kg_ratio
                stock.quantity_kg -= deduct_kg
            else:
                deduct_kg = 0

            # Log the transaction matching YOUR detailed Transaction model
            transaction = Transaction(
                transaction_type='OUT',
                item_type=item.item_type,
                material=item.material,
                width_inches=item.width_inches,
                length_inches=item.length_inches,
                micron_label=item.micron_label,
                is_printed=item.is_printed,
                buyer_name=item.buyer_name,
                zone_code=stock.zone_code,
                quantity_pieces=deduct_amount,
                quantity_kg=deduct_kg,
                user_id=current_user.id,
                notes=f"Direct Stock Out - Given to: {notes}"
            )
            db.session.add(transaction)

        db.session.commit()
        
        # Audit log
        current_app.log_audit('stock', item.id, 'DELETE', 
                             {'quantity': quantity_to_deduct}, {'deducted': quantity_to_deduct})
        
        flash(f'Successfully stocked out {quantity_to_deduct:.2f} pieces of {item.display_name}.', 'success')
        return redirect(url_for('stock.stock_directory'))

    return render_template('stock_out.html', item=item)                         