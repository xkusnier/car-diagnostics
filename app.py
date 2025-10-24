from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flasgger import Swagger
from datetime import datetime
import os
import requests
from flask_cors import CORS
import csv
import requests
from io import StringIO
from flask import jsonify
from datetime import datetime, timedelta
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity  # Overenie importu

app = Flask(__name__)
CORS(app)
swagger = Swagger(app)

# Konfigurácia JWT
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "your-secret-key")  # Nahraďte vlastným tajným kľúčom
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)  # Token platný 1 hodinu
jwt = JWTManager(app)

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
class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="user")
    email = db.Column(db.String(120), unique=True, nullable=False)
    devices = db.relationship("Device", backref="user", lazy=True, cascade="all, delete")

class Device(db.Model):
    __tablename__ = "device"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status = db.Column(db.Boolean, default=False)  # True = online
    link = db.relationship("DeviceVehicle", backref="device", lazy=True, cascade="all, delete")

class DeviceVehicle(db.Model):
    __tablename__ = "device_vehicle"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False)
    last_vin_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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

@app.route("/init-db")
def init_db():
    db.create_all()
    return jsonify({"status": "Database ok"})

# get na konrolu
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Flask bezi"})

# --- DTC CODES DATABASE MODEL ---
class DtcCodeMeaning(db.Model):
    __tablename__ = "dtc_codes_meaning"
    id = db.Column(db.Integer, primary_key=True)
    dtc_code = db.Column(db.String(20), unique=True, nullable=False)
    dtc_description = db.Column(db.Text, nullable=True)
