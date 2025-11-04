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
## toto je test ci funguje git
# MODELY DB
class DeviceUser(db.Model):
    __tablename__ = "device_user"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), primary_key=True)
    status = db.Column(db.Boolean, default=True)

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
# PRIDAJ SEM – hneď za class DTCCode(db.Model): ...
class PendingCommand(db.Model):
    __tablename__ = "pending_commands"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False)
    command = db.Column(db.String(50), nullable=False)
    executed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
# --- DTC CODES DATABASE MODEL ---
class DtcCodeMeaning(db.Model):
    __tablename__ = "dtc_codes_meaning"
    id = db.Column(db.Integer, primary_key=True)
    dtc_code = db.Column(db.String(20), unique=True, nullable=False)
    dtc_description = db.Column(db.Text, nullable=True)

@app.route("/init-db")
def init_db():
    db.create_all()
    return jsonify({"status": "Database ok"})

# get na konrolu
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Flask bezi"})

import requests
from flask import Flask, jsonify, request


@app.route("/api/vin/nhtsa", methods=["POST"])
def decode_vin_nhtsa():
    """
    Dekóduje VIN pomocou NHTSA (bez API kľúča, zdarma)
    """
    try:
        payload = request.get_json()
        vin = payload.get("vin")
        if not vin:
            return jsonify({"error": "Missing VIN"}), 400

        url = f"https://vpic.nhtsa.dot.gov/api/vehicles/decodevinvaluesextended/{vin}?format=json"
        response = requests.get(url)
        data = response.json()

        if "Results" not in data:
            return jsonify({"error": "Unexpected API response"}), 500

        vehicle_info = data["Results"][0]
        return jsonify({
            "vin": vin,
            "make": vehicle_info.get("Make"),
            "model": vehicle_info.get("Model"),
            "year": vehicle_info.get("ModelYear"),
            "engine": vehicle_info.get("EngineModel"),
            "bodyClass": vehicle_info.get("BodyClass"),
            "manufacturer": vehicle_info.get("ManufacturerName"),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/add-device", methods=["POST"])
@jwt_required()
def add_device():
    """
    Pridá nové zariadenie (device) do databázy.
    """
    try:
        payload = request.get_json()
        device_id_raw = payload.get("device_id")
        user_id = get_jwt_identity()  # teraz je to string s ID
        user = User.query.get(int(user_id))

        if not user:
            return jsonify({"error": "User not found"}), 404

        # Konverzia na integer
        try:
            device_id = int(device_id_raw)
        except (ValueError, TypeError):
            return jsonify({"error": "Device ID must be an integer"}), 400

        # Kontrola duplicity
        existing = Device.query.get(device_id)
        if existing:
            return jsonify({"error": f"Device ID {device_id} already exists"}), 409

        # Vytvorenie nového záznamu
        new_device = Device(id=device_id, user_id=user.id, status=False)
        db.session.add(new_device)
        db.session.commit()

        return jsonify({
            "status": "success",
            "device_id": device_id,
            "message": f"Device {device_id} successfully added"
        }), 201

    except Exception as e:
        db.session.rollback()
        print("❌ ADD DEVICE ERROR:", e)
        return jsonify({"error": str(e)}), 500



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
        access_token = create_access_token(identity=str(user.id))
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

@app.route("/api/register", methods=["POST"])
def register():
    try:
        data = request.get_json()
        email = data.get("email")
        password = data.get("password")

        if not email or not password:
            return jsonify({"error": "Missing email or password"}), 400

        if User.query.filter_by(email=email).first():
            return jsonify({"error": "Email already exists"}), 409

        new_user = User(email=email, password=password, role="user")
        db.session.add(new_user)
        db.session.commit()

        return jsonify({"status": "success", "message": "User registered"}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """
    RPi sa hlási každých 30s. Server vráti príkaz (ak je).
    ---
    tags:
      - Device
    parameters:
      - in: body
        name: body
        schema:
          properties:
            device_id: {type: integer, example: 1}
    responses:
      200:
        description: OK alebo príkaz
        examples:
          application/json:
            status: ok
          application/json:
            command: GET_VIN
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")
        if not device_id:
            return jsonify({"error": "missing device_id"}), 400

        # Aktualizuj device
        device = Device.query.get(device_id)
        if not device:
            device = Device(id=device_id, status=True)
            db.session.add(device)
        else:
            device.status = True
        db.session.commit()

        # Skontroluj čakajúci príkaz
        cmd = PendingCommand.query.filter_by(device_id=device_id, executed=False).first()
        if cmd:
            cmd.executed = True
            db.session.commit()
            return jsonify({"command": cmd.command}), 200

        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/trigger", methods=["POST"])
def trigger_command():
    """
    Admin pošle príkaz na RPi
    ---
    tags:
      - Admin
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        schema:
          properties:
            device_id: {type: integer, example: 1}
            command: {type: string, enum: [GET_VIN, GET_DTCS_PERM, GET_DTCS_PEND, GET_RPM, GET_TEMP]}
    responses:
      200: {description: Príkaz zaradený}
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")
        command = data.get("command")

        if command not in ["GET_VIN", "GET_DTCS_PERM", "GET_DTCS_PEND", "GET_RPM", "GET_TEMP"]:
            return jsonify({"error": "invalid command"}), 400

        cmd = PendingCommand(device_id=device_id, command=command)
        db.session.add(cmd)
        db.session.commit()
        return jsonify({"status": "queued", "command": command}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
# prijimanie packetov (zatial iba message_type= RAW_DATA)
@app.route("/api/can", methods=["POST"])
def receive_can_packet():
    """
    Raspberry odošle VIN alebo DTC dáta ako TEXT (už dekódované na RPi).
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
            vin:
              type: string
              example: "5J8TB4H55FL123456"
            dtc_code:
              type: string
              example: "P0301"
    responses:
      201:
        description: Dáta boli prijaté a spracované
      400:
        description: Chýbajúce alebo neplatné dáta
    """
    try:
        payload = request.get_json()
        device_id = payload.get("device_id")
        vin = payload.get("vin")
        dtc_code = payload.get("dtc_code")

        if device_id is None:
            return jsonify({"error": "Missing 'device_id'"}), 400

        # Musí byť buď VIN alebo DTC
        if not vin and not dtc_code:
            return jsonify({"error": "Missing 'vin' or 'dtc_code'"}), 400

        if vin and dtc_code:
            return jsonify({"error": "Provide either 'vin' or 'dtc_code', not both"}), 400

        # --- Spracovanie VIN ---
        if vin:
            vin = vin.strip().upper()
            if len(vin) != 17:
                return jsonify({"error": "VIN must be 17 characters"}), 400

            vehicle = Vehicle.query.filter_by(vin=vin).first()
            if not vehicle:
                vehicle = Vehicle(vin=vin)
                db.session.add(vehicle)
                db.session.commit()

            # Aktualizuj last_vin_id pre device
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state:
                state = DeviceVehicle(device_id=device_id, last_vin_id=vehicle.id)
                db.session.add(state)
            else:
                state.last_vin_id = vehicle.id
            db.session.commit()

            return jsonify({"status": "VIN stored", "vin": vin}), 201

        # --- Spracovanie DTC ---
        if dtc_code:
            dtc_code = dtc_code.strip().upper()

            # Získaj aktuálny VIN pre daný device
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated with this device"}), 400

            vehicle = Vehicle.query.get(state.last_vin_id)
            if not vehicle:
                return jsonify({"error": "Vehicle not found"}), 404

            # Ulož DTC
            new_dtc = DTCCode(vin_id=vehicle.id, dtc_code=dtc_code)
            db.session.add(new_dtc)
            db.session.commit()

            return jsonify({
                "status": "DTC stored",
                "vin": vehicle.vin,
                "dtc": dtc_code
            }), 201

    except Exception as e:
        db.session.rollback()
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
