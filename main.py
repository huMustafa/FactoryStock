from flask import Flask, render_template, redirect, url_for, flash, request, session, g
from flask_login import LoginManager, current_user, login_required, logout_user
from flask_wtf import CSRFProtect
from dotenv import load_dotenv
import os
import time
from collections import defaultdict
from models import db, User, AuditLog
from auth import auth_bp
from stock import stock_bp
from requests import requests_bp
from settings import settings_bp
from functools import wraps

load_dotenv()

# In-memory rate limiter for login attempts
login_attempts = defaultdict(list)

def rate_limit_login(ip, max_attempts=5, window_seconds=300):
    """Rate limit login attempts per IP"""
    now = time.time()
    # Clean old attempts
    login_attempts[ip] = [t for t in login_attempts[ip] if now - t < window_seconds]
    
    if len(login_attempts[ip]) >= max_attempts:
        return False
    return True

def record_login_attempt(ip):
    """Record a login attempt"""
    login_attempts[ip].append(time.time())

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', os.urandom(32).hex())
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///factory_stock.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 minutes
    app.config['SESSION_COOKIE_SECURE'] = False  # Set to True if using HTTPS
    app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access to session
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Prevent CSRF
    app.config['WTF_CSRF_TIME_LIMIT'] = None  # CSRF tokens don't expire
    
    # Initialize extensions
    db.init_app(app)
    csrf = CSRFProtect()
    csrf.init_app(app)
    
    # Setup Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please sign in to access this page.'
    login_manager.login_message_category = 'error'
    login_manager.session_protection = 'strong'
    
    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None
    
    # ==========================================
    # GLOBAL SECURITY MIDDLEWARE
    # ==========================================
    @app.before_request
    def require_authentication():
        """Block ALL requests unless user is authenticated"""
        # List of public endpoints (only login page)
        public_endpoints = ['auth.login', 'static']
        
        # Get the endpoint name
        endpoint = request.endpoint
        
        # If endpoint is public, allow access
        if endpoint and endpoint in public_endpoints:
            return None
        
        # Rate limit login attempts
        if endpoint == 'auth.login' and request.method == 'POST':
            client_ip = request.remote_addr or request.environ.get('HTTP_X_FORWARDED_FOR', 'unknown')
            if not rate_limit_login(client_ip):
                flash('Too many login attempts. Please try again in 5 minutes.', 'error')
                return render_template('login.html'), 429
        
        # If user is NOT authenticated, redirect to login
        if not current_user.is_authenticated:
            # Store the original URL to redirect back after login
            session['next_url'] = request.url
            return redirect(url_for('auth.login'))
        
        # Verify user is still active
        if current_user.is_authenticated and not current_user.is_active:
            logout_user()
            session.clear()
            flash('Account deactivated. Contact administrator.', 'error')
            return redirect(url_for('auth.login'))
    
    # Add cache control to prevent browser caching of protected pages
    @app.after_request
    def add_security_headers(response):
        """Prevent caching of sensitive pages and add security headers"""
        if not request.path.startswith('/static'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0, private'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            response.headers['Vary'] = 'Cookie'
        
        # Security headers for all responses
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Content Security Policy
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
            "font-src 'self' https://cdnjs.cloudflare.com; "
            "img-src 'self' data:; "
            "connect-src 'self'"
        )
        
        return response
    
    # Audit logging for sensitive operations
    def log_audit(table_name, record_id, action, old_values=None, new_values=None):
        """Log audit trail for sensitive operations"""
        if current_user.is_authenticated:
            try:
                audit = AuditLog(
                    table_name=table_name,
                    record_id=record_id,
                    action=action,
                    old_values=str(old_values) if old_values else None,
                    new_values=str(new_values) if new_values else None,
                    changed_by=current_user.id
                )
                db.session.add(audit)
                db.session.commit()
            except Exception:
                db.session.rollback()
    
    app.log_audit = log_audit
    
    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(stock_bp, url_prefix='/stock')
    app.register_blueprint(requests_bp, url_prefix='/requests')
    app.register_blueprint(settings_bp, url_prefix='/settings')
    
    # Main dashboard route
    @app.route('/')
    def dashboard():
        # This route is protected by @app.before_request
        return redirect(url_for('stock.stock_directory'))
    
    # Custom date filter (GMT+5)
    @app.template_filter('format_date')
    def format_date_filter(date, fmt='%d/%m/%Y'):
        if date:
            from datetime import timedelta
            # Convert UTC to GMT+5
            local_date = date + timedelta(hours=5)
            return local_date.strftime(fmt)
        return ''
    
    # Create tables and default users
    with app.app_context():
        db.create_all()
        if not User.query.first():
            # Create Store Keeper
            sk = User(username='storekeeper', email='store@factory.com', role='store_keeper')
            sk.set_password('StoreKeeper@123')
            db.session.add(sk)
            
            # Create Owner
            owner = User(username='owner', email='owner@factory.com', role='owner')
            owner.set_password('Owner@123456')
            db.session.add(owner)
            
            # Create Sample Supervisor
            sup = User(username='ahmed', email='ahmed@factory.com', role='supervisor')
            sup.set_password('Ahmed@123456')
            db.session.add(sup)
            
            # Create default zones
            from models import Zone
            if not Zone.query.get('A-01'):
                db.session.add(Zone(code='A-01', description='Rolls Area'))
            
            db.session.commit()
            print("[OK] Default users created! CHANGE PASSWORDS IMMEDIATELY!")
    
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5001, debug=False)