from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import re

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=True)  # Optional field
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'store_keeper', 'supervisor', 'owner'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    
    def set_password(self, password):
        # Strong password requirements: min 8 chars, upper, lower, digit, special
        if not password or len(password) < 8:
            return False
        if not re.search(r'[A-Z]', password):
            return False
        if not re.search(r'[a-z]', password):
            return False
        if not re.search(r'\d', password):
            return False
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            return False
        
        # Check password history (prevent reuse of last 5 passwords)
        from models import PasswordHistory
        if self.id:  # Only check history if user already exists
            recent_hashes = db.session.query(PasswordHistory.password_hash).filter(
                PasswordHistory.user_id == self.id
            ).order_by(PasswordHistory.created_at.desc()).limit(5).all()
            
            for (old_hash,) in recent_hashes:
                if check_password_hash(old_hash, password):
                    return False
        
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
        
        # Store in history if user already has ID (existing user)
        # For new users, call add_password_history after flush/commit
        if self.id:
            history = PasswordHistory(user_id=self.id, password_hash=self.password_hash)
            db.session.add(history)
        
        return True

    def add_password_history(self):
        """Add current password to history - call after user has an ID (after flush)"""
        from models import PasswordHistory
        if self.id and self.password_hash:
            history = PasswordHistory(user_id=self.id, password_hash=self.password_hash)
            db.session.add(history)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class PasswordHistory(db.Model):
    __tablename__ = 'password_history'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    
    user = db.relationship('User', backref='password_history')

class Zone(db.Model):
    __tablename__ = 'zones'
    
    code = db.Column(db.String(10), primary_key=True)
    description = db.Column(db.String(100), nullable=True)

class Item(db.Model):
    __tablename__ = 'items'
    
    id = db.Column(db.Integer, primary_key=True)
    item_type = db.Column(db.String(20), nullable=False)  # 'roll', 'sheet', 'bag'
    material = db.Column(db.String(20), nullable=False)  # 'PE', 'HDPE', 'PP'
    width_inches = db.Column(db.Float, nullable=True)
    length_inches = db.Column(db.Float, nullable=True)
    micron_label = db.Column(db.String(20), nullable=False)  # e.g., "60", "60/120"
    is_printed = db.Column(db.Boolean, default=False)
    print_details = db.Column(db.String(200), nullable=True)
    buyer_name = db.Column(db.String(100), nullable=True)
    
    # Bag-specific fields
    gusset_inches = db.Column(db.Float, nullable=True)
    flap_inches = db.Column(db.Float, nullable=True)
    brand_name = db.Column(db.String(100), nullable=True)
    handle_type = db.Column(db.String(20), nullable=True)  # 'D-cut', 'Loop', 'None'
    
    # Roll-specific fields
    gusset_type = db.Column(db.String(20), nullable=True)  # '1-side', '2-side', None
    gusset_length_inches = db.Column(db.Float, nullable=True)  # Gusset length for rolls
    color = db.Column(db.String(50), nullable=True)  # Roll color (optional)
    
    @property
    def display_name(self):
        name = f"{self.material} {self.item_type.capitalize()}"
        if self.item_type in ['roll', 'sheet']:
            name += f" {self.width_inches}\""
        elif self.item_type == 'bag':
            name += f" {self.length_inches}\"x{self.width_inches}\""
        
        name += f" {self.micron_label}µ"
        if self.item_type == 'roll':
            if self.gusset_type:
                name += f" Gusset:{self.gusset_type}"
            if self.color:
                name += f" {self.color}"
        if self.is_printed:
            name += " Printed"
            if self.buyer_name:
                name += f" ({self.buyer_name})"
        else:
            name += " Unprinted"
        return name
    
    @property
    def specs(self):
        if self.item_type == 'bag':
            parts = [f"{self.material} Bag", f"{self.length_inches}\"x{self.width_inches}\"", f"{self.micron_label}µ"]
            if self.gusset_inches:
                parts.append(f"Gusset: {self.gusset_inches}\"")
            if self.flap_inches:
                parts.append(f"Flap: {self.flap_inches}\"")
            if self.handle_type:
                parts.append(f"Handle: {self.handle_type}")
            if self.brand_name:
                parts.append(f"Brand: {self.brand_name}")
            return " | ".join(parts)
        elif self.item_type == 'roll':
            parts = [f"{self.material} Roll {self.width_inches}\" x {self.micron_label}µ"]
            if self.gusset_type:
                parts.append(f"Gusset: {self.gusset_type}")
            if self.gusset_length_inches:
                parts.append(f"Gusset Length: {self.gusset_length_inches}\"")
            if self.color:
                parts.append(f"Color: {self.color}")
            return " | ".join(parts)
        else:
            return f"{self.material} Sheet {self.width_inches}\" x {self.micron_label}µ"

