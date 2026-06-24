from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from models import db, Request, Item, Stock, Transaction
from datetime import datetime
from sqlalchemy import func
import re

requests_bp = Blueprint('requests', __name__)

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

@requests_bp.route('/')
@login_required
def requests_list():
    if current_user.role == 'supervisor':
        # Supervisors see only their own requests
        requests = Request.query.filter_by(requested_by=current_user.id)\
            .order_by(Request.created_at.desc()).all()
    elif current_user.role == 'store_keeper':
        # Store keeper sees pending requests
        requests = Request.query.filter_by(status='pending')\
            .order_by(Request.created_at.desc()).all()
    else:
        # Owners see all requests (read-only)
        requests = Request.query.order_by(Request.created_at.desc()).all()
    
    return render_template('requests.html', requests=requests)

@requests_bp.route('/new', methods=['POST'])
@login_required
def create_request():
    if current_user.role != 'supervisor':
        flash('Only supervisors can create requests', 'error')
        return redirect(url_for('stock.stock_directory'))
    
    item_id = validate_float(request.form.get('item_id'), 1, 1000000)
    quantity = validate_float(request.form.get('quantity', 0), 0.01, 100000)
    notes = sanitize_input(request.form.get('notes', ''), 500)
    
    if item_id is None or quantity is None:
        flash('Invalid input data', 'error')
        return redirect(url_for('stock.stock_directory'))
    
    # Verify item exists
    item = Item.query.get(int(item_id))
    if not item:
        flash('Item not found', 'error')
        return redirect(url_for('stock.stock_directory'))
    
    request_obj = Request(
        requested_by=current_user.id,
        item_id=int(item_id),
        quantity_pieces_requested=quantity,
        notes=notes,
        status='pending'
    )
    
    db.session.add(request_obj)
    db.session.commit()
    
    # Audit log
    current_app.log_audit('requests', request_obj.id, 'INSERT', 
                         None, {'item_id': item_id, 'quantity': quantity})
    
    flash('Request submitted successfully', 'success')
    return redirect(url_for('requests.requests_list'))

@requests_bp.route('/<int:request_id>/fulfill', methods=['POST'])
@login_required
def fulfill_request(request_id):
    if current_user.role != 'store_keeper':
        flash('Only store keepers can fulfill requests', 'error')
        return redirect(url_for('requests.requests_list'))
    
    req = Request.query.get_or_404(request_id)
    
    if req.status != 'pending':
        flash('Request already fulfilled', 'error')
        return redirect(url_for('requests.requests_list'))
    
    # Get receiver name from form
    receiver_name = sanitize_input(request.form.get('receiver_name', ''), 500)
    if not receiver_name:
        flash('Receiver name is required.', 'error')
        return redirect(url_for('requests.requests_list'))
    
    # Get available stock for this item, oldest first (FIFO)
    available_stocks = Stock.query.filter_by(item_id=req.item_id)\
        .filter(Stock.quantity_pieces > 0)\
        .order_by(Stock.date_received.asc()).all()
    
    total_available = sum(s.quantity_pieces for s in available_stocks)
    if total_available < req.quantity_pieces_requested:
        flash(f'Insufficient stock available. Available: {total_available:.2f}, Requested: {req.quantity_pieces_requested:.2f}', 'error')
        return redirect(url_for('requests.requests_list'))
    
    # Get item details for transaction
    item = Item.query.get(req.item_id)
    if not item:
        flash('Item no longer exists', 'error')
        return redirect(url_for('requests.requests_list'))
    
    # Deduct stock using FIFO (same logic as stock_out)
    remaining = req.quantity_pieces_requested
    
    for stock in available_stocks:
        if remaining <= 0:
            break
        
        deduct_amount = min(stock.quantity_pieces, remaining)
        stock.quantity_pieces -= deduct_amount
        remaining -= deduct_amount
        
        # Calculate proportional KG to deduct
        original_pieces = stock.quantity_pieces + deduct_amount
        if original_pieces > 0:
            kg_ratio = stock.quantity_kg / original_pieces
            deduct_kg = deduct_amount * kg_ratio
            stock.quantity_kg -= deduct_kg
        else:
            deduct_kg = 0
        
        # Create transaction for each stock record
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
            request_id=req.id,
            user_id=current_user.id,
            notes=f"Stock OUT - Request #{req.id} by {req.requester.username} - Given to: {receiver_name}"
        )
        db.session.add(transaction)
    
    # Update request
    req.status = 'completed'
    req.fulfilled_by = current_user.id
    req.fulfilled_at = datetime.utcnow()
    
    db.session.commit()
    
    # Audit log
    current_app.log_audit('requests', req.id, 'UPDATE', 
                         {'status': 'pending'}, {'status': 'completed', 'fulfilled_by': current_user.id, 'receiver': receiver_name})
    
    flash('Request fulfilled successfully', 'success')
    return redirect(url_for('requests.requests_list'))