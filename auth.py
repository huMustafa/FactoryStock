from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User
from datetime import datetime
from urllib.parse import urlparse, urljoin

auth_bp = Blueprint('auth', __name__)

def is_safe_url(target):
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    # If already logged in, redirect to dashboard
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        
        # Input validation
        if not username or not password:
            flash('Username and password are required', 'error')
            return render_template('login.html')
        
        # Rate limiting is handled in before_request
        client_ip = request.remote_addr or request.environ.get('HTTP_X_FORWARDED_FOR', 'unknown')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if not user.is_active:
                flash('Account is deactivated. Contact administrator.', 'error')
                return render_template('login.html')
            
            # Prevent session fixation
            session.clear()
            
            # Login the user
            login_user(user, remember=remember)
            session.permanent = True
            
            # Get the next URL from session (set by before_request)
            next_url = session.pop('next_url', None)
            
            # Validate next_url to prevent open redirect attacks
            if next_url and is_safe_url(next_url):
                return redirect(next_url)
            
            flash('Logged in successfully', 'success')
            return redirect(url_for('dashboard'))
        else:
            # Record failed attempt for rate limiting
            from main import record_login_attempt
            record_login_attempt(client_ip)
            flash('Invalid username or password', 'error')
    
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()  # Clear all session data
    flash('You have been logged out', 'info')
    return redirect(url_for('auth.login'))