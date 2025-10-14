from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class DiagnosticData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.String(50))
    code = db.Column(db.String(10))
    severity = db.Column(db.String(20))
    timestamp = db.Column(db.DateTime, server_default=db.func.now())
