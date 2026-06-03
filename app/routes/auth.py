from flask import Blueprint, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from app.models import User
from app import db
import os

bp = Blueprint('auth', __name__)
oauth = OAuth()

ALLOWED_EMAILS = {
    "alex@thedeliagroup.com", "alia@thedeliagroup.com", "amanda@thedeliagroup.com",
    "austin@thedeliagroup.com", "brock@tdgcommercialre.com", "bryan@thedeliagroup.com",
    "casey@thedeliagroup.com", "chaise@tdgcommercialre.com", "christilles@thedeliagroup.com",
    "jair@thedeliagroup.com", "jimmy@thedeliagroup.com", "joanne@thedeliagroup.com",
    "joe.c@thedeliagroup.com", "joe@poweredbyinfinity.com", "johnathon@thedeliagroup.com",
    "jovona@thedeliagroup.com", "julie@poweredbyinfinity.com", "keith@thedeliagroup.com",
    "kim@thedeliagroup.com", "kristin@thedeliagroup.com", "laith@thedeliagroup.com",
    "manual@thedeliagroup.com", "martin@thedeliagroup.com", "megan@thedeliagroup.com",
    "parker@thedeliagroup.com", "renee@thedeliagroup.com", "ryan@tothteamnetwork.com",
    "samar@thedeliagroup.com", "sara@thedeliagroup.com", "sarah@thedeliagroup.com",
    "shariful@thedeliagroup.com", "shayne@poweredbyinfinity.com", "tahlia@thedeliagroup.com",
    "team@poweredbyinfinity.com",
    # Admin / staff
    "renee@poweredbyinfinity.com",
}

def init_oauth(app):
    oauth.init_app(app)
    oauth.register(
        name='google',
        client_id=os.environ.get('GOOGLE_CLIENT_ID'),
        client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

@bp.route('/login', methods=['GET', 'POST'])
def login():
    from flask import render_template
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))
    # Legacy username/password (kept for emergency admin access)
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=True)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.home'))
        flash('Invalid username or password.', 'danger')
    return render_template('auth/login.html')

@bp.route('/login/google')
def login_google():
    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@bp.route('/login/google/callback')
def google_callback():
    token = oauth.google.authorize_access_token()
    userinfo = token.get('userinfo') or oauth.google.userinfo()
    email = (userinfo.get('email') or '').lower().strip()

    if email not in ALLOWED_EMAILS:
        flash(f'Access denied. {email} is not authorized for TDG Command Center.', 'danger')
        return redirect(url_for('auth.login'))

    # Find or auto-create user record by email
    user = User.query.filter_by(email=email).first()
    if not user:
        # Auto-provision: strip domain to make username
        username = email.split('@')[0]
        # Make username unique
        base = username
        i = 2
        while User.query.filter_by(username=username).first():
            username = f"{base}{i}"
            i += 1
        user = User(username=username, email=email, role='staff', is_active=True)
        user.set_password(os.urandom(32).hex())  # random unusable password
        db.session.add(user)
        db.session.commit()

    login_user(user, remember=True)
    next_page = request.args.get('next') or session.pop('next', None)
    return redirect(next_page or url_for('main.home'))

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
