from app import create_app, db, socketio
from app.models import User, Agent, Transaction, LeadGenLog, BusinessPlan, Pipeline

app = create_app()

@app.shell_context_processor
def make_shell_context():
    return dict(db=db, User=User, Agent=Agent, Transaction=Transaction,
                LeadGenLog=LeadGenLog, BusinessPlan=BusinessPlan, Pipeline=Pipeline)

if __name__ == '__main__':
    socketio.run(app, debug=False, host='0.0.0.0', port=5000)
