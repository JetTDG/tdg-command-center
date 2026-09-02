
from flask import Flask, abort, redirect, request, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_socketio import SocketIO
from dotenv import load_dotenv
import os

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
socketio = SocketIO()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///tdg_command_center.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Trust Railway's HTTPS proxy so redirect_uri uses https://
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    # async_mode=eventlet — matches gunicorn worker class
    socketio.init_app(app, async_mode='eventlet', cors_allowed_origins='*')
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access Jet Center.'

    from app.models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.routes import auth, main, sms_consent
    from app.routes import gl
    from app.routes import docs
    auth.init_oauth(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(gl.bp)
    app.register_blueprint(docs.bp)
    app.register_blueprint(sms_consent.bp)

    # Agents are self-service users: their own scorecard is their complete
    # authenticated Jet Center surface. Enforce this server-side instead of
    # relying on navigation visibility, which previously left company pages
    # and mutation routes directly reachable by URL.
    agent_scorecard_endpoints = {
        'main.scorecard',
        'main.scorecard_drill',
        'main.scorecard_zillow_detail',
        'main.scorecard_deal_breakdown',
        'main.business_plan_form_for',
        'auth.logout',
        'main.health',
        'static',
    }

    @app.before_request
    def enforce_agent_scorecard_only():
        if not current_user.is_authenticated or current_user.role != 'agent':
            return None
        if not current_user.agent_id:
            abort(403)
        if request.endpoint in agent_scorecard_endpoints:
            return None
        if request.method in {'GET', 'HEAD'}:
            return redirect(url_for('main.scorecard', agent_id=current_user.agent_id))
        abort(403)

    # ── Timezone filter ──────────────────────────────────────────────────────
    # All DB timestamps (doc_envelopes.sent_at/completed_at/last_synced_at,
    # DocuSign API times, etc.) are stored as naive UTC. Templates must never
    # strftime them directly — always convert to US/Eastern for display
    # (Renee is ET-only). Fixed June 30 2026 — Document Pipeline was showing
    # raw UTC timestamps unlabeled, which read as if they were local time.
    from zoneinfo import ZoneInfo
    _UTC = ZoneInfo('UTC')
    _ET  = ZoneInfo('America/New_York')

    def to_et(dt):
        """Convert a naive-UTC or aware datetime to US/Eastern. Returns None if dt is falsy."""
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
        return dt.astimezone(_ET)

    def fmt_et(value):
        converted = to_et(value)
        return converted.strftime('%b %-d, %Y %-I:%M %p') if converted else '—'

    app.jinja_env.filters['to_et'] = to_et
    app.jinja_env.filters['fmt_et'] = fmt_et
    app.jinja_env.filters['show_value'] = lambda value: '—' if value is None else value

    # Ensure all tables exist for normal app startup. Migration/inspection tools
    # can explicitly skip this so Alembic remains the only schema writer.
    if os.environ.get('SKIP_DB_CREATE_ALL') != '1':
        with app.app_context():
            db.create_all()

    return app
