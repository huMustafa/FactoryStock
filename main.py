from flask import Flask, render_template, redirect, url_for, flash, request, session, g
from flask_login import LoginManager, current_user, login_required, logout_user
from flask_wtf import CSRFProtect
from dotenv import load_dotenv
import os
import time
from collections import defaultdict
import redis
from models import db, User, AuditLog
from auth import auth_bp
from stock import stock_bp
from requests import requests_bp
from settings import settings_bp
from functools import wraps

load_dotenv()

# Redis client for rate limiting (with graceful fallback)
redis_client = None
try:
    redis_client = redis.Redis(
        host=os.getenv('REDIS_HOST', 'localhost'),
        port=int(os.getenv('REDIS_PORT', 6379)),
        db=int(os.getenv('REDIS_DB', 0)),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2
    )
    redis_client.ping()
except Exception:
    redis_client = None

# In-memory fallback for rate limiting
login_attempts = defaultdict(list)

def rate_limit_login(ip, max_attempts=5, window_seconds=300):
    """Rate limit login attempts per IP using Redis with in-memory fallback"""
    now = time.time()
    window_start = now - window_seconds
    key = f"login_attempts:{ip}"
    
    if redis_client:
        try:
            # Remove expired entries
            redis_client.zremrangebyscore(key, 0, window_start)
            # Count current attempts
            current_attempts = redis_client.zcard(key)
            
            if current_attempts >= max_attempts:
                return False
            
            # Add current attempt
            redis_client.zadd(key, {str(now): now})
            redis_client.expire(key, window_seconds + 60)
            return True
        except Exception:
            pass  # Fall back to in-memory
    
    # In-memory fallback
    login_attempts[ip] = [t for t in login_attempts[ip] if t > window_start]
    if len(login_attempts[ip]) >= max_attempts:
        return False
    login_attempts[ip].append(now)
    return True

def record_login_attempt(ip):
    """Record a login attempt - handled by rate_limit_login"""
    pass  # rate_limit_login already records attempts

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
        
        # Content Security Policy - allow Alpine.js inline handlers
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
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
    
    # Security event logging
    def log_security_event(event_type, details=None, severity='WARNING'):
        """Log security-related events"""
        try:
            from models import SecurityEvent
            if not hasattr(db.Model, '_decl_class_registry') or 'SecurityEvent' not in db.Model._decl_class_registry:
                # SecurityEvent model may not exist yet, skip
                pass
            else:
                user_id = current_user.id if current_user.is_authenticated else None
                ip = request.remote_addr or request.environ.get('HTTP_X_FORWARDED_FOR', 'unknown')
                ua = request.headers.get('User-Agent', '')[:500]
                
                event = SecurityEvent(
                    event_type=event_type,
                    user_id=user_id,
                    ip_address=ip,
                    user_agent=ua,
                    details=details,
                    severity=severity
                )
                db.session.add(event)
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.warning(f"Security event logging failed: {e}")
    
    app.log_audit = log_audit
    app.log_security = log_security_event
    
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