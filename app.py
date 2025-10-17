from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os

app = Flask(__name__)

# DATABAZA
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# MODELY DB
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
class LastVinDevice(db.Model):
    __tablename__ = "last_vin_device"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, unique=True, nullable=False)
    last_vin_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    

@app.route("/init-db")
def init_db():
    db.create_all()
    return jsonify({"status": "Database ok"})

# get na konrolu
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Flask bezi"})

# prijimanie packetov (zatial iba message_type= RAW_DATA)
@app.route("/api/can", methods=["POST"])
def receive_can_packet():
    """
    VIN TEST postman:
    {
        "device_id": 1,
        "data": "4A374E453147303036343234"
    }

    
    DTC TEST postman:
    {
    "device_id": 1,
    "data": "544D424A4A374E"
    }
    """
    try:
        payload = request.get_json()
        data_hex = payload.get("data")
        device_id = payload.get("device_id")

        if not data_hex or device_id is None:
            return jsonify({"error": "Missing 'data' or 'device_id'"}), 400

        # dekodovanie hex -> text
        decoded_str = bytes.fromhex(data_hex).decode(errors="ignore").strip()
        if not decoded_str:
            return jsonify({"error": "Invalid hex data"}), 400

        # --- Rozlíšenie VIN vs DTC ---
        if len(decoded_str) > 15:  # VIN (17 znakov) zatial docasny hardcode - treba dorobit rozlisovanie podla prvych znakov napr vwv - len skoda ma ine atd... + kontrola poctu znakov 
                                    # -alebo externe overit ci VIN existuje, neni najlepsie solution niektore vo vindecoderi nejsu + vpodstate mi je jedno ci je vin v niakej externej db, alebo nie...
            vin = decoded_str
            vehicle = Vehicle.query.filter_by(vin=vin).first()
            if not vehicle:
                vehicle = Vehicle(vin=vin)
                db.session.add(vehicle)
                db.session.commit()

            # aktualizuj LASTVINDEVICE
            state = LastVinDevice.query.filter_by(device_id=device_id).first()
            if not state:
                # vytvorenie device <-> vin
                state = LastVinDevice(device_id=device_id, last_vin_id=vehicle.id)
                db.session.add(state)
            else:
                # zmenenie last vin pre dany device
                state.last_vin_id = vehicle.id
            db.session.commit()

            return jsonify({"status": "VIN stored", "vin": vin}), 201

        else:  # packet je DTC
            dtc_code = decoded_str

            # VIN, ktoremu toto DTC patri
            state = LastVinDevice.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "VIN not found"}), 400

            vehicle = Vehicle.query.get(state.last_vin_id)
            if not vehicle:
                return jsonify({"error": "VIN not found"}), 404

            new_dtc = DTCCode(vin_id=vehicle.id, dtc_code=dtc_code)
            db.session.add(new_dtc)
            db.session.commit()

            return jsonify({ # FEEDBACK PRE POSTMANA
                "status": "DTC stored",
                "vin": vehicle.vin,
                "dtc": dtc_code
            }), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# VSETKO ZOBRAZ GET
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
