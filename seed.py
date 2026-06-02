"""Run once to create admin user and load agents from CTE spreadsheet."""
from app import create_app, db
from app.models import User, Agent
from werkzeug.security import generate_password_hash

AGENTS = [
    'Brock Bean','Kristin Ebert','Laith Marroki','Manual Kajy','Parker Anderson',
    'Johnathon Sesi','Megan Calahan','Keith Finlayson','Bryan Besaw','Martin Shauya',
    'Samar Mansour','Chris Tilles','Jimmy Zaflow','Kimberly Duff','Janice Smith',
    'Jair Lopez','Chaise Hughes','Kathy Toth','Julie Kelsey','Austin Nagaitis',
    'Jovona Manni','Nicole Dorris','Shariful Hossain','Tahlia Campbell','Amanda Dryden',
    'Peter McShane Lewis','Alia Molhem','Mark Yaldo','Ryan Ebert','Joe Delia',
    'Alex Linton','Rocky Fowler','Sarah Hayes','April Nugent','Alexandra Salvatore',
    'Casey Wagner','Austin Montgomery','John Delia'
]

app = create_app()
with app.app_context():
    db.create_all()

    # Admin user
    if not User.query.filter_by(username='renee').first():
        u = User(username='renee', email='Renee@thedeliagroup.com', role='admin')
        u.set_password('TDG2026!')
        db.session.add(u)
        print('Created admin user: renee / TDG2026!')

    # Staff user
    if not User.query.filter_by(username='admin').first():
        u2 = User(username='admin', email='admin@thedeliagroup.com', role='staff')
        u2.set_password('TDGstaff2026!')
        db.session.add(u2)
        print('Created staff user: admin / TDGstaff2026!')

    # Agents
    created = 0
    for name in AGENTS:
        if not Agent.query.filter_by(name=name).first():
            db.session.add(Agent(name=name, role='Both L/B', agent_type='Individual', status='Active'))
            created += 1

    db.session.commit()
    print(f'Seeded {created} agents.')
    print(f'Total agents in DB: {Agent.query.count()}')
    print('Done! Go to /login')
