from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
import os

app = Flask(__name__)

# ✅ Ak nie je definovaný DATABASE_URL, použi SQLite ako fallback
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
# Render niekedy pridáva "postgres://" namiesto "postgresql://"
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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
        car_id=data.get("car_id"),
        code=data.get("code"),
        severity=data.get("severity", "unknown")
    )
    db.session.add(new_entry)
    db.session.commit()
    return jsonify({"status": "ok"})

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Flask server running successfully 🚀"})

# ✅ Pridaj toto, aby Render vedel, čo spúšťať aj lokálne
if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # vytvorí tabuľku ak neexistuje
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
