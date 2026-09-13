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
    micron_search = request.args.get('micron_search', '')
    width_search = request.args.get('width_search', '')
    
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
                db.cast(Item.length_inches, db.String).like(f'%{search_term}%')
            )
        )
    # ----------------------------
    
    if micron_search:
        query = query.filter(func.lower(Item.micron_label).like(f'%{micron_search.lower()}%'))
    
    if width_search:
        query = query.filter(db.cast(Item.width_inches, db.String).like(f'%{width_search}%'))
    
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
    
    # 1. Unprinted rolls + sheets total pieces (global, unfiltered)
    total_unprinted_qty = db.session.query(func.sum(Stock.quantity_pieces))\
        .join(Item)\
        .filter(Stock.quantity_pieces > 0, Item.is_printed == False, Item.item_type.in_(['roll', 'sheet']))\
        .scalar() or 0
    
    # 2. Weight by material (global, unfiltered)
    material_weight_totals = dict(db.session.query(
        Item.material, func.sum(Stock.quantity_kg)
    ).join(Stock)
        .filter(Stock.quantity_pieces > 0)
        .group_by(Item.material).all())
    
    return render_template('dashboard.html', 
                         stocks=stocks, 
                         materials=[m[0] for m in materials],
                         types=[t[0] for t in types],
                         selected_material=material,
                         selected_type=item_type,
                         search_term=search,
                         micron_search=micron_search,
                         width_search=width_search,
                         total_weight_kg=total_weight_kg,
                         total_unprinted_qty=total_unprinted_qty,
                         material_weight_totals=material_weight_totals)

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
        is_printed = request.form.get('printed') == 'on'
        print_details = sanitize_input(request.form.get('print_details', ''), 200) if is_printed else None
        buyer_name = sanitize_input(request.form.get('buyer', ''), 100) if is_printed else None
        zone_code = sanitize_input(request.form.get('zone', ''), 10)
        date_received = datetime.now().date()
        
        # Quantity is always 1 piece per stock entry
        quantity = 1.0
        
        # Bag-specific fields
        gusset = validate_float(request.form.get('gusset'), 0, 10000)
        flap = validate_float(request.form.get('flap'), 0, 10000)
        brand_name = sanitize_input(request.form.get('brand_name', ''), 100)
        handle_type = sanitize_input(request.form.get('handle_type', ''), 20)
        
        # Roll-specific fields
        has_gusset = request.form.get('has_gusset') == 'on'
        gusset_type = sanitize_input(request.form.get('gusset_type', ''), 20) if has_gusset else None
        gusset_length_inches = validate_float(request.form.get('gusset_length'), 0, 10000) if has_gusset else None
        color = sanitize_input(request.form.get('color', ''), 50)
        
        # Unit conversion for bags only
        dimension_unit = request.form.get('dimension_unit', 'inch')
        
        # Convert cm to inches (1 inch = 2.54 cm) for bags
        if item_type == 'bag':
            if dimension_unit == 'cm':
                width = width / 2.54 if width else 0
                length = length / 2.54 if length else 0
        
        # Validate required fields
        if not all([material, item_type, micron_label, weight is not None, zone_code]):
            flash('All required fields must be filled', 'error')
            zones = Zone.query.order_by(Zone.code).all()
            return render_template('stock_in.html', zones=zones)
        
        if item_type not in ['roll', 'sheet', 'bag']:
            flash('Invalid item type', 'error')
            zones = Zone.query.order_by(Zone.code).all()
            return render_template('stock_in.html', zones=zones)
        
        if material not in ['PE', 'HDPE', 'PP', 'PE - Recycle', 'HDPE - Recycle']:
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
        
        if item_type == 'bag':
            if length <= 0:
                flash('Length is required for bags', 'error')
                zones = Zone.query.order_by(Zone.code).all()
                return render_template('stock_in.html', zones=zones)
            if width <= 0:
                flash('Width is required for bags', 'error')
                zones = Zone.query.order_by(Zone.code).all()
                return render_template('stock_in.html', zones=zones)
        
        # Validate micron format based on item type
        micron_error = validate_micron(micron_label, item_type)
        if micron_error:
            flash(micron_error, 'error')
            zones = Zone.query.order_by(Zone.code).all()
            return render_template('stock_in.html', zones=zones)
        
        # Find or create item
        item = Item.query.filter_by(
            item_type=item_type,
            material=material,
            width_inches=width,
            length_inches=length,
            micron_label=micron_label,
            is_printed=is_printed,
            buyer_name=buyer_name
        ).first()
        
        if not item:
            item = Item(
                item_type=item_type,
                material=material,
                width_inches=width,
                length_inches=length,
                micron_label=micron_label,
                is_printed=is_printed,
                print_details=print_details,
                buyer_name=buyer_name,
                gusset_inches=gusset if item_type == 'bag' else None,
                flap_inches=flap if item_type == 'bag' else None,
                brand_name=brand_name if item_type == 'bag' else None,
                handle_type=handle_type if item_type == 'bag' else None,
                gusset_type=gusset_type if item_type == 'roll' else None,
                gusset_length_inches=gusset_length_inches if item_type == 'roll' else None,
                color=color if item_type == 'roll' else None
            )
            db.session.add(item)
            db.session.flush()  # Get item ID
        
        # Always create a new stock record (no merging)
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
            width_inches=width,
            length_inches=length,
            micron_label=micron_label,
            is_printed=is_printed,
            buyer_name=buyer_name,
            zone_code=zone_code,
            quantity_pieces=quantity,
            quantity_kg=weight,
            user_id=current_user.id,
            notes=f"Stock IN - {item.display_name}",
            gusset_inches=gusset if item_type == 'bag' else None,
            flap_inches=flap if item_type == 'bag' else None,
            brand_name=brand_name if item_type == 'bag' else None,
            handle_type=handle_type if item_type == 'bag' else None,
            gusset_type=gusset_type if item_type == 'roll' else None,
            gusset_length_inches=gusset_length_inches if item_type == 'roll' else None,
            color=color if item_type == 'roll' else None
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


