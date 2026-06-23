from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=True)  # Optional field
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'store_keeper', 'supervisor', 'owner'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        # Require at least 8 characters
        if password and len(password) >= 8:
            self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
            return True
        return False
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

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
    
    @property
    def display_name(self):
        name = f"{self.material} {self.item_type.capitalize()}"
        if self.item_type in ['roll', 'sheet']:
            name += f" {self.width_inches}\""
        elif self.item_type == 'bag':
            name += f" {self.length_inches}\"x{self.width_inches}\""
        
        name += f" {self.micron_label}µ"
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
            return f"{self.material} Bag {self.length_inches}\" x {self.micron_label}mic"
        elif self.item_type == 'roll':
            return f"{self.material} Roll {self.width_inches}\" x {self.micron_label}mic"
        else:
            return f"{self.material} Sheet {self.width_inches}\" x {self.micron_label}mic"

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
    status = db.Column(db.String(20), default='pending')  # 'pending', 'completed'
    fulfilled_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    fulfilled_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    requester = db.relationship('User', foreign_keys=[requested_by], backref='raised_requests')
    fulfiller = db.relationship('User', foreign_keys=[fulfilled_by], backref='fulfilled_requests')
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