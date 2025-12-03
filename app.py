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
from openai import OpenAI

groq_client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)


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
    # ➕ NOVÉ STĹPCE
    year = db.Column(db.String(10), nullable=True)
    brand = db.Column(db.String(10), nullable=True)
    model = db.Column(db.String(100), nullable=True)
    engine = db.Column(db.String(100), nullable=True)
    
    dtcs_active = db.relationship("DTCCodeActive", backref="vehicle", lazy=True, cascade="all, delete")
    dtcs_history = db.relationship("DTCCodeHistory", backref="vehicle", lazy=True, cascade="all, delete")

class DTCCodeActive(db.Model):
    __tablename__ = "dtc_codes_active"
    id = db.Column(db.Integer, primary_key=True)
    vin_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)
    severity = db.Column(db.String(20), nullable=False, default="medium")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class DTCCodeHistory(db.Model):
    __tablename__ = "dtc_codes_history"
    id = db.Column(db.Integer, primary_key=True)
    vin_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)
    severity = db.Column(db.String(20), nullable=False, default="medium")
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


# ============================
# 🔐 3-WAY HANDSHAKE (SYN, SYN-ACK, ACK)
# ============================
def ai_detect_severity(description):
    try:
        prompt = f"""
You are an automotive diagnostic assistant.

Classify severity of the DTC description into:
- critical
- medium
- low

Return only one word.

Description:
{description}
"""

        response = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        value = response.choices[0].message.content.strip().lower()

        if value not in ["critical", "medium", "low"]:
            return "medium"

        return value
    except Exception as e:
        print("⚠️ AI severity error:", e)
        return "medium"


