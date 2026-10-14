"""Incoming Offers signed Jet Center source contract, isolated SQLite."""
import hashlib
import hmac
import json
import time

import pytest
from flask import Flask
from app import db
from app.models import Agent, Transaction, User
from app.routes import incoming_offer_reporting as source

KEY = 'k' * 40
TENANT = 'org_tdg_synthetic'

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('JETAI_REPORT_SERVICE_KEY', KEY)
    monkeypatch.setenv('JETAI_TDG_ORG_PUBLIC_ID', TENANT)
    monkeypatch.setattr(source, 'ADMIN_EMAILS', {'admin@example.test'})
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path / "cc.sqlite"}')
    db.init_app(app)
    app.register_blueprint(source.bp)
    with app.app_context():
        db.create_all()
        owner = Agent(name='Listing Owner', email='agent@example.test', status='Active')
        other = Agent(name='Other', email='other@example.test', status='Active')
        db.session.add_all([owner, other]); db.session.flush()
        db.session.add_all([
            User(username='admin', email='admin@example.test', role='admin', is_active=True),
            User(username='agent', email='agent@example.test', role='agent', agent_id=owner.id, is_active=True),
            User(username='inactive', email='inactive@example.test', role='admin', is_active=False),
            Transaction(id=15546, transaction_type='Listing', status='Active', archived=False,
                        address='5904 Niles Dr, #5906, Troy, MI 48098', agent_id=owner.id),
            Transaction(transaction_type='Listing', status='Pending', archived=False,
                        address='456 Else Road', agent_id=other.id),
            Transaction(transaction_type='Listing', status='Active', archived=False,
                        address='No Owner Road', agent_id=None),
        ])
        db.session.commit()
        yield app.test_client(), owner.id
        db.session.remove(); db.drop_all()

def signed(client, endpoint, fields, *, key=KEY):
    body={'version':1,'tenant':TENANT,'email':'jet.tdg3@gmail.com',
          'timestamp':int(time.time()),'nonce':'a'*32, **fields}
    raw=json.dumps(body).encode()
    sig='sha256='+hmac.new(key.encode(),raw,hashlib.sha256).hexdigest()
    return client.post('/api/jetai/reports/'+endpoint,data=raw,
        headers={'Content-Type':'application/json','X-JetAI-Signature':sig})

def test_exact_named_jet_inventory_and_owner_with_unit(client):
    browser,owner_id=client
    inventory=signed(browser,'incoming-offer-inventory',{'scope':'team'})
    assert inventory.status_code==200
    rows=inventory.json['owned']
    assert {x['listing_id'] for x in rows}=={15546,15547}
    assert inventory.json['exceptions'][0]['reason']=='current_agent_unresolved'
    row=next(x for x in rows if x['listing_id']==15546)
    assert row['owner_user_id']==owner_id
    fields={'listing_id':15546,'agent_id':owner_id,'property_key':row['address']}
    response=signed(browser,'incoming-offer-owner',fields)
    assert response.status_code==200 and response.json['owner_email']=='agent@example.test'
    assert response.json['address_key'].count('#5906')==1
    assert response.headers['Cache-Control']=='no-store'
    assert signed(browser,'incoming-offer-owner',{**fields,'property_key':'5904 Niles Dr, #5907, Troy, MI 48098'}).status_code==404
    assert signed(browser,'incoming-offer-owner',{**fields,'agent_id':owner_id+1}).status_code==404

def test_signed_key_does_not_make_anyone_team_reviewer(client):
    browser,owner_id=client
    for email in ('agent@example.test','inactive@example.test','stranger@example.test'):
        assert signed(browser,'incoming-offer-inventory',{'scope':'team','email':email}).status_code==403
        assert signed(browser,'incoming-offer-owner',{'email':email,'listing_id':15546,
            'agent_id':owner_id,'property_key':'5904 Niles Dr, #5906, Troy, MI 48098'}).status_code==403 or email=='agent@example.test'
    assert signed(browser,'incoming-offer-inventory',{'scope':'team'},key='wrong'*8).status_code==401
    assert signed(browser,'incoming-offer-inventory',{'scope':'team','timestamp':0}).status_code==400
