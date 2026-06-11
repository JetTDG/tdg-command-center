
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

    transactions = db.relationship('Transaction', foreign_keys='Transaction.agent_id', backref='agent', lazy='dynamic')
    lead_gen_logs = db.relationship('LeadGenLog', backref='agent', lazy='dynamic')
    business_plans = db.relationship('BusinessPlan', backref='agent', lazy='dynamic')

    def __repr__(self):
        return f'<Agent {self.name}>'


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=True)  # nullable for import
    transaction_type = db.Column(db.String(50))   # Listing, Buyer, Other, Referral, Lease, CRE Listing, CRE Buyer, CRE Landlord Rep, CRE Tenant Rep, CRE Business Only
    status = db.Column(db.String(30))             # Active, Pending, Closed, etc.
    sub_status = db.Column(db.String(50))
    lead_type = db.Column(db.String(30))          # Team, Agent
    lead_source = db.Column(db.String(100))       # SOI, Zillow, Veterans United, etc.

    # Property
    address = db.Column(db.String(300))
    client_name = db.Column(db.String(200))
    location = db.Column(db.String(100))          # Rochester, etc.

    # Dates
    signed_date = db.Column(db.Date)
    mls_live_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date)
    under_contract_date = db.Column(db.Date)
    projected_close_date = db.Column(db.Date)
    close_date = db.Column(db.Date)

    # Financials
    list_price = db.Column(db.Float)
    adj_list_price = db.Column(db.Float)     # adjusted/reduced list price
    sale_price = db.Column(db.Float)
    commission_pct = db.Column(db.Float)
    gci = db.Column(db.Float, default=0.0)
    bonus = db.Column(db.Float)
    transaction_fee = db.Column(db.Float)
    broker_split = db.Column(db.Float)
    franchise_split = db.Column(db.Float)
    referral_fee = db.Column(db.Float)
    net_income = db.Column(db.Float)
    taxes = db.Column(db.Float)
    net_after_taxes = db.Column(db.Float)

    # Agent splits
    primary_agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=True)
    primary_agent_name = db.Column(db.String(100))
    primary_agent_pct = db.Column(db.Float)
    primary_agent_gci = db.Column(db.Float)
    secondary_agent_name = db.Column(db.String(100))
    secondary_agent_pct = db.Column(db.Float)
    secondary_agent_gci = db.Column(db.Float)
    # Team members 3 & 4
    member3_name = db.Column(db.String(100))
    member3_pct = db.Column(db.Float)
    member3_gci = db.Column(db.Float)
    member4_name = db.Column(db.String(100))
    member4_pct = db.Column(db.Float)
    member4_gci = db.Column(db.Float)

    # Additional financials (CTE parity)
    units = db.Column(db.Float)             # number of units in the deal
    eo_fee = db.Column(db.Float)            # E&O fee
    donation = db.Column(db.Float)          # donation deduction
    other_fee = db.Column(db.Float)         # miscellaneous fee
    old_list_price = db.Column(db.Float)    # price before reduction

    # Extra dates (CTE parity)
    list_date = db.Column(db.Date)          # when listing originally went live

    # Status/flags
    paid = db.Column(db.Boolean, default=False)  # Paid?
    link_to_file = db.Column(db.String(500))     # ∞ link to file

    # Vendors
    mortgage_company = db.Column(db.String(100))
    title_company = db.Column(db.String(100))
    admin_name = db.Column(db.String(100))
    inspection_date = db.Column(db.Date)
    appraisal_date = db.Column(db.Date)

    # Meta
    year = db.Column(db.Integer)
    month = db.Column(db.Integer)
    notes = db.Column(db.Text)
    amt_paid = db.Column(db.Float)     # amount paid to agent
    archived = db.Column(db.Boolean, default=False)  # True = old historical, hidden from My Business
    division = db.Column(db.String(50))               # 'Commercial' or 'Residential' — stored in DB
    fub_id = db.Column(db.String(50))
    docusign_id = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Computed Properties (CTE formula parity) ─────────────────────────────

    @property
    def dom(self):
        """Days on Market: today - mls_live_date only.
        No MLS live date = not yet on market, so no DOM."""
        from datetime import date
        if not self.mls_live_date:
            return None
        end = self.close_date or date.today()
        return (end - self.mls_live_date).days

    @property
    def dsc(self):
        """Days Since Close: close_date → today."""
        from datetime import date
        if not self.close_date:
            return None
        return (date.today() - self.close_date).days

    @property
    def exp_in(self):
        """Days until expiry: today → expiry_date."""
        from datetime import date
        if not self.expiry_date:
            return None
        return (self.expiry_date - date.today()).days

    @property
    def up_closing(self):
        """Days until projected close: today → projected_close_date."""
        from datetime import date
        if not self.projected_close_date:
            return None
        return (self.projected_close_date - date.today()).days

    @property
    def company_dollar(self):
        """CTE 'Team TDG' formula:
        GCI + Bonus + TxFee − Referral − all agent GCIs
        (Broker Split, Franchise, E&O, Donation, Other are NOT deducted here — they come off in 1099)
        """
        if not self.gci:
            return None
        deductions = sum(filter(None, [
            self.primary_agent_gci,
            self.secondary_agent_gci,
            self.member3_gci,
            self.member4_gci,
            self.referral_fee,
        ]))
        additions = sum(filter(None, [
            self.bonus,
            self.transaction_fee,
        ]))
        return round(self.gci + additions - deductions, 2)

    @property
    def income_1099(self):
        """CTE '1099 Income' formula:
        Company Dollar − BrokerSplit − FranchiseSplit − E&O − Donation − Other
        """
        cd = self.company_dollar
        if cd is None:
            return None
        deductions = sum(filter(None, [
            self.broker_split,
            self.franchise_split,
            self.eo_fee,
            self.donation,
            self.other_fee,
        ]))
        return round(cd - deductions, 2)

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
    untyped_appts = db.Column(db.Integer, default=0)
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


class AuditLog(db.Model):
    """Tracks every change made to transactions — who changed what, when, from→to."""
    __tablename__ = 'audit_log'
    id           = db.Column(db.Integer, primary_key=True)
    table_name   = db.Column(db.String(50), nullable=False, default='transactions')
    record_id    = db.Column(db.Integer, nullable=False)   # transaction id
    field_name   = db.Column(db.String(100), nullable=False)
    old_value    = db.Column(db.Text)
    new_value    = db.Column(db.Text)
    changed_by   = db.Column(db.String(120))               # email / username
    changed_at   = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    note         = db.Column(db.String(200))               # optional context

    def __repr__(self):
        return f'<AuditLog {self.table_name}#{self.record_id} {self.field_name}>'


class GLScan(db.Model):
    """Tracks every QR scan + form submission from Commercial Golden Letter landing pages."""
    __tablename__ = 'gl_scans'
    id           = db.Column(db.Integer, primary_key=True)
    slug         = db.Column(db.String(80), nullable=False, index=True)   # e.g. "fraser-industrial"
    city         = db.Column(db.String(80))
    vertical     = db.Column(db.String(80))
    event_type   = db.Column(db.String(20), nullable=False)               # "scan" | "sms_tap" | "form_submit"
    name         = db.Column(db.String(120))
    phone        = db.Column(db.String(30))
    address      = db.Column(db.String(200))
    fub_id       = db.Column(db.String(50))                               # FUB person id after push
    fub_status   = db.Column(db.String(30))                               # "created" | "updated" | "error" | None
    ip           = db.Column(db.String(60))
    user_agent   = db.Column(db.String(300))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f'<GLScan {self.slug} {self.event_type} {self.created_at}>'


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