@app.route("/api/connect", methods=["POST"])
def device_connect_syn():
    """
    1. RPi pošle SYN ⇒ server odpovie SYN-ACK
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")

        if not device_id:
            return jsonify({"error": "missing device_id"}), 400

        device = Device.query.get(device_id)
        if not device:
            device = Device(id=device_id, status=False)
            db.session.add(device)
        else:
            device.status = False  # ešte nie je potvrdené

        db.session.commit()

        return jsonify({
            "handshake": "SYN-ACK",
            "device_id": device_id
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/connect/ack", methods=["POST"])
def device_connect_ack():
    """
    2. RPi pošle ACK ⇒ označíme device ako ONLINE
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")

        if not device_id:
            return jsonify({"error": "missing device_id"}), 400

        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": "device not found"}), 404

        device.status = True  # teraz je oficiálne online
        db.session.commit()

        return jsonify({
            "status": "online",
            "device_id": device_id,
            "handshake": "ACK-complete"
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500





@app.route("/api/device/<int:device_id>/clear-dtcs", methods=["POST"])
@jwt_required()
def clear_device_dtcs(device_id):
    """
    Namiesto vymazania DTC z DB teraz:
    ➤ vytvorí pending command CLEAR_DTCS
    ➤ RPi vymaže chyby v aute
    ➤ RPi neskôr pošle výsledok clear_status cez /api/can
    """

    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)

        if not user:
            return jsonify({"error": "User not found"}), 404

        # Kontrola vlastníctva ako predtým
        if user.role == "admin":
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()

        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404

        # ⚠️ TOTO SME ODSTRÁNILI:
        # deleted_count = DTCCodeActive.query.filter_by(vin_id=vin_id).delete()
        # teraz sa DB nemaže hneď!

        # ➕ NOVÉ: vytvoríme príkaz
        cmd = PendingCommand(device_id=device_id, command="CLEAR_DTCS")
        db.session.add(cmd)
        db.session.commit()

        return jsonify({
            "status": "waiting",
            "message": "Clear command sent to device. Waiting for RPi confirmation."
        }), 200

    except Exception as e:
        db.session.rollback()
        print("❌ CLEAR DEVICE DTCS ERROR:", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/dtc-history-full", methods=["POST"])
@jwt_required(optional=True)
def dtc_history_full():
    """
    Vráti úplnú históriu DTC kódov pre dané VIN,
    vrátane popisu chyby z tabuľky dtc_codes_meaning.
    Prístupné pre všetkých prihlásených aj neprihlásených.
    """
    try:
        data = request.get_json()
        vin = data.get("vin")

        if not vin:
            return jsonify({"error": "Missing 'vin' parameter"}), 400

        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404

        history = (
            db.session.query(
                DTCCodeHistory.dtc_code,
                DTCCodeHistory.created_at,
                DtcCodeMeaning.dtc_description
            )
            .outerjoin(DtcCodeMeaning, DTCCodeHistory.dtc_code == DtcCodeMeaning.dtc_code)
            .filter(DTCCodeHistory.vin_id == vehicle.id)
            .order_by(DTCCodeHistory.created_at.desc())
            .all()
        )

        results = [
            {
                "dtc_code": h.dtc_code,
                "description": h.dtc_description or "No description available",
                "created_at": h.created_at.isoformat()
            }
            for h in history
        ]

        return jsonify({
            "status": "success",
            "vin": vin.upper(),
            "history": results
        }), 200

    except Exception as e:
        print("❌ DTC HISTORY FULL ERROR:", e)
        return jsonify({"error": str(e)}), 500




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
            "brand": vehicle_info.get("Brand"),
            "model": vehicle_info.get("Model"),
            "year": vehicle_info.get("ModelYear"),
            "engine": vehicle_info.get("EngineModel"),
            "bodyClass": vehicle_info.get("BodyClass"),
            "manufacturer": vehicle_info.get("ManufacturerName"),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/<int:device_id>/read-dtcs", methods=["POST"])
@jwt_required()
def read_device_dtcs(device_id):
    """
    Pošle príkaz GET_DTCS_PERM na zariadenie pre čítanie DTC kódov.
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)

        if not user:
            return jsonify({"error": "User not found"}), 404

        # Kontrola vlastníctva
        if user.role == "admin":
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()

        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404

        # Vytvoríme príkaz GET_DTCS_PERM
        cmd = PendingCommand(device_id=device_id, command="GET_DTCS_PERM")
        db.session.add(cmd)
        db.session.commit()

        return jsonify({
            "status": "success",
            "message": "Read DTC command sent to device",
            "device_id": device_id,
            "command": "GET_DTCS_PERM"
        }), 200

    except Exception as e:
        db.session.rollback()
        print("❌ READ DEVICE DTCS ERROR:", e)
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
        target_user_id = payload.get("user_id")  # 👈 admin zadá user_id
        current_user_id = int(get_jwt_identity())
        current_user = User.query.get(current_user_id)

        if not current_user:
            return jsonify({"error": "User not found"}), 404

        # 🔐 ak nie je admin, môže pridať len sebe
        if current_user.role != "admin":
            target_user_id = current_user.id

        if not target_user_id:
            return jsonify({"error": "Missing user_id (admin only)"}), 400

        try:
            device_id = int(device_id_raw)
        except (ValueError, TypeError):
            return jsonify({"error": "Device ID must be an integer"}), 400

        existing = Device.query.get(device_id)
        if existing:
            return jsonify({"error": f"Device ID {device_id} already exists"}), 409

        # ✅ pridanie zariadenia
        new_device = Device(id=device_id, user_id=int(target_user_id), status=False)
        db.session.add(new_device)
        db.session.commit()

        return jsonify({
            "status": "success",
            "device_id": device_id,
            "assigned_to": int(target_user_id),
            "message": f"Device {device_id} assigned to user {target_user_id}"
        }), 201

    except Exception as e:
        db.session.rollback()
        print("❌ ADD DEVICE ERROR:", e)
        return jsonify({"error": str(e)}), 500




@app.route("/api/device/<int:device_id>/diagnostics", methods=["GET"])
@jwt_required()
def device_diagnostics(device_id):
    """
    Vráti DTC kódy, popis a VIN pre konkrétne zariadenie.
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)

        if not user:
            return jsonify({"error": "User not found"}), 404

        # 👑 Admin môže vidieť všetko
        if user.role == "admin":
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()

        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404

        vin = None
        year = None
        brand = None
        model = None
        engine = None
        dtcs = []

        if device.link and len(device.link) > 0 and device.link[0].last_vin_id:
            vin_obj = Vehicle.query.get(device.link[0].last_vin_id)
            if vin_obj:
                vin = vin_obj.vin
                brand = vin_obj.brand
                year = vin_obj.year
                model = vin_obj.model
                engine = vin_obj.engine

                # 🧩 Join s tabuľkou dtc_codes_meaning kvôli popisu
                dtcs_query = (
                    db.session.query(
                        DTCCodeActive.dtc_code,
                        DTCCodeActive.severity,
                        DTCCodeActive.created_at,
                        DtcCodeMeaning.dtc_description
                    )
                    .outerjoin(DtcCodeMeaning, DTCCodeActive.dtc_code == DtcCodeMeaning.dtc_code)
                    .filter(DTCCodeActive.vin_id == vin_obj.id)
                    .order_by(DTCCodeActive.created_at.desc())
                    .all()
                )

                dtcs = [
                    {
                        "dtc_code": d.dtc_code,
                        "description": d.dtc_description or "No description",
                        "severity": d.severity,
                        "created_at": d.created_at.isoformat(),
                    }
                    for d in dtcs_query
                ]

        return jsonify({
            "status": "success",
            "device_id": device.id,
            "vin": vin,
            "brand": brand,
            "year": year,
            "model": model,
            "engine": engine,
            "dtc_codes": dtcs or [],
            "online": device.status
        }), 200

    except Exception as e:
        print("❌ DEVICE DIAGNOSTICS ERROR:", e)
        return jsonify({"error": str(e)}), 500





@app.route("/api/my-devices", methods=["GET"])
@jwt_required()
def my_devices():
    """
    Vráti všetky zariadenia prihláseného používateľa,
    spolu s VIN a online statusom.
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)

        if not user:
            return jsonify({"error": "User not found"}), 404

        # 👑 ak admin → všetky devices
        if user.role == "admin":
            devices = Device.query.all()
        else:
            devices = Device.query.filter_by(user_id=user_id).all()

        result = []
        for d in devices:
            vin = None
            if d.link and len(d.link) > 0 and d.link[0].last_vin_id:
                vin_obj = Vehicle.query.get(d.link[0].last_vin_id)
                vin = vin_obj.vin if vin_obj else None

            result.append({
                "device_id": d.id,
                "vin": vin,
                "status": "Online" if d.status else "Offline",
                "user_id": d.user_id  # 👈 pre admina
            })

        return jsonify({"status": "success", "devices": result}), 200

    except Exception as e:
        print("❌ MY DEVICES ERROR:", e)
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
        if not user or user.password != password:
            return jsonify({"error": "Invalid credentials"}), 401

        # 🔑 Generovanie JWT tokenu
        access_token = create_access_token(identity=str(user.id))
        return jsonify({
            "status": "success",
            "access_token": access_token,
            "role": user.role   # 👈 pošleme rolu do FE
        }), 200

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
            command: {type: string, enum: [GET_VIN, GET_DTCS_PERM, GET_DTCS_PEND, GET_RPM, GET_TEMP, CLEAR_DTCS]}
    responses:
      200: {description: Príkaz zaradený}
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")
        command = data.get("command")

        # CLEAR_DTCS patrí medzi povolené príkazy
        valid_commands = [
            "GET_VIN",
            "GET_DTCS_PERM",
            "GET_DTCS_PEND",
            "GET_RPM",
            "GET_TEMP",
            "CLEAR_DTCS",
        ]

        if command not in valid_commands:
            return jsonify({"error": "invalid command"}), 400

        cmd = PendingCommand(device_id=device_id, command=command)
        db.session.add(cmd)
        db.session.commit()

        return jsonify({"status": "queued", "command": command}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/can", methods=["POST"])
def receive_can_packet():
    """
    RPi pošle VIN, DTC alebo clear_status po CLEAR_DTCS akcii.
    """
    try:
        payload = request.get_json()
        device_id = payload.get("device_id")
        vin = payload.get("vin")
        dtc_code = payload.get("dtc_code")
        
        # ➕ NOVÉ: výsledok CLEAR_DTCS
        clear_status = payload.get("clear_status")  # "ok" alebo "fail"

        # ➕ NOVÉ: dodatočné informácie o vozidle
        year = payload.get("year")
        model = payload.get("model") 
        brand = payload.get("brand")
        engine = payload.get("engine")

        if device_id is None:
            return jsonify({"error": "Missing 'device_id'"}), 400

        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": f"Device {device_id} not found"}), 404

        # -----------------------------------
        # 1️⃣ NOVÁ LOGIKA: CLEAR_DTCS potvrdenie
        # -----------------------------------
        if clear_status is not None:
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()

            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated for clear"}), 400

            if clear_status == "ok":
                DTCCodeActive.query.filter_by(vin_id=state.last_vin_id).delete()
                db.session.commit()

                return jsonify({
                    "status": "DTC cleared",
                    "vin_id": state.last_vin_id
                }), 200
            
            return jsonify({"status": "Clear failed"}), 200

        # -----------------------------------
        # 2️⃣ ROZŠÍRENÉ spracovanie VIN s dodatočnými informáciami
        # -----------------------------------
        if vin:
            vin = vin.strip().upper()
            if len(vin) != 17:
                return jsonify({"error": "VIN must be 17 characters"}), 400

            vehicle = Vehicle.query.filter_by(vin=vin).first()
            if not vehicle:
                # ➕ Vytvoríme nové vozidlo s dodatočnými informáciami
                vehicle = Vehicle(vin=vin)
                
                # ✅ Pridáme nové polia AK EXISTUJÚ v modeli
                if hasattr(Vehicle, 'year') and year:
                    vehicle.year = year
                if hasattr(Vehicle, 'brand') and brand:
                    vehicle.brand = brand  
                if hasattr(Vehicle, 'model') and model:
                    vehicle.model = model  
                if hasattr(Vehicle, 'engine') and engine:
                    vehicle.engine = engine
                    
                db.session.add(vehicle)
                db.session.commit()
            else:
                # ➕ Aktualizujeme existujúce vozidlo s novými informáciami
                updated = False
                if hasattr(Vehicle, 'year') and year and vehicle.year != year:
                    vehicle.year = year
                    updated = True
                if hasattr(Vehicle, 'brand') and brand and vehicle.brand != brand:
                    vehicle.brand = brand  
                    updated = True
                if hasattr(Vehicle, 'model') and model and vehicle.model != model:
                    vehicle.model = model  
                    updated = True
                if hasattr(Vehicle, 'engine') and engine and vehicle.engine != engine:
                    vehicle.engine = engine
                    updated = True
                    
                if updated:
                    db.session.commit()

            device.status = True

            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state:
                state = DeviceVehicle(device_id=device_id, last_vin_id=vehicle.id)
                db.session.add(state)
            else:
                state.last_vin_id = vehicle.id

            db.session.commit()

            return jsonify({
                "status": "VIN stored",
                "vin": vin,
                "brand":brand,
                "year": vehicle.year,
                "model": vehicle.model, 
                "engine": vehicle.engine
            }), 201

        # -----------------------------------
        # 3️⃣ Pôvodné spracovanie DTC (S AI SEVERITY)
        # -----------------------------------
        if dtc_code:
            dtc_code = dtc_code.strip().upper()
        
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated with this device"}), 400
        
            vehicle = Vehicle.query.get(state.last_vin_id)
            if not vehicle:
                return jsonify({"error": "Vehicle not found"}), 404
        
            # Nájdeme popis DTC
            meaning = DtcCodeMeaning.query.filter(
                db.func.lower(DtcCodeMeaning.dtc_code) == dtc_code.lower()
            ).first()
        
            description = meaning.dtc_description if meaning else ""
        
            # 🧠 Spočítame severity raz
            severity = ai_detect_severity(description)
        
            # HISTÓRIA
            db.session.add(
                DTCCodeHistory(
                    vin_id=vehicle.id,
                    dtc_code=dtc_code,
                    severity=severity
                )
            )
        
            # AKTÍVNE — replace
            DTCCodeActive.query.filter_by(
                vin_id=vehicle.id,
                dtc_code=dtc_code
            ).delete()
        
            db.session.add(
                DTCCodeActive(
                    vin_id=vehicle.id,
                    dtc_code=dtc_code,
                    severity=severity
                )
            )
        
            db.session.commit()
        
            return jsonify({
                "status": "DTC stored",
                "vin": vehicle.vin,
                "dtc": dtc_code,
                "severity": severity
            }), 201

    except Exception as e:
        db.session.rollback()
        print("❌ CAN PACKET ERROR:", e)
        return jsonify({"error": str(e)}), 500




@app.route("/api/dtc-history/<vin>", methods=["GET"])
@jwt_required()
def get_dtc_history(vin):
    vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
    if not vehicle:
        return jsonify({"error": "Vehicle not found"}), 404

    history = DTCCodeHistory.query.filter_by(vin_id=vehicle.id).order_by(DTCCodeHistory.created_at.desc()).all()
    return jsonify({
        "vin": vin,
        "dtc_history": [
            {"dtc_code": d.dtc_code, "created_at": d.created_at.isoformat()} for d in history
        ]
    }), 200


@app.route("/api/device_offline/<int:device_id>", methods=["POST"])
def device_offline(device_id):
    """
    Nastaví zariadenie na OFFLINE stav manuálne.
    --- 
    tags:
      - Device
    """
    try:
        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": "Device not found"}), 404

        device.status = False
        db.session.commit()
        return jsonify({
            "status": "success",
            "device_id": device_id,
            "message": "Device set to offline"
        }), 200

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
            "brand": data.get("brand"),
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
    Zobrazí všetky VIN a priradené DTC kódy (z aktívnych aj historických tabuliek).
    """
    try:
        vehicles = Vehicle.query.all()
        data = []
        for v in vehicles:
            data.append({
                "vin": v.vin,
                "dtc_codes_active": [d.dtc_code for d in v.dtcs_active],
                "dtc_codes_history": [d.dtc_code for d in v.dtcs_history]
            })
        return jsonify(data), 200
    except Exception as e:
        print("❌ SHOW ALL ERROR:", e)
        return jsonify({"error": str(e)}), 500



if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
