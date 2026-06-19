from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import db, User, Zone
import json

settings_bp = Blueprint('settings', __name__)

@settings_bp.route('/')
@login_required
def settings():
    if current_user.role != 'owner':
        flash('Unauthorized access', 'error')
        return redirect(url_for('stock.stock_directory'))
    
    users = User.query.all()
    zones = Zone.query.order_by(Zone.code).all()
    
    return render_template('settings.html', users=users, zones=zones)

@settings_bp.route('/users/add', methods=['POST'])
@login_required
def add_user():
    if current_user.role != 'owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    role = request.form.get('role')
    
    if User.query.filter_by(username=username).first():
        flash('Username already exists', 'error')
        return redirect(url_for('settings.settings'))
    
    user = User(username=username, email=email, role=role)
    if user.set_password(password):
        db.session.add(user)
        db.session.commit()
        flash('User added successfully', 'success')
    else:
        flash('Password must be at least 4 characters and alphanumeric', 'error')
    
    return redirect(url_for('settings.settings'))

@settings_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
def toggle_user(user_id):
    if current_user.role != 'owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    user = User.query.get_or_404(user_id)
    user.is_active = not user.is_active
    db.session.commit()
    
    flash(f'User {"activated" if user.is_active else "deactivated"}', 'success')
    return redirect(url_for('settings.settings'))

@settings_bp.route('/zones/add', methods=['POST'])
@login_required
def add_zone():
    if current_user.role != 'owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    code = request.form.get('code')
    description = request.form.get('description')
    
    zone = Zone(code=code, description=description)
    try:
        db.session.add(zone)
        db.session.commit()
        flash('Zone added successfully', 'success')
    except:
        db.session.rollback()
        flash('Zone code already exists', 'error')
    
    return redirect(url_for('settings.settings'))

@settings_bp.route('/zones/<code>/delete', methods=['POST'])
@login_required
def delete_zone(code):
    if current_user.role != 'owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    zone = Zone.query.get_or_404(code)
    db.session.delete(zone)
    db.session.commit()
    
    flash('Zone deleted successfully', 'success')
    return redirect(url_for('settings.settings'))