def validate_micron(micron_label, item_type):
    """Validate micron format based on item type"""
    import re
    
    if item_type == 'sheet':
        # Sheet: single integer > 20, no slash
        if '/' in micron_label:
            return 'Sheet micron must be a single integer (no "/")'
        if not re.match(r'^\d+$', micron_label):
            return 'Sheet micron must be a single integer'
        value = int(micron_label)
        if value <= 20:
            return 'Sheet micron must be greater than 20'
    
    elif item_type == 'roll':
        # Roll: format x/y, both > 20, y = 2*x
        if '/' not in micron_label:
            return 'Roll micron must be in format x/y (e.g., 40/80)'
        parts = micron_label.split('/')
        if len(parts) != 2:
            return 'Roll micron must be in format x/y (e.g., 40/80)'
        if not re.match(r'^\d+$', parts[0]) or not re.match(r'^\d+$', parts[1]):
            return 'Roll micron values must be integers'
        x = int(parts[0])
        y = int(parts[1])
        if x <= 20 or y <= 20:
            return 'Both micron values must be greater than 20'
        if y != 2 * x:
            return 'Second value must be exactly double the first (e.g., 40/80)'
    
    elif item_type == 'bag':
        # Bag: single integer > 0
        if '/' in micron_label:
            return 'Bag micron must be a single integer'
        if not re.match(r'^\d+$', micron_label):
            return 'Bag micron must be a single integer'
        value = int(micron_label)
        if value <= 0:
            return 'Bag micron must be greater than 0'
    
    return None

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

