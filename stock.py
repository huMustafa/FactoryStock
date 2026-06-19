from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import db, Item, Stock, Transaction, Zone, User
from datetime import datetime
from sqlalchemy import or_, func

stock_bp = Blueprint('stock', __name__)

@stock_bp.route('/')
@login_required
def stock_directory():
    # Get filter parameters
    search = request.args.get('search', '')
    material = request.args.get('material', 'all')
    item_type = request.args.get('type', 'all')
    
    # Build query
    query = Stock.query.join(Item).join(Zone)
    
    # --- UPDATED SEARCH LOGIC ---
    if search:
        search_term = search.lower()
        query = query.filter(
            or_(
                func.lower(Item.buyer_name).like(f'%{search_term}%'),
                func.lower(Item.print_details).like(f'%{search_term}%'),
                func.lower(Item.material).like(f'%{search_term}%'),
                func.lower(Item.micron_label).like(f'%{search_term}%'),
                func.lower(Zone.code).like(f'%{search_term}%')
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
    
    return render_template('dashboard.html', 
                         stocks=stocks, 
                         materials=[m[0] for m in materials],
                         types=[t[0] for t in types],
                         selected_material=material,
                         selected_type=item_type,
                         search_term=search)

@stock_bp.route('/in', methods=['GET', 'POST'])
@login_required
def stock_in():
    if current_user.role != 'store_keeper':
        flash('Unauthorized access', 'error')
        return redirect(url_for('stock.stock_directory'))
    
    if request.method == 'POST':
        # Get form data
        material = request.form.get('material')
        item_type = request.form.get('type')
        width = float(request.form.get('width', 0) or 0)
        length = float(request.form.get('length', 0) or 0)
        micron_label = request.form.get('micron')
        weight = float(request.form.get('weight'))
        quantity = float(request.form.get('quantity'))
        is_printed = request.form.get('printed') == 'on'
        print_details = request.form.get('print_details') if is_printed else None
        buyer_name = request.form.get('buyer') if is_printed else None
        zone_code = request.form.get('zone')
        date_received = datetime.now().date()
        
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