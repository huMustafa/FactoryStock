from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Request, Item, Stock, Transaction
from datetime import datetime

requests_bp = Blueprint('requests', __name__)

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
    
    item_id = request.form.get('item_id')
    quantity = float(request.form.get('quantity'))
    notes = request.form.get('notes')
    
    request_obj = Request(
        requested_by=current_user.id,
        item_id=item_id,
        quantity_pieces_requested=quantity,
        notes=notes,
        status='pending'
    )
    
    db.session.add(request_obj)
    db.session.commit()
    
    flash('Request submitted successfully', 'success')
    return redirect(url_for('requests.requests_list'))

@requests_bp.route('/<int:request_id>/fulfill', methods=['POST'])
@login_required
def fulfill_request(request_id):
    if current_user.role != 'store_keeper':
        flash('Only store keepers can fulfill requests', 'error')
        return redirect(url_for('requests.requests_list'))
    
    req = Request.query.get_or_404(request_id)
    
    # Get stock for this item
    stock = Stock.query.filter_by(item_id=req.item_id).first()
    
    if not stock or stock.quantity_pieces < req.quantity_pieces_requested:
        flash('Insufficient stock available', 'error')
        return redirect(url_for('requests.requests_list'))
    
    # Deduct stock
    stock.quantity_pieces -= req.quantity_pieces_requested
    # Calculate weight proportionally
    weight_per_piece = stock.quantity_kg / (stock.quantity_pieces + req.quantity_pieces_requested)
    weight_to_deduct = weight_per_piece * req.quantity_pieces_requested
    stock.quantity_kg -= weight_to_deduct
    
    # Create transaction
    item = Item.query.get(req.item_id)
    if not item:
        flash('Item no longer exists', 'error')
        return redirect(url_for('requests.requests_list'))

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
        quantity_pieces=req.quantity_pieces_requested,
        quantity_kg=weight_to_deduct,
        request_id=req.id,
        user_id=current_user.id,
        notes=f"Stock OUT - Request #{req.id} by {req.requester.username}"
    )
    
    db.session.add(transaction)
    
    # Update request
    req.status = 'completed'
    req.fulfilled_by = current_user.id
    req.fulfilled_at = datetime.utcnow()
    
    db.session.commit()
    
    flash('Request fulfilled successfully', 'success')
    return redirect(url_for('requests.requests_list'))