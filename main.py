from flask import Flask, render_template, redirect, url_for, flash, request, session, g
from flask_login import LoginManager, current_user, login_required, logout_user
from flask_wtf import CSRFProtect
from dotenv import load_dotenv
import os
import time
import threading
from collections import defaultdict
import redis
from models import db, User, AuditLog
from auth import auth_bp
from stock import stock_bp
from requests import requests_bp
from settings import settings_bp
from functools import wraps
from flask_migrate import Migrate

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

# In-memory fallback for rate limiting (thread-safe)
login_attempts = defaultdict(list)
login_attempts_lock = threading.Lock()

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
    
    # In-memory fallback (thread-safe)
    with login_attempts_lock:
        login_attempts[ip] = [t for t in login_attempts[ip] if t > window_start]
        if len(login_attempts[ip]) >= max_attempts:
            return False
        login_attempts[ip].append(now)
    return True

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', os.urandom(32).hex())
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///factory_stock.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 minutes
    app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access to session
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Prevent CSRF
    app.config['WTF_CSRF_TIME_LIMIT'] = None  # CSRF tokens don't expire
    
    # Initialize extensions
    db.init_app(app)
    migrate = Migrate(app, db)
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
    
    # Context processor to inject pending requests count
    @app.context_processor
    def inject_pending_requests():
        if current_user.is_authenticated and current_user.role in ['owner', 'store_keeper']:
            from models import Request
            pending_count = Request.query.filter_by(status='pending').count()
            return {'pending_requests_count': pending_count}
        return {'pending_requests_count': 0}
    
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
    
    # CLI command to create initial admin user
    @app.cli.command('create-admin')
    def create_admin():
        """Create initial admin (owner) user."""
        import click
        from models import User
        
        if User.query.filter_by(role='owner').first():
            click.echo('An owner user already exists.')
            return
        
        username = click.prompt('Admin username')
        email = click.prompt('Admin email', default='', show_default=False)
        password = click.prompt('Admin password', hide_input=True, confirmation_prompt=True)
        
        # Validate password
        import re
        if len(password) < 8:
            click.echo('Password must be at least 8 characters')
            return
        if not re.search(r'[A-Z]', password):
            click.echo('Password must contain at least one uppercase letter')
            return
        if not re.search(r'[a-z]', password):
            click.echo('Password must contain at least one lowercase letter')
            return
        if not re.search(r'\d', password):
            click.echo('Password must contain at least one digit')
            return
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            click.echo('Password must contain at least one special character')
            return
        
        user = User(username=username, email=email if email else None, role='owner')
        if user.set_password(password):
            db.session.add(user)
            db.session.flush()
            user.add_password_history()
            db.session.commit()
            click.echo(f'Admin user "{username}" created successfully.')
        else:
            click.echo('Failed to create user - invalid password')
            db.session.rollback()

    # Create tables
    with app.app_context():
        db.create_all()
        # Note: Default users are NOT created automatically.
        # Run 'flask create-admin' or use the settings page to create the initial admin user.
    
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5001, debug=False)