# Merged Usage Report
@stock_bp.route('/usage-report')
@login_required
def usage_report():
    if current_user.role not in ['owner', 'store_keeper']:
        flash('Unauthorized access', 'error')
        return redirect(url_for('stock.stock_directory'))
    
    from datetime import timedelta, time
    import re
    
    def normalize_name(name: str) -> str:
        """Strip, lower-case, remove leading honorifics (mr., mrs., ms., dr., eng.), collapse multiple spaces."""
        if not name:
            return ''
        name = name.strip().lower()
        # Remove common honorifics
        for honorific in ['mr.', 'mrs.', 'ms.', 'dr.', 'eng.']:
            if name.startswith(honorific):
                name = name[len(honorific):].strip()
        # Collapse multiple spaces
        name = re.sub(r'\s+', ' ', name)
        return name
    
    # Get date parameter (default to today)
    date_str = request.args.get('date')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = datetime.now().date()
    else:
        selected_date = datetime.now().date()
    
    # Query OUT transactions for the selected date using date range (handles timezone issues)
    start_of_day = datetime.combine(selected_date, time.min)
    end_of_day = datetime.combine(selected_date, time.max)
    
    transactions = Transaction.query.filter(
        Transaction.transaction_type == 'OUT',
        Transaction.executed_at >= start_of_day,
        Transaction.executed_at <= end_of_day
    ).all()
    
    # --- Supervisor Usage ---
    supervisor_data = {}
    for tx in transactions:
        user = User.query.get(tx.user_id)
        if user and user.role == 'supervisor':
            username = user.username
            if username not in supervisor_data:
                supervisor_data[username] = {'total_kg': 0.0, 'transaction_count': 0}
            supervisor_data[username]['total_kg'] += float(tx.quantity_kg or 0)
            supervisor_data[username]['transaction_count'] += 1
    
    # Get all supervisors for those with 0 usage
    all_supervisors = User.query.filter_by(role='supervisor', is_active=True).all()
    supervisor_dict = {s.username: s for s in all_supervisors}
    
    # Build supervisor result list
    supervisor_result = []
    for username in sorted(supervisor_dict.keys()):
        data = supervisor_data.get(username, {'total_kg': 0.0, 'transaction_count': 0})
        supervisor_result.append({
            'username': username,
            'total_kg': data['total_kg'],
            'transaction_count': data['transaction_count']
        })
    
    # --- Receiver Usage ---
    receiver_data = {}
    for tx in transactions:
        receiver = None
        if tx.notes:
            if 'Given to:' in tx.notes:
                receiver = tx.notes.split('Given to:')[-1].strip()
            elif 'Handed to' in tx.notes:
                receiver = tx.notes.split('Handed to')[-1].strip()
        
        if receiver:
            normalized = receiver.strip().title()
            if normalized not in receiver_data:
                receiver_data[normalized] = {'display_name': receiver, 'total_kg': 0.0, 'transaction_count': 0}
            receiver_data[normalized]['total_kg'] += float(tx.quantity_kg or 0)
            receiver_data[normalized]['transaction_count'] += 1
    
    # Build receiver result list
    receiver_result = []
    for normalized, data in receiver_data.items():
        receiver_result.append({
            'receiver': data['display_name'],
            'total_kg': data['total_kg'],
            'transaction_count': data['transaction_count']
        })
    
    # --- Merge matching supervisor and receiver entries ---
    def normalize_name(name: str) -> str:
        """Strip, lower-case, remove leading honorifics (mr., mrs., ms., dr., eng.), collapse multiple spaces."""
        if not name:
            return ''
        name = name.strip().lower()
        # Remove common honorifics
        for honorific in ['mr.', 'mrs.', 'ms.', 'dr.', 'eng.']:
            if name.startswith(honorific):
                name = name[len(honorific):].strip()
        # Collapse multiple spaces
        name = re.sub(r'\s+', ' ', name)
        return name
    
    # --- Merge matching supervisor and receiver entries ---
    # Create normalized lookup for receivers
    receiver_lookup = {}
    for r in receiver_result:
        normalized = normalize_name(r['receiver'])
        receiver_lookup[normalized] = r
    
    # Track which receivers have been merged
    merged_receivers = set()
    
    # Merge matching entries
    merged_supervisor_result = []
    for sup in supervisor_result:
        sup_norm = normalize_name(sup['username'])
        matching_receiver = receiver_lookup.get(sup_norm)
        
        if matching_receiver:
            # Match found - merge into single entry
            merged_supervisor_result.append({
                'username': sup['username'],
                'total_kg': sup['total_kg'] + matching_receiver['total_kg'],
                'transaction_count': sup['transaction_count'] + matching_receiver['transaction_count'],
                'is_merged': True,
                'receiver_name': matching_receiver['receiver']
            })
            merged_receivers.add(normalize_name(matching_receiver['receiver']))
        else:
            # No match - keep as supervisor only
            merged_supervisor_result.append({
                'username': sup['username'],
                'total_kg': sup['total_kg'],
                'transaction_count': sup['transaction_count'],
                'is_merged': False,
                'receiver_name': None
            })
    
    # Add receivers that didn't match any supervisor
    unmatched_receiver_result = []
    for rec in receiver_result:
        rec_norm = normalize_name(rec['receiver'])
        if rec_norm not in merged_receivers:
            unmatched_receiver_result.append({
                'receiver': rec['receiver'],
                'total_kg': rec['total_kg'],
                'transaction_count': rec['transaction_count'],
                'is_merged': False
            })
    
    # Sort both by total kg descending
    merged_supervisor_result.sort(key=lambda x: x['total_kg'], reverse=True)
    unmatched_receiver_result.sort(key=lambda x: x['total_kg'], reverse=True)
    
    # Combined totals
    supervisor_total_kg = sum(r['total_kg'] for r in merged_supervisor_result)
    receiver_total_kg = sum(r['total_kg'] for r in unmatched_receiver_result)
    combined_total_kg = supervisor_total_kg + receiver_total_kg
    supervisor_tx_count = sum(r['transaction_count'] for r in merged_supervisor_result)
    receiver_tx_count = sum(r['transaction_count'] for r in unmatched_receiver_result)
    combined_tx_count = supervisor_tx_count + receiver_tx_count
    
    return render_template('usage_report.html',
                         selected_date=selected_date,
                         prev_date=selected_date - timedelta(days=1),
                         next_date=selected_date + timedelta(days=1),
                         supervisor_usage=merged_supervisor_result,
                         receiver_usage=unmatched_receiver_result,
                         supervisor_total_kg=supervisor_total_kg,
                         receiver_total_kg=receiver_total_kg,
                         combined_total_kg=combined_total_kg,
                         supervisor_tx_count=supervisor_tx_count,
                         receiver_tx_count=receiver_tx_count,
                         combined_tx_count=combined_tx_count)

