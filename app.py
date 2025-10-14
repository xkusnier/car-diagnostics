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

# ✅ Endpoint na prijímanie CAN packetov
@app.route("/api/can", methods=["POST"])
def receive_can_packet():
    """
    Očakáva JSON:
    {
        "type": "VIN",
        "data": "4A374E453147303036343234"
    }
    alebo
    {
        "type": "DTC",
        "data": "P0301"
    }
    """
    try:
        payload = request.get_json()
        packet_type = payload.get("type")
        data_hex = payload.get("data")

        if not packet_type or not data_hex:
            return jsonify({"error": "Missing fields 'type' or 'data'"}), 400

        if packet_type.upper() == "VIN":
            # dekódovanie hex -> string
            vin = bytes.fromhex(data_hex).decode(errors="ignore").strip()
            if not vin:
                return jsonify({"error": "Invalid VIN data"}), 400

            vehicle = Vehicle.query.filter_by(vin=vin).first()
            if not vehicle:
                vehicle = Vehicle(vin=vin)
                db.session.add(vehicle)
                db.session.commit()

            return jsonify({"status": "VIN stored", "vin": vin}), 201

        elif packet_type.upper() == "DTC":
            dtc_code = bytes.fromhex(data_hex).decode(errors="ignore").strip()
            if not dtc_code:
                return jsonify({"error": "Invalid DTC data"}), 400

            # priradi DTC k poslednému VIN, ktoré bolo prijaté
            last_vehicle = Vehicle.query.order_by(Vehicle.id.desc()).first()
            if not last_vehicle:
                return jsonify({"error": "No VIN found to assign DTC"}), 400

            new_dtc = DTCCode(vin_id=last_vehicle.id, dtc_code=dtc_code)
            db.session.add(new_dtc)
            db.session.commit()

            return jsonify({"status": "DTC stored", "vin": last_vehicle.vin, "dtc": dtc_code}), 201

        else:
            return jsonify({"error": "Unknown packet type"}), 400

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
