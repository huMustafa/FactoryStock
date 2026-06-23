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
                func.lower(Zone.code).like(f'%{search_term}%'),
                
                # Search width by casting float to String
                db.cast(Item.width_inches, db.String).like(f'%{search_term}%'),
                
                # Search length for bags
                db.cast(Item.length_inches, db.String).like(f'%{search_term}%'),
                
                # Cast float columns to String so we can search decimals like "50.5"
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
        # Validate and sanitize form data
        material = request.form.get('material', '')[:50]
        item_type = request.form.get('type', '')[:20]
        
        try:
            width = float(request.form.get('width', 0) or 0)
            length = float(request.form.get('length', 0) or 0)
            weight = float(request.form.get('weight', 0))
            quantity = float(request.form.get('quantity', 0))
        except (ValueError, TypeError):
            flash('Invalid numeric values entered.', 'error')
            return redirect(request.url)
        
        micron_label = request.form.get('micron', '')[:20]
        is_printed = request.form.get('printed') == 'on'
        print_details = request.form.get('print_details', '')[:200] if is_printed else None
        buyer_name = request.form.get('buyer', '')[:100] if is_printed else None
        zone_code = request.form.get('zone', '')[:10]
        date_received = datetime.now().date()
        
        # Validate required fields
        if not material or not item_type or not micron_label or not zone_code:
            flash('Missing required fields.', 'error')
            return redirect(request.url)
        
        if quantity <= 0 or weight <= 0:
            flash('Quantity and weight must be greater than zero.', 'error')
            return redirect(request.url)
        
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
        # Validate and sanitize input
        try:
            quantity_to_deduct = float(request.form.get('quantity_pieces', 0))
        except (ValueError, TypeError):
            flash('Invalid quantity entered.', 'error')
            return redirect(request.url)
        
        notes = request.form.get('notes', '')[:500]  # Limit notes length

        if quantity_to_deduct <= 0:
            flash('Please enter a valid quantity greater than zero.', 'error')
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
                notes=f"Direct Stock Out. {notes}"
            )
            db.session.add(transaction)

        db.session.commit()
        flash(f'Successfully stocked out {quantity_to_deduct:.2f} pieces of {item.display_name}.', 'success')
        return redirect(url_for('stock.stock_directory'))

    return render_template('stock_out.html', item=item)                         