class Stock(db.Model):
    __tablename__ = 'stock'
    
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    zone_code = db.Column(db.String(10), db.ForeignKey('zones.code'), nullable=False)
    quantity_pieces = db.Column(db.Float, nullable=False)
    quantity_kg = db.Column(db.Float, nullable=False)
    bundle_size = db.Column(db.Integer, nullable=True)
    date_received = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    item = db.relationship('Item', backref='stock_records')
    zone = db.relationship('Zone', backref='stock_records')

class Transaction(db.Model):
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    transaction_type = db.Column(db.String(10), nullable=False)  # 'IN', 'OUT', 'RETURN'
    item_type = db.Column(db.String(20), nullable=False)
    material = db.Column(db.String(20), nullable=False)
    width_inches = db.Column(db.Float, nullable=True)
    length_inches = db.Column(db.Float, nullable=True)
    micron_label = db.Column(db.String(20), nullable=False)
    is_printed = db.Column(db.Boolean)
    buyer_name = db.Column(db.String(100), nullable=True)
    zone_code = db.Column(db.String(10), nullable=True)
    quantity_pieces = db.Column(db.Float, nullable=False)
    quantity_kg = db.Column(db.Float, nullable=False)
    
    # Bag-specific fields
    gusset_inches = db.Column(db.Float, nullable=True)
    flap_inches = db.Column(db.Float, nullable=True)
    brand_name = db.Column(db.String(100), nullable=True)
    handle_type = db.Column(db.String(20), nullable=True)
    
    # Roll-specific fields
    gusset_type = db.Column(db.String(20), nullable=True)  # '1-side', '2-side'
    gusset_length_inches = db.Column(db.Float, nullable=True)
    color = db.Column(db.String(50), nullable=True)
    
    request_id = db.Column(db.Integer, db.ForeignKey('requests.id'), nullable=True)
    original_transaction_id = db.Column(db.Integer, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    executed_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', backref='transactions')

class Request(db.Model):
    __tablename__ = 'requests'
    
    id = db.Column(db.Integer, primary_key=True)
    requested_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    quantity_pieces_requested = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')  # 'pending', 'completed', 'cancelled'
    fulfilled_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    fulfilled_at = db.Column(db.DateTime, nullable=True)
    cancelled_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancel_reason = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    requester = db.relationship('User', foreign_keys=[requested_by], backref='raised_requests')
    fulfiller = db.relationship('User', foreign_keys=[fulfilled_by], backref='fulfilled_requests')
    canceller = db.relationship('User', foreign_keys=[cancelled_by], backref='cancelled_requests')
    item = db.relationship('Item', backref='requests')

class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    
    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(50), nullable=False)
    record_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(10), nullable=False)  # 'INSERT', 'UPDATE', 'DELETE'
    old_values = db.Column(db.Text, nullable=True)
    new_values = db.Column(db.Text, nullable=True)
    changed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='audit_logs')

class SecurityEvent(db.Model):
    __tablename__ = 'security_events'
    
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50), nullable=False)  # LOGIN_FAILED, LOGIN_SUCCESS, PERMISSION_DENIED, SUSPICIOUS_ACTIVITY
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    details = db.Column(db.Text, nullable=True)
    severity = db.Column(db.String(20), default='WARNING')  # INFO, WARNING, CRITICAL
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    
    user = db.relationship('User', backref='security_events')