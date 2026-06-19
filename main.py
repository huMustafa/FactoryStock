from flask import Flask, render_template, redirect, url_for, flash, request, session, g
from flask_login import LoginManager, current_user, login_required, logout_user
from flask_wtf import CSRFProtect
from dotenv import load_dotenv
import os
from models import db, User
from auth import auth_bp
from stock import stock_bp
from requests import requests_bp
from settings import settings_bp
from functools import wraps

load_dotenv()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', os.urandom(32).hex())
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///factory_stock.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 minutes
    app.config['SESSION_COOKIE_SECURE'] = False  # Set to True if using HTTPS
    app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access to session
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Prevent CSRF
    
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
        """Prevent caching of sensitive pages"""
        if not request.path.startswith('/static'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0, private'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            response.headers['Vary'] = 'Cookie'
        return response
    
    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(stock_bp, url_prefix='/stock')
    app.register_blueprint(requests_bp, url_prefix='/requests')
    app.register_blueprint(settings_bp, url_prefix='/settings')
    
    # Main dashboard route
    @app.route('/')
    def dashboard():
        # This route is protected by @app.before_request
        return render_template('dashboard.html')
    
    # Custom date filter
    @app.template_filter('format_date')
    def format_date_filter(date, fmt='%d/%m/%Y'):
        if date:
            return date.strftime(fmt)
        return ''
    
    # Create tables and default users
    with app.app_context():
        db.create_all()
        if not User.query.first():
            # Create Store Keeper
            sk = User(username='storekeeper', email='store@factory.com', role='store_keeper')
            sk.set_password('store123')
            db.session.add(sk)
            
            # Create Owner
            owner = User(username='owner', email='owner@factory.com', role='owner')
            owner.set_password('owner123')
            db.session.add(owner)
            
            # Create Sample Supervisor
            sup = User(username='ahmed', email='ahmed@factory.com', role='supervisor')
            sup.set_password('ahmed123')
            db.session.add(sup)
            
            # Create default zones
            from models import Zone
            if not Zone.query.get('A-01'):
                db.session.add(Zone(code='A-01', description='Rolls Area'))
            
            db.session.commit()
            print("✅ Default users created! CHANGE PASSWORDS IMMEDIATELY!")
    
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5001, debug=False)