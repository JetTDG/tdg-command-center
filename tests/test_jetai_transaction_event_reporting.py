"""Offline source contract and security tests for the only approved dated event lane."""
import hashlib
import hmac
import json
import time
from datetime import date, timedelta

import pytest
from flask import Flask
from app import db
from app.models import Agent, Transaction, User
from app.routes import transaction_event_reporting as source

KEY = 'synthetic-test-key-' * 3
TENANT = 'synthetic-tenant'

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('JETAI_REPORT_SERVICE_KEY', KEY)
    monkeypatch.setenv('JETAI_TDG_ORG_PUBLIC_ID', TENANT)
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path / "events.sqlite"}')
    db.init_app(app)
    app.register_blueprint(source.bp)
    with app.app_context():
        db.create_all()
        agent = Agent(name='Linked Agent', email='agent@example.test', status='Active')
        db.session.add(agent); db.session.flush()
        today = source.datetime.now(source._ET).date()
        common = dict(transaction_type='Listing', status='Closed', archived=False,
                      address='10 Sample St', agent_id=agent.id, sale_price=100000,
                      primary_agent_name='Primary', secondary_agent_name='Secondary',
                      member3_name='Third', member4_name='Fourth')
        db.session.add_all([
            User(username='agent', email='agent@example.test', role='agent', is_active=True),
            User(username='jet', email='jet.tdg3@gmail.com', role='agent', is_active=True),
            Transaction(id=10, close_date=today, signed_date=today-timedelta(days=1), **common),
            Transaction(id=11, close_date=today-timedelta(days=1), **{**common, 'status':'Pending'}),
            Transaction(id=12, close_date=today, **{**common, 'archived':True}),
            Transaction(id=13, close_date=today, is_import_duplicate=True, **common),
            Transaction(id=14, close_date=today-timedelta(days=2), **common),
        ])
        db.session.commit()
        yield app.test_client(), today
        db.session.remove(); db.drop_all()

def send(client, **overrides):
    import secrets
    data = dict(version=1, tenant=TENANT, email='jet.tdg3@gmail.com',
                metric='transaction_event_count', event='closed', days=2,
                timestamp=int(time.time()), nonce=secrets.token_hex(16))
    data.update(overrides)
    raw = json.dumps(data).encode()
    signature = 'sha256=' + hmac.new(KEY.encode(), raw, hashlib.sha256).hexdigest()
    return client.post('/api/jetai/reports/transaction-events', data=raw,
                       headers={'Content-Type':'application/json', 'X-JetAI-Signature':signature})

def test_count_is_independent_of_status_and_excludes_archived_duplicate(client):
    browser, today = client
    result = send(browser)
    assert result.status_code == 200
    assert result.json == dict(count=2, event='closed', days=2,
        window_start=(today-timedelta(days=1)).isoformat(),
        window_end=(today+timedelta(days=1)).isoformat(),
        as_of=result.json['as_of'], source='Jet Center',
        definition='Non-archived, non-duplicate transactions by event date')
    assert result.headers['Cache-Control'] == 'no-store'

def test_details_are_capped_projected_and_have_recorded_roles(client):
    browser, today = client
    result = send(browser, metric='transaction_event_details', limit=1,
                  window_start=(today-timedelta(days=1)).isoformat(),
                  window_end=(today+timedelta(days=1)).isoformat())
    assert result.status_code == 200
    assert result.json['count'] == 2 and result.json['truncated'] is True
    assert len(result.json['rows']) == 1 and result.json['limit'] == 1
    row = result.json['rows'][0]
    assert set(row) == {'id','address','agent','transaction_type','current_status',
                        'close_date','sale_price','projected_close_date','participant_roles'}
    assert row['participant_roles'] == {'primary':'Primary','secondary':'Secondary',
                                        'member3':'Third','member4':'Fourth'}
    assert row['id'] == 10 and row['agent'] == 'Linked Agent'

def test_bad_signature_tenant_and_non_jet_denied(client):
    browser, _ = client
    assert send(browser, email='agent@example.test').status_code == 403
    assert send(browser, email='jet.tdg3@gmail.com.evil').status_code == 403
    assert send(browser, tenant='wrong').status_code == 403
    assert send(browser, timestamp=0).status_code == 401
    assert send(browser, nonce='invalid').status_code == 401
    raw = b'{}'
    assert browser.post('/api/jetai/reports/transaction-events', data=raw,
                        headers={'X-JetAI-Signature':'sha256='+'0'*64}).status_code == 401

def test_replay_and_bounded_date_contract(client):
    browser, today = client
    first = send(browser, nonce='b'*32)
    assert first.status_code == 200
    assert send(browser, nonce='b'*32).status_code == 401
    for fields in [dict(days=366), dict(days=True), dict(metric='unexpected'),
                   dict(metric='transaction_event_details', limit=21,
                        window_start=today.isoformat(), window_end=(today+timedelta(days=1)).isoformat()),
                   dict(metric='transaction_event_details', limit=1,
                        window_start=today.isoformat(), window_end=today.isoformat())]:
        assert send(browser, nonce='c'*32, **fields).status_code == 400

def test_real_app_registers_event_route(monkeypatch, tmp_path):
    from app import create_app
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "real-app.sqlite"}')
    app = create_app()
    assert any(rule.rule == '/api/jetai/reports/transaction-events' and
               'POST' in rule.methods for rule in app.url_map.iter_rules())

def test_exact_signed_jet_grant_does_not_require_center_login_row(client):
    browser, _ = client
    with browser.application.app_context():
        user = User.query.filter_by(email='jet.tdg3@gmail.com').one()
        db.session.delete(user)
        db.session.commit()
    assert send(browser, nonce='d'*32).status_code == 200
    assert send(browser, nonce='e'*32, email='agent@example.test').status_code == 403
