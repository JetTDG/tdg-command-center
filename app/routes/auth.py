from flask import Blueprint, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from app.models import User, Agent
from app import db
import os

bp = Blueprint('auth', __name__)
oauth = OAuth()

# ── Role definitions ──────────────────────────────────────────────────────────
# These accounts get role='admin' — they see everything, manage all agents, access Users page
ADMIN_EMAILS = {
    "renee@thedeliagroup.com",
    "renee@poweredbyinfinity.com",   # backup login
    "joseph@thedeliagroup.com",      # Joseph DElia
    "joe@poweredbyinfinity.com",     # Joseph DElia alternate
    "kristin@poweredbyinfinity.com", # Kristin Ebert
    "julie@poweredbyinfinity.com",   # Julie Kelsey
    "team@poweredbyinfinity.com",    # Team@PoweredByInfinity.com
    "jenny@thedeliagroup.com",       # Jenny O'Neal — Executive Assistant
    "klrw928@kw.com",                # Emily Colvin
    "jet.tdg3@gmail.com",            # Jet (Hermes agent) — QA/verification login, admin tier
    "mattmartinec@kw.com",            # Matt Martinec — office team leader / recruit demonstrations
}

# All TDG Google accounts allowed to log in (agents get role='agent')
ALLOWED_EMAILS = ADMIN_EMAILS | {
    "alex@thedeliagroup.com",
    "lexy@thedeliagroup.com",
    "alia@thedeliagroup.com",
    "austin@thedeliagroup.com",
    "brock@tdgcommercialre.com",
    "bryan@thedeliagroup.com",
    "casey@thedeliagroup.com",
    "chaise@tdgcommercialre.com",
    "christilles@thedeliagroup.com",
    "jair@thedeliagroup.com",
    "jimmy@thedeliagroup.com",
    "joe.c@thedeliagroup.com",
    "johnathon@thedeliagroup.com",
    "jovona@thedeliagroup.com",
    "keith@thedeliagroup.com",
    "kim@thedeliagroup.com",
    "kristin@thedeliagroup.com",     # Kristin Ebert alternate
    "laith@thedeliagroup.com",
    "manual@thedeliagroup.com",
    "maeson@thedeliagroup.com",
    "martin@thedeliagroup.com",
    "megan@thedeliagroup.com",
    "parker@thedeliagroup.com",
    "ryan@tothteamnetwork.com",
    "samar@thedeliagroup.com",
    "sara@thedeliagroup.com",
    "sarah@thedeliagroup.com",
    "shariful@thedeliagroup.com",
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
        flash(f'Access denied. {email} is not authorized for Jet Center.', 'danger')
        return redirect(url_for('auth.login'))

    # Determine role
    role = 'admin' if email in ADMIN_EMAILS else 'agent'

    # Find or auto-create user record by email
    user = User.query.filter(User.email.ilike(email)).first()
    if not user:
        # Auto-provision
        username = email.split('@')[0]
        base = username
        i = 2
        while User.query.filter_by(username=username).first():
            username = f"{base}{i}"
            i += 1
        user = User(username=username, email=email, role=role, is_active=True)
        user.set_password(os.urandom(32).hex())  # random unusable password
        db.session.add(user)
        db.session.flush()  # get user.id before linking agent

    else:
        # Update role in case it changed (e.g., agent promoted to admin)
        user.role = role

    # Link to agent record by matching email
    if not user.agent_id:
        agent = Agent.query.filter(Agent.email.ilike(email)).first()
        if agent:
            user.agent_id = agent.id

    db.session.commit()
    login_user(user, remember=True)

    # Role-based redirect: agents go to their own scorecard, admins go home
    next_page = request.args.get('next') or session.pop('next', None)
    if next_page:
        return redirect(next_page)
    if role == 'agent' and user.agent_id:
        return redirect(url_for('main.scorecard', agent_id=user.agent_id))
    return redirect(url_for('main.home'))


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
