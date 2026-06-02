
from app import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), default='staff')  # admin, staff
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


class Agent(db.Model):
    __tablename__ = 'agents'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(50))  # Lead Agent, Both L/B
    agent_type = db.Column(db.String(20))  # Team, Individual
    status = db.Column(db.String(20), default='Active')  # Active, Inactive
    split_pct = db.Column(db.Float, default=0.0)
    cap_amount = db.Column(db.Float, default=0.0)
    start_month = db.Column(db.Integer)
    email = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    transactions = db.relationship('Transaction', backref='agent', lazy='dynamic')
    lead_gen_logs = db.relationship('LeadGenLog', backref='agent', lazy='dynamic')
    business_plans = db.relationship('BusinessPlan', backref='agent', lazy='dynamic')

    def __repr__(self):
        return f'<Agent {self.name}>'


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=False)
    transaction_type = db.Column(db.String(20))  # Listing, Buyer, Other, Referral, Lease, Commercial
    status = db.Column(db.String(30))  # Active, Pending, Closed, Pipeline, Pre-Signed, Coming Soon, x-Cancelled, y-Sale Failed, z-Expired, Temp Off Market
    lead_type = db.Column(db.String(30))  # Team, Agent
    address = db.Column(db.String(200))
    client_name = db.Column(db.String(100))
    sale_price = db.Column(db.Float, default=0.0)
    commission_pct = db.Column(db.Float, default=0.0)
    gci = db.Column(db.Float, default=0.0)
    net_income = db.Column(db.Float, default=0.0)
    signed_date = db.Column(db.Date)
    close_date = db.Column(db.Date)
    contract_date = db.Column(db.Date)
    year = db.Column(db.Integer)
    month = db.Column(db.Integer)
    notes = db.Column(db.Text)
    fub_id = db.Column(db.String(50))
    docusign_id = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<Transaction {self.address} - {self.status}>'


class LeadGenLog(db.Model):
    __tablename__ = 'lead_gen_log'
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=False)
    log_date = db.Column(db.Date, nullable=False)
    hours = db.Column(db.Float, default=0.0)
    dials = db.Column(db.Integer, default=0)
    contacts = db.Column(db.Integer, default=0)
    nurtures = db.Column(db.Integer, default=0)
    listing_appts_set = db.Column(db.Integer, default=0)
    listing_appts_held = db.Column(db.Integer, default=0)
    listings_signed = db.Column(db.Integer, default=0)
    buyer_appts_set = db.Column(db.Integer, default=0)
    buyer_appts_held = db.Column(db.Integer, default=0)
    buyers_signed = db.Column(db.Integer, default=0)
    written_offers = db.Column(db.Integer, default=0)
    showings = db.Column(db.Integer, default=0)
    open_houses = db.Column(db.Integer, default=0)
    lead_source = db.Column(db.String(50))  # FSBOs, Expireds, Sphere, etc.
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<LeadGenLog {self.agent.name} {self.log_date}>'


class BusinessPlan(db.Model):
    __tablename__ = 'business_plan'
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    listing_unit_goal = db.Column(db.Integer, default=0)
    buyer_unit_goal = db.Column(db.Integer, default=0)
    total_unit_goal = db.Column(db.Integer, default=0)
    gci_goal = db.Column(db.Float, default=0.0)
    avg_sale_price = db.Column(db.Float, default=0.0)
    listing_comm_pct = db.Column(db.Float, default=0.03)
    buyer_comm_pct = db.Column(db.Float, default=0.03)
    split_pct = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)
    submitted_by = db.Column(db.String(100))  # agent self-submit name
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<BusinessPlan {self.agent.name} {self.year}>'


class Pipeline(db.Model):
    __tablename__ = 'pipeline'
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=True)
    lead_name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.String(200))
    lead_date = db.Column(db.Date)
    timeframe = db.Column(db.String(20))  # <10 Days, 10-30 Days, 30-60 Days, 60+ Days
    price_point = db.Column(db.Float)
    source = db.Column(db.String(50))
    lead_type = db.Column(db.String(20))  # Buyer, Seller, Both
    status = db.Column(db.String(30))
    notes = db.Column(db.Text)
    appt_set_date = db.Column(db.Date)
    fub_id = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
