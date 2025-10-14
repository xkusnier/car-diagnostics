from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
import os

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
db = SQLAlchemy(app)

class DiagnosticData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.String(50))
    code = db.Column(db.String(10))
    severity = db.Column(db.String(20))
    timestamp = db.Column(db.DateTime)

@app.route("/api/data", methods=["POST"])
def receive_data():
    data = request.get_json()
    new_entry = DiagnosticData(
        car_id=data["car_id"],
        code=data["code"],
        severity=data.get("severity", "unknown")
    )
    db.session.add(new_entry)
    db.session.commit()
    return jsonify({"status": "ok"})