# Endpoint pre prihlásenie
@app.route("/api/login", methods=["POST"])
def login():
    """
    Prihlásenie používateľa a vrátenie JWT tokenu.
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            email:
              type: string
              example: "user@example.com"
            password:
              type: string
              example: "password123"
    responses:
      200:
        description: Úspešné prihlásenie s JWT tokenom
      401:
        description: Neplatné prihlasovacie údaje
    """
    try:
        data = request.get_json()
        email = data.get("email")
        password = data.get("password")

        if not email or not password:
            return jsonify({"error": "Missing email or password"}), 400

        user = User.query.filter_by(email=email).first()
        if not user or user.password != password:  # Pre jednoduchosť porovnávam heslo priamo (v praxi použi hashovanie, napr. bcrypt)
            return jsonify({"error": "Invalid credentials"}), 401

        # Generovanie JWT tokenu
        access_token = create_access_token(identity={"id": user.id, "email": user.email, "role": user.role})
        return jsonify({"status": "success", "access_token": access_token}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500




@app.route("/api/load-dtc-codes", methods=["POST"])
def load_dtc_codes_from_csv():
    """
    Načíta DTC kódy z CSV súboru (napr. uloženého na GitHube) a uloží ich do databázy.
    ---
    tags:
      - DTC Codes
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            csv_url:
              type: string
              example: "https://raw.githubusercontent.com/xkusnier/car-diagnostics/main/dtc_converted.csv"
    responses:
      200:
        description: DTC kódy boli úspešne nahraté do databázy
    """
    try:
        payload = request.get_json()
        csv_url = payload.get("csv_url")

        if not csv_url:
            return jsonify({"error": "Missing 'csv_url' parameter"}), 400

        # ✅ Stiahni CSV z GitHubu (RAW link)
        response = requests.get(csv_url)
        if response.status_code != 200:
            return jsonify({"error": f"Failed to fetch CSV: {response.status_code}"}), 400

        csv_text = response.text
        csv_reader = csv.reader(StringIO(csv_text))

        inserted, skipped = 0, 0

        for row in csv_reader:
            if len(row) < 2:
                continue

            dtc_code = row[0].strip()
            dtc_description = row[1].strip()

            # ✅ Skontroluj, či DTC už existuje
            if DtcCodeMeaning.query.filter_by(dtc_code=dtc_code).first():
                skipped += 1
                continue

            db.session.add(DtcCodeMeaning(dtc_code=dtc_code, dtc_description=dtc_description))
            inserted += 1

        db.session.commit()

        return jsonify({
            "status": "success",
            "inserted": inserted,
            "skipped_existing": skipped
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500



@app.route("/api/dtc-description", methods=["POST"])
def get_dtc_description():
    """
    Získa textový popis DTC kódu z databázy.
    ---
    tags:
      - DTC Codes
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            dtc_code:
              type: string
              example: "P0263"
    responses:
      200:
        description: Popis DTC kódu
      404:
        description: Kód neexistuje v databáze
    """
    try:
        payload = request.get_json()
        dtc_code = payload.get("dtc_code")

        if not dtc_code:
            return jsonify({"error": "Missing 'dtc_code' parameter"}), 400

        # Vyhľadanie v databáze (case-insensitive)
        record = DtcCodeMeaning.query.filter(
            db.func.lower(DtcCodeMeaning.dtc_code) == dtc_code.lower()
        ).first()

        if not record:
            return jsonify({
                "status": "not_found",
                "message": f"DTC code '{dtc_code}' not found in database."
            }), 404

        return jsonify({
            "status": "success",
            "dtc_code": record.dtc_code,
            "description": record.dtc_description
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500




@app.route("/api/hello", methods=["POST"]) #    Raspberry posle serveru ze je online - server mu odpovie REQUEST_VIN. - lebo vsak server nevie kto je raspbbery musi sa ohlasit prve...
def register_device_and_request_vin():
    """
        Raspberry sa ohlási serveru, že je online.
        Server mu odpovie príkazom REQUEST_VIN.
        ---
        tags:
          - VIN Communication
        parameters:
          - in: body
            name: body
            required: true
            schema:
              type: object
              properties:
                device_id:
                  type: integer
                  example: 1
        responses:
          200:
            description: Server žiada VIN
            examples:
              application/json:
                command: REQUEST_VIN
        """
    try:
        payload = request.get_json()
        device_id = payload.get("device_id")

        if device_id is None:
            return jsonify({"error": "Missing 'device_id'"}), 400

        state = DeviceVehicle.query.filter_by(device_id=device_id).first()
        if not state:
            state = DeviceVehicle(device_id=device_id)
            db.session.add(state)
        state.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({
            "command": "REQUEST_VIN",
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# prijimanie packetov (zatial iba message_type= RAW_DATA)
@app.route("/api/can", methods=["POST"])
def receive_can_packet():
    """
    Raspberry odošle VIN alebo DTC dáta na server.
    ---
    tags:
      - VIN Communication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            device_id:
              type: integer
              example: 1
            data:
              type: string
              example: "5756575A5A5A314A5A3257323337393733"
    responses:
      201:
        description: Dáta boli prijaté a spracované
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
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state:
                # vytvorenie device <-> vin
                state = DeviceVehicle(device_id=device_id, last_vin_id=vehicle.id)
                db.session.add(state)
            else:
                # zmenenie last vin pre dany device
                state.last_vin_id = vehicle.id
            db.session.commit()

            return jsonify({"status": "VIN stored", "vin": vin}), 201

        else:  # packet je DTC
            dtc_code = decoded_str

            # VIN, ktoremu toto DTC patri
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
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


@app.route("/api/vindecode", methods=["POST"])
def decode_vin_apiverve():
    """
    Dekóduje VIN pomocou apiverve VIN Decoder API.
    ---
    tags:
      - VIN Information (apiverve)
    """
    try:
        payload = request.get_json()
        vin = payload.get("vin")

        if not vin:
            return jsonify({"error": "Missing 'vin' in body"}), 400

        api_key = os.getenv("VINDECODER_API_KEY")
        if not api_key:
            return jsonify({"error": "Missing VINDECODER_API_KEY env var on server"}), 500

        # ✅ Apiverve VIN Decoder API používa GET
        url = f"https://api.apiverve.com/v1/vindecoder?vin={vin}"
        headers = {
            "X-API-Key": api_key
        }

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            return jsonify({
                "error": "VIN decoder API error",
                "status": response.status_code,
                "details": response.text
            }), response.status_code

        data = response.json()

        # Väčšina Apiverve API má štruktúru: {"status":"success","data": {...}}
        if "data" in data:
            data = data["data"]

        cleaned = {
            "vin": vin,
            "make": data.get("make"),
            "model": data.get("model"),
            "year": data.get("year"),
            "trim": data.get("trim"),
            "engine": data.get("engine"),
            "transmission": data.get("transmission"),
            "driveType": data.get("driveType"),
            "fuelType": data.get("fuelType"),
            "bodyStyle": data.get("bodyStyle")
        }

        return jsonify({
            "status": "success",
            "source": "apiverve",
            "data": cleaned
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500



# VSETKO ZOBRAZ GET
@app.route("/api/all", methods=["GET"])
def show_all():
    """
    Zobrazí všetky VIN a priradené DTC kódy.
    ---
    tags:
      - VIN Communication
    responses:
      200:
        description: Zoznam VIN a DTC
    """
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
