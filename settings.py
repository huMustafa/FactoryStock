from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from models import db, User, Zone
import json
import re

settings_bp = Blueprint('settings', __name__)

def validate_password(password):
    """Validate password strength"""
    if not password or len(password) < 8:
        return False, 'Password must be at least 8 characters'
    if not re.search(r'[A-Z]', password):
        return False, 'Password must contain at least one uppercase letter'
    if not re.search(r'[a-z]', password):
        return False, 'Password must contain at least one lowercase letter'
    if not re.search(r'\d', password):
        return False, 'Password must contain at least one digit'
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, 'Password must contain at least one special character'
    return True, ''

def sanitize_input(text, max_length=200):
    """Sanitize user input to prevent XSS"""
    if not text:
        return ''
    # Remove potentially dangerous characters
    text = re.sub(r'[<>\"\'&]', '', text)
    return text[:max_length]

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
    
    username = sanitize_input(request.form.get('username', ''), 80)
    email = sanitize_input(request.form.get('email', ''), 120)
    password = request.form.get('password', '')
    role = sanitize_input(request.form.get('role', ''), 20)
    
    if not username or not password or not role:
        flash('All fields are required', 'error')
        return redirect(url_for('settings.settings'))
    
    if role not in ['store_keeper', 'supervisor', 'owner']:
        flash('Invalid role', 'error')
        return redirect(url_for('settings.settings'))
    
    if User.query.filter_by(username=username).first():
        flash('Username already exists', 'error')
        return redirect(url_for('settings.settings'))
    
    # Validate password strength
    valid, msg = validate_password(password)
    if not valid:
        flash(msg, 'error')
        return redirect(url_for('settings.settings'))
    
    user = User(username=username, email=email, role=role)
    if user.set_password(password):
        db.session.add(user)
        db.session.commit()
        
        # Audit log
        current_app.log_audit('users', user.id, 'INSERT', 
                             None, {'username': username, 'role': role})
        
        flash('User added successfully', 'success')
    else:
        flash('Invalid password', 'error')
    
    return redirect(url_for('settings.settings'))

@settings_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
def toggle_user(user_id):
    if current_user.role != 'owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    user = User.query.get_or_404(user_id)
    old_status = user.is_active
    user.is_active = not user.is_active
    db.session.commit()
    
    # Audit log
    current_app.log_audit('users', user.id, 'UPDATE', 
                         {'is_active': old_status}, {'is_active': user.is_active})
    
    flash(f'User {"activated" if user.is_active else "deactivated"}', 'success')
    return redirect(url_for('settings.settings'))

@settings_bp.route('/zones/add', methods=['POST'])
@login_required
def add_zone():
    if current_user.role != 'owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    code = sanitize_input(request.form.get('code', ''), 10)
    description = sanitize_input(request.form.get('description', ''), 100)
    
    if not code:
        flash('Zone code is required', 'error')
        return redirect(url_for('settings.settings'))
    
    zone = Zone(code=code, description=description)
    try:
        db.session.add(zone)
        db.session.commit()
        
        # Audit log
        current_app.log_audit('zones', zone.code, 'INSERT', 
                             None, {'code': code, 'description': description})
        
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
    zone_code = zone.code
    zone_desc = zone.description
    db.session.delete(zone)
    db.session.commit()
    
    # Audit log
    current_app.log_audit('zones', zone_code, 'DELETE', 
                         {'code': zone_code, 'description': zone_desc}, None)
    
    flash('Zone deleted successfully', 'success')
    return redirect(url_for('settings.settings'))