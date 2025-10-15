from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os

app = Flask(__name__)

# ✅ Databáza
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ✅ Modely
class Vehicle(db.Model):
    __tablename__ = "vehicles"
    id = db.Column(db.Integer, primary_key=True)
    vin = db.Column(db.String(50), unique=True, nullable=False)
    dtcs = db.relationship("DTCCode", backref="vehicle", lazy=True, cascade="all, delete")

class DTCCode(db.Model):
    __tablename__ = "dtc_codes"
    id = db.Column(db.Integer, primary_key=True)
    vin_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ✅ Inicializácia databázy
@app.route("/init-db")
def init_db():
    db.create_all()
    return jsonify({"status": "Database initialized ✅"})

# ✅ Root route pre Render kontrolu
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Flask backend running 🚀"})

# ✅ Endpoint na prijímanie CAN packetov
@app.route("/api/can", methods=["POST"])
def receive_can_packet():
    """
    Očakáva JSON:
    {
        "data": "4A374E453147303036343234"
    }
    Backend automaticky rozlíši VIN alebo DTC podľa dĺžky a formátu.
    """
    try:
        payload = request.get_json()
        data_hex = payload.get("data")

        if not data_hex:
            return jsonify({"error": "Missing field 'data'"}), 400

        # dekóduj hex -> text
        decoded_str = bytes.fromhex(data_hex).decode(errors="ignore").strip()

        if not decoded_str:
            return jsonify({"error": "Invalid hex data"}), 400

        # Rozlíšenie typu
        if len(decoded_str) > 15:  # typicky VIN (17 znakov)
            vin = decoded_str
            vehicle = Vehicle.query.filter_by(vin=vin).first()
            if not vehicle:
                vehicle = Vehicle(vin=vin)
                db.session.add(vehicle)
                db.session.commit()

            return jsonify({
                "status": "VIN stored",
                "vin": vin
            }), 201

        else:  # typicky DTC
            dtc_code = decoded_str
            last_vehicle = Vehicle.query.order_by(Vehicle.id.desc()).first()

            if not last_vehicle:
                return jsonify({"error": "No VIN found to assign DTC"}), 400

            new_dtc = DTCCode(vin_id=last_vehicle.id, dtc_code=dtc_code)
            db.session.add(new_dtc)
            db.session.commit()

            return jsonify({
                "status": "DTC stored",
                "vin": last_vehicle.vin,
                "dtc": dtc_code
            }), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ Zobrazenie všetkých VIN + DTC
@app.route("/api/all", methods=["GET"])
def show_all():
    vehicles = Vehicle.query.all()
    data = []
    for v in vehicles:
        data.append({
            "vin": v.vin,
            "dtc_codes": [d.dtc_code for d in v.dtcs]
        })
    return jsonify(data)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