@stock_bp.route('/out/<int:item_id>', methods=['GET', 'POST'])
@login_required
def stock_out(item_id):
    # Only Store Keepers can do this
    if current_user.role != 'store_keeper':
        flash('Permission denied.', 'error')
        return redirect(url_for('stock.stock_directory'))

    item = db.session.get(Item, item_id)
    if not item:
        flash('Item not found.', 'error')
        return redirect(url_for('stock.stock_directory'))

    if request.method == 'POST':
        notes = sanitize_input(request.form.get('notes', ''), 500)
        
        if not notes:
            flash('Receiver name is required.', 'error')
            return redirect(request.url)
        
        # Get selected stock records
        stock_ids = request.form.getlist('stock_id')
        if not stock_ids:
            flash('Please select at least one stock record.', 'error')
            return redirect(request.url)
        
        total_deducted_pieces = 0
        total_deducted_kg = 0
        
        for stock_id in stock_ids:
            stock = db.session.get(Stock, int(stock_id))
            if not stock or stock.item_id != item.id:
                continue
            
            # Deduct exactly 1 piece per selected stock record
            qty = 1.0
            
            if stock.quantity_pieces < qty:
                flash(f'Insufficient stock in record #{stock_id}.', 'error')
                return redirect(request.url)
            
            # Calculate proportional KG
            original_pieces = stock.quantity_pieces
            if original_pieces > 0:
                kg_ratio = stock.quantity_kg / original_pieces
                deduct_kg = qty * kg_ratio
            else:
                deduct_kg = 0
            
            # Deduct from stock
            stock.quantity_pieces -= qty
            stock.quantity_kg -= deduct_kg
            total_deducted_pieces += qty
            total_deducted_kg += deduct_kg
            
            # Log transaction
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
                quantity_pieces=qty,
                quantity_kg=deduct_kg,
                user_id=current_user.id,
                notes=f"Direct Stock Out - Given to: {notes}",
                gusset_inches=item.gusset_inches,
                flap_inches=item.flap_inches,
                brand_name=item.brand_name,
                handle_type=item.handle_type,
                gusset_type=item.gusset_type,
                gusset_length_inches=item.gusset_length_inches,
                color=item.color
            )
            db.session.add(transaction)
        
        if total_deducted_pieces == 0:
            flash('No valid quantities entered.', 'error')
            return redirect(request.url)
        
        db.session.commit()
        
        # Audit log
        current_app.log_audit('stock', item.id, 'DELETE', 
                             {'quantity': total_deducted_pieces}, {'deducted': total_deducted_pieces})
        
        flash(f'Successfully stocked out {total_deducted_pieces:.0f} pieces ({total_deducted_kg:.2f} kg) of {item.display_name}.', 'success')
        return redirect(url_for('stock.stock_directory'))

    # GET - show stock selection
    available_stocks = Stock.query.filter_by(item_id=item.id)\
        .filter(Stock.quantity_pieces > 0)\
        .order_by(Stock.date_received.asc()).all()
    
    if not available_stocks:
        flash('No stock available for this item.', 'error')
        return redirect(url_for('stock.stock_directory'))
    
    # Calculate weight per piece for each stock record
    stock_data = []
    for stock in available_stocks:
        wt_per_piece = stock.quantity_kg / stock.quantity_pieces if stock.quantity_pieces > 0 else 0
        stock_data.append({
            'id': stock.id,
            'zone_code': stock.zone_code,
            'quantity_pieces': stock.quantity_pieces,
            'quantity_kg': stock.quantity_kg,
            'wt_per_piece': wt_per_piece,
            'date_received': stock.date_received
        })
    
    return render_template('stock_out_select.html', item=item, stock_data=stock_data)                         