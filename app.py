from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flasgger import Swagger
from datetime import datetime, timedelta
import os
import requests
from flask_cors import CORS
import csv
from io import StringIO
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity

# ✅ NEW: WebSocket (Socket.IO)
from flask_socketio import SocketIO, emit, join_room
import eventlet
eventlet.monkey_patch()

app = Flask(__name__)
CORS(app, origins=[
    "https://car-diagnostics-frontend.onrender.com",  # frontend
    "https://car-diagnostics.onrender.com",           # backend (pre Swagger)
    "http://localhost:5000",
    "http://localhost:3000"  # ak používaš React lokálne
])
@app.before_request
def ensure_json_content_type():
    # Pre POST, PUT, PATCH endpointy vyžaduj JSON
    if request.method in ['POST', 'PUT', 'PATCH']:
        # Ak je to OPTIONS request, preskoč (pre CORS)
        if request.method == 'OPTIONS':
            return
            
        # Ak request nemá Content-Type: application/json
        if not request.is_json:
            return jsonify({
                "error": "Content-Type must be application/json",
                "detail": "Please set Content-Type header to 'application/json'"
            }), 415

from flask import make_response


@app.before_request
def log_request_info():
    print(f"Request: {request.method} {request.path}")
    print(f"Headers: {dict(request.headers)}")
    if request.is_json:
        print(f"JSON: {request.get_json()}")




@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# ✅ Swagger konfigurácia
app.config['SWAGGER'] = {
    'title': 'Inteligentná diagnostika API',
    'uiversion': 3,
    'openapi': '3.0.2',
    'doc_expansion': 'list',
    'description': '''
        API pre bakalársku prácu - diagnostika vozidiel
        
        ## Testovanie cez Postman
        
        Všetky endpointy je možné testovať v Postmane podľa nasledujúcich príkladov:
        
        ### 🔐 Autentifikácia
        1. Zaregistruj sa: `POST /api/register`
        2. Prihlás sa: `POST /api/login` → získaš JWT token
        3. Pre autorizované endpointy pridaj header: `Authorization: Bearer <token>`
        
        ### 📱 Zariadenia
        - Pridanie zariadenia: `POST /api/add-device` s JSON body `{"device_id": 12345}`
        - Zoznam mojich zariadení: `GET /api/my-devices`
        - Diagnostika: `GET /api/device/12345/diagnostics`
        
        ### 🔄 Komunikácia s RPi
        - Heartbeat: `POST /api/heartbeat` s JSON `{"device_id": 12345}`
        - Trigger príkazu: `POST /api/trigger` s JSON `{"device_id": 12345, "command": "GET_VIN"}`
        - CAN packet: `POST /api/can` s JSON `{"device_id": 12345, "vin": "1HGCM82633A123456"}`
    ''',
    'version': '1.0.0',
    'termsOfService': '',
    'contact': {
        'name': 'Jozef Kušnier',
        'email': '120957@stuba.sk'
    },
    'license': {
        'name': 'MIT'
    },
    'servers': [
        {
            'url': 'https://car-diagnostics.onrender.com',  # 🔥 tvoja Render URL
            'description': 'Render server'
        },
        {
            'url': 'http://localhost:5000',  
            'description': 'Local development'
        }
    ],
    'components': {
        'securitySchemes': {
            'bearerAuth': {
                'type': 'http',
                'scheme': 'bearer',
                'bearerFormat': 'JWT'
            }
        }
    }
}

swagger = Swagger(app, template=app.config['SWAGGER'])
app.config['SWAGGER_UI_DOC_EXPANSION'] = 'list'
app.config['SWAGGER_UI_OPERATION_ID'] = True
app.config['SWAGGER_UI_REQUEST_DURATION'] = True
app.config['SWAGGER_UI_TRY_IT_OUT'] = False  # 🔥 TOTO JE TO NAJDÔLEŽITEJŠIE!
# ✅ NEW: Socket.IO init (WS)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet"
)

# Konfigurácia JWT
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "your-secret-key")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)
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

# =========================
# MODELY DB
# =========================
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
    severity = db.Column(db.String(20), default="medium")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DTCCodeHistory(db.Model):
    __tablename__ = "dtc_codes_history"
    id = db.Column(db.Integer, primary_key=True)
    vin_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)
    severity = db.Column(db.String(20), default="medium")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PendingCommand(db.Model):
    __tablename__ = "pending_commands"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False)
    command = db.Column(db.String(50), nullable=False)
    executed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DtcCodeMeaning(db.Model):
    __tablename__ = "dtc_codes_meaning"
    id = db.Column(db.Integer, primary_key=True)
    dtc_code = db.Column(db.String(20), unique=True, nullable=False)
    dtc_description = db.Column(db.Text, nullable=True)

class DtcPattern(db.Model):
    __tablename__ = "dtc_patterns"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    primary_cause = db.Column(db.String(255), nullable=False)
    confidence = db.Column(db.Integer, default=80)
    source_url = db.Column(db.Text, nullable=True)  # 🔥 NOVÉ

class DtcPatternLink(db.Model):
    __tablename__ = "dtc_pattern_links"
    id = db.Column(db.Integer, primary_key=True)
    pattern_id = db.Column(db.Integer, db.ForeignKey("dtc_patterns.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)

class VehicleTelemetry(db.Model):  # 🔥 Zmena názvu
    __tablename__ = "vehicle_telemetry"  # 🔥 Nový názov tabuľky
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)  # 🔥 Zmena z device_id
    vehicle = db.relationship("Vehicle", backref=db.backref("telemetry", lazy="dynamic"))  # 🔥 Vzťah

    odometer = db.Column(db.Integer, nullable=True)

    battery_voltage = db.Column(db.Float, nullable=True)
    battery_health = db.Column(db.String(30), nullable=True)

    engine_running = db.Column(db.Boolean, nullable=True)
    engine_rpm = db.Column(db.Integer, nullable=True)
    engine_load = db.Column(db.Float, nullable=True)
    coolant_temp = db.Column(db.Integer, nullable=True)
    oil_temp = db.Column(db.Integer, nullable=True)
    intake_air_temp = db.Column(db.Integer, nullable=True)

    consumption_lh = db.Column(db.Float, nullable=True)
    consumption_l100km = db.Column(db.Float, nullable=True)
    maf = db.Column(db.Float, nullable=True)
    fuel_type = db.Column(db.String(20), nullable=True)

    speed = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# =========================
# INIT / HEALTH
# =========================
@app.route("/init-db")
def init_db():
    """
    Inicializácia databázy
    ---
    tags:
      - System
    description: |
      Vytvorí všetky tabuľky v databáze podľa definovaných modelov.
      
      **Testovanie cez Postman:**
      - Metóda: `GET`
      - URL: `http://car-diagnostics.onrender.com/init-db`
      - Headers: žiadne
      - Body: žiadne
    responses:
      200:
        description: Databáza úspešne vytvorená
        schema:
          type: object
          properties:
            status:
              type: string
              example: "Database ok"
    """
    db.create_all()
    return jsonify({"status": "Database ok"})

@app.route("/", methods=["GET"])
def home():
    """
    Health check endpoint
    ---
    tags:
      - System
    description: |
      Overenie, či server beží.
      
      **Testovanie cez Postman:**
      - Metóda: `GET`
      - URL: `http://car-diagnostics.onrender.com/`
      - Headers: žiadne
      - Body: žiadne
      
      **Očakávaná odpoveď:**
      ```json
      {
        "status": "ok",
        "message": "Flask bezi"
      }
      ```
    responses:
      200:
        description: Server beží
        schema:
          type: object
          properties:
            status:
              type: string
              example: "ok"
            message:
              type: string
              example: "Flask bezi"
    """
    return jsonify({"status": "ok", "message": "Flask bezi"})

# =========================
# SEVERITY
# =========================
CRITICAL_KEYWORDS = [
    "misfire", "stall", "overheat", "knock", "no start",
    "oil pressure", "detonation", "shaft", "timing",
    "crank", "camshaft", "failure", "shutdown"
]

LOW_KEYWORDS = [
    "lamp", "light", "interior", "seat", "mirror",
    "window", "audio", "radio", "speaker", "door",
    "sensor circuit low", "cosmetic"
]

def detect_severity_from_description(description: str) -> str:
    if not description:
        return "medium"
    text = description.lower()
    for word in CRITICAL_KEYWORDS:
        if word in text:
            return "critical"
    for word in LOW_KEYWORDS:
        if word in text:
            return "low"
    return "medium"

# =========================
# ✅ NEW: SOCKET.IO HELPERS
# =========================
def _iso(ts: datetime | None) -> str | None:
    if not ts:
        return None
    return ts.replace(microsecond=0).isoformat() + "Z"

def _telemetry_payload(device_id: int, payload: dict) -> dict:
    # jednotný formát pre FE
    return {
        "device_id": device_id,
        "odometer": payload.get("odometer"),
        "battery": payload.get("battery"),
        "engine": payload.get("engine"),
        "fuel": payload.get("fuel"),
        "speed": payload.get("speed"),
        "timestamp": payload.get("timestamp") or _iso(datetime.utcnow()),
    }

def _save_telemetry_to_db(device_id: int, t: dict) -> None:
    """Uloží telemetriu do DB - nájde vehicle_id cez DeviceVehicle"""
    try:
        # 🔥 Získať vehicle_id z DeviceVehicle
        device_vehicle = DeviceVehicle.query.filter_by(device_id=device_id).first()
        if not device_vehicle or not device_vehicle.last_vin_id:
            print(f"❌ No vehicle associated with device {device_id}")
            return

        vehicle_id = device_vehicle.last_vin_id
        
        battery = t.get("battery") or {}
        engine = t.get("engine") or {}
        fuel = t.get("fuel") or {}

        row = VehicleTelemetry(  # 🔥 Použi nový model
            vehicle_id=vehicle_id,  # 🔥 Ukladáme vehicle_id, nie device_id
            odometer=t.get("odometer"),
            battery_voltage=battery.get("battery_voltage"),
            battery_health=battery.get("health"),
            engine_running=engine.get("running"),
            engine_rpm=engine.get("rpm"),
            engine_load=engine.get("load"),
            coolant_temp=engine.get("coolant_temp"),
            oil_temp=engine.get("oil_temp"),
            intake_air_temp=engine.get("intake_air_temp"),
            consumption_lh=fuel.get("consumption_lh"),
            consumption_l100km=fuel.get("consumption_l100km"),
            maf=fuel.get("maf"),
            fuel_type=fuel.get("type"),
            speed=t.get("speed"),
            created_at=datetime.utcnow(),
        )
        db.session.add(row)
        db.session.commit()
        print(f"✅ Telemetry saved for vehicle_id: {vehicle_id}")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error saving telemetry: {e}")
# =========================
# ✅ NEW: SOCKET.IO EVENTS
# =========================
@socketio.on("connect")
def ws_connect():
    emit("server_ready", {"status": "ok"})

@socketio.on("subscribe_device")
def ws_subscribe_device(data):
    """
    FE: socket.emit("subscribe_device", {device_id: 1})
    """
    try:
        device_id = int(data.get("device_id"))
    except Exception:
        emit("error", {"error": "invalid device_id"})
        return

    join_room(f"device:{device_id}")
    emit("subscribed", {"device_id": device_id})

@socketio.on("telemetry")
def ws_telemetry(data):
    """
    RPi (alebo test client) posiela realtime telemetry cez WS.
    Server to uloží + pushne FE.
    """
    try:
        device_id = int(data.get("device_id"))
    except Exception:
        emit("error", {"error": "missing/invalid device_id"})
        return

    device = Device.query.get(device_id)
    if not device:
        emit("error", {"error": "device not found"})
        return

    # realtime payload
    t = _telemetry_payload(device_id, data)

    # mark device online (keď posiela telemetry, je online)
    try:
        device.status = True
        db.session.commit()
    except Exception:
        db.session.rollback()

    # uloz do DB (ak chces)
    _save_telemetry_to_db(device_id, t)

    # PUSH do FE
    socketio.emit("telemetry_update", t, room=f"device:{device_id}")
    emit("telemetry_ack", {"ok": True, "timestamp": t["timestamp"]})

# =========================
# 3-WAY HANDSHAKE
# =========================
@app.route("/api/connect", methods=["POST"])
def device_connect_syn():
    """
    3-way handshake - SYN
    ---
    tags:
      - Device Communication
    description: |
      Prvý krok trojcestného handshaku pri pripájaní zariadenia.
      
      **Testovanie cez Postman:**
      - Metóda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/connect`
      - Headers: `Content-Type: application/json`
      - Body (raw JSON):
        ```json
        {
          "device_id": 12345
        }
        ```
      
      **Očakávaná odpoveď:**
      ```json
      {
        "handshake": "SYN-ACK",
        "device_id": 12345
      }
      ```
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            device_id:
              type: integer
              example: 12345
    responses:
      200:
        description: SYN-ACK odpoveď
        schema:
          type: object
          properties:
            handshake:
              type: string
              example: "SYN-ACK"
            device_id:
              type: integer
      400:
        description: Chýba device_id
      500:
        description: Server error
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
            device.status = False

        db.session.commit()

        return jsonify({"handshake": "SYN-ACK", "device_id": device_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/connect/ack", methods=["POST"])
def device_connect_ack():
    """
    3-way handshake - ACK
    ---
    tags:
      - Device Communication
    description: |
      Druhý krok trojcestného handshaku - potvrdenie pripojenia.
      
      **Testovanie cez Postman:**
      - Metóda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/connect/ack`
      - Headers: `Content-Type: application/json`
      - Body (raw JSON):
        ```json
        {
          "device_id": 12345
        }
        ```
      
      **Očakávaná odpoveď:**
      ```json
      {
        "status": "online",
        "device_id": 12345,
        "handshake": "ACK-complete"
      }
      ```
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            device_id:
              type: integer
              example: 12345
    responses:
      200:
        description: Pripojenie dokončené
        schema:
          type: object
          properties:
            status:
              type: string
              example: "online"
            device_id:
              type: integer
            handshake:
              type: string
              example: "ACK-complete"
      400:
        description: Chýba device_id
      404:
        description: Device not found
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")

        if not device_id:
            return jsonify({"error": "missing device_id"}), 400

        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": "device not found"}), 404

        device.status = True
        db.session.commit()

        return jsonify({"status": "online", "device_id": device_id, "handshake": "ACK-complete"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================
# PATTERN CHECK
# =========================
@app.route("/api/dtc/pattern-check/<vin>", methods=["GET"])
@jwt_required(optional=True)
def check_dtc_patterns(vin):
    vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
    if not vehicle:
        return jsonify({"error": "Vehicle not found"}), 404

    active_dtcs = set(d.dtc_code.upper() for d in DTCCodeActive.query.filter_by(vin_id=vehicle.id))

    if not active_dtcs:
        return jsonify({
            "vin": vin,
            "active_dtcs": [],
            "matched_patterns": [],
            "message": "Vehicle has no active DTC codes"
        }), 200

    matched_patterns = []
    patterns = DtcPattern.query.all()

    for pattern in patterns:
        pattern_codes = set(
            l.dtc_code.upper()
            for l in DtcPatternLink.query.filter_by(pattern_id=pattern.id)
        )
        if pattern_codes.issubset(active_dtcs):
            matched_patterns.append({
                "pattern_id": pattern.id,
                "pattern_name": pattern.name,
                "primary_cause": pattern.primary_cause,
                "confidence": pattern.confidence,
                "required_codes": list(pattern_codes),
                "vehicle_codes": list(active_dtcs)
            })

    return jsonify({
        "vin": vin,
        "active_dtc_codes": list(active_dtcs),
        "matched_patterns": matched_patterns
    }), 200

# =========================
# CLEAR / READ DTC (pending commands)
# =========================
@app.route("/api/device/<int:device_id>/clear-dtcs", methods=["POST"])
@jwt_required()
def clear_device_dtcs(device_id):
    """
    Odoslanie príkazu na vymazanie DTC kódov
    ---
    tags:
      - DTC
    security:
      - bearerAuth: []
    description: |
      Odošle príkaz na vymazanie DTC kódov pre konkrétne zariadenie.
      
      **Testovanie cez Postman:**
      - Metóda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/device/12345/clear-dtcs`
      - Headers: 
        - `Content-Type: application/json`
        - `Authorization: Bearer <token>`
      - Body: žiadne
      
      **Očakávaná odpoveď:**
      ```json
      {
        "status": "waiting",
        "message": "Clear command sent to device. Waiting for RPi confirmation."
      }
      ```
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Príkaz odoslaný
        schema:
          type: object
          properties:
            status:
              type: string
              example: "waiting"
            message:
              type: string
      404:
        description: Device not found
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        if user.role == "admin":
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()

        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404

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

@app.route("/api/device/<int:device_id>/read-dtcs", methods=["POST"])
@jwt_required()
def read_device_dtcs(device_id):
    """
    Odoslanie príkazu na načítanie DTC kódov
    ---
    tags:
      - DTC
    security:
      - bearerAuth: []
    description: |
      Odošle príkaz na načítanie aktuálnych DTC kódov.
      
      **Testovanie cez Postman:**
      - Metóda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/device/12345/read-dtcs`
      - Headers: 
        - `Content-Type: application/json`
        - `Authorization: Bearer <token>`
      - Body: žiadne
      
      **Očakávaná odpoveď:**
      ```json
      {
        "status": "success",
        "message": "Read DTC command sent to device",
        "device_id": 12345,
        "command": "GET_DTCS_PERM"
      }
      ```
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Príkaz odoslaný
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            message:
              type: string
            device_id:
              type: integer
            command:
              type: string
      404:
        description: Device not found
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        if user.role == "admin":
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()

        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404

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

# =========================
# DTC HISTORY FULL
# =========================
@app.route("/api/dtc-history-full", methods=["POST"])
@jwt_required(optional=True)
def dtc_history_full():
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

        results = [{
            "dtc_code": h.dtc_code,
            "description": h.dtc_description or "No description available",
            "created_at": h.created_at.isoformat()
        } for h in history]

        return jsonify({"status": "success", "vin": vin.upper(), "history": results}), 200

    except Exception as e:
        print("❌ DTC HISTORY FULL ERROR:", e)
        return jsonify({"error": str(e)}), 500

# =========================
# VIN decode NHTSA
# =========================
@app.route("/api/vin/nhtsa", methods=["POST"])
def decode_vin_nhtsa():
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


# =========================
# DELETE DEVICE
# =========================
@app.route("/api/device/<int:device_id>", methods=["DELETE"])
@jwt_required()
def delete_device(device_id):
    """
    Odstránenie zariadenia
    ---
    tags:
      - Devices
    security:
      - bearerAuth: []
    description: |
        Odstráni zariadenie a všetky súvisiace dáta.
        
        **Testovanie cez Postman:**
        - Metóda: `DELETE`
        - URL: `http://car-diagnostics.onrender.com/api/device/12345`
        - Headers: `Authorization: Bearer <token>`
        
        **Očakávaná odpoveď:**
        ```json
        {
          "status": "success",
          "message": "Device 12345 and all related data deleted successfully"
        }
        ```
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Zariadenie odstránené
      403:
        description: Nemáte oprávnenie odstrániť toto zariadenie
      404:
        description: Zariadenie neexistuje
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({"error": "User not found"}), 404

        # Nájsť zariadenie
        if user.role == "admin":
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()

        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404

        # Odstrániť všetky súvisiace záznamy
        try:
            # 1. Odstrániť DeviceVehicle
            DeviceVehicle.query.filter_by(device_id=device_id).delete()
            
            # 2. Odstrániť PendingCommands
            PendingCommand.query.filter_by(device_id=device_id).delete()
            
            
            # 4. Odstrániť samotné zariadenie
            db.session.delete(device)
            
            db.session.commit()
            
            return jsonify({
                "status": "success",
                "message": f"Device {device_id} and all related data deleted successfully"
            }), 200
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error deleting device data: {e}")
            return jsonify({"error": "Failed to delete device data"}), 500

    except Exception as e:
        print("❌ DELETE DEVICE ERROR:", e)
        return jsonify({"error": str(e)}), 500
# =========================
# ADD DEVICE
# =========================
@app.route("/api/add-device", methods=["POST"])
@jwt_required()
def add_device():
    """
    Pridanie nového zariadenia
    ---
    tags:
      - Devices
    security:
      - bearerAuth: []
    description: |
      Pridá nové diagnostické zariadenie a priradí ho používateľovi.
      
      **Testovanie cez Postman:**
      - Metóda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/add-device`
      - Headers: 
        - `Content-Type: application/json`
        - `Authorization: Bearer <token>`
      - Body (raw JSON):
        ```json
        {
          "device_id": 12345
        }
        ```
      
      Pre admina je možné priradiť zariadenie inému používateľovi:
      ```json
      {
        "device_id": 12345,
        "user_id": 2
      }
      ```
      
      **Očakávaná odpoveď:**
      ```json
      {
        "status": "success",
        "device_id": 12345,
        "assigned_to": 1,
        "message": "Device 12345 assigned to user 1"
      }
      ```
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            device_id:
              type: integer
              example: 12345
            user_id:
              type: integer
              description: Len pre admina
              example: 1
    responses:
      201:
        description: Zariadenie pridané
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            device_id:
              type: integer
            assigned_to:
              type: integer
            message:
              type: string
      400:
        description: Chybný request
      401:
        description: Neautorizovaný
      409:
        description: Zariadenie už existuje
      500:
        description: Server error
    """
    try:
        payload = request.get_json()
        device_id_raw = payload.get("device_id")
        target_user_id = payload.get("user_id")
        current_user_id = int(get_jwt_identity())
        current_user = User.query.get(current_user_id)

        if not current_user:
            return jsonify({"error": "User not found"}), 404

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

# =========================
# DEVICE DIAGNOSTICS
# =========================
@app.route("/api/device/<int:device_id>/diagnostics", methods=["GET"])
@jwt_required()
def device_diagnostics(device_id):
    """
    Získanie diagnostických údajov pre zariadenie
    ---
    tags:
      - Devices
    security:
      - bearerAuth: []
    description: |
      Vráti kompletné diagnostické informácie pre zariadenie vrátane VIN a DTC kódov.
      
      **Testovanie cez Postman:**
      - Metóda: `GET`
      - URL: `http://car-diagnostics.onrender.com/api/device/12345/diagnostics`
      - Headers: 
        - `Authorization: Bearer <token>`
      - Body: žiadne
      
      **Očakávaná odpoveď:**
      ```json
      {
        "status": "success",
        "device_id": 12345,
        "vin": "1HGCM82633A123456",
        "brand": "Honda",
        "year": "2021",
        "model": "Accord",
        "engine": "2.0L",
        "dtc_codes": [
          {
            "dtc_code": "P0300",
            "description": "Random/Multiple Cylinder Misfire Detected",
            "severity": "critical",
            "created_at": "2025-02-15T10:30:00"
          }
        ],
        "online": true
      }
      ```
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Diagnostické údaje
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            device_id:
              type: integer
            vin:
              type: string
            brand:
              type: string
            year:
              type: string
            model:
              type: string
            engine:
              type: string
            dtc_codes:
              type: array
            online:
              type: boolean
      404:
        description: Device not found
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        if user.role == "admin":
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()

        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404

        vin = year = brand = model = engine = None
        dtcs = []

        if device.link and len(device.link) > 0 and device.link[0].last_vin_id:
            vin_obj = Vehicle.query.get(device.link[0].last_vin_id)
            if vin_obj:
                vin = vin_obj.vin
                brand = vin_obj.brand
                year = vin_obj.year
                model = vin_obj.model
                engine = vin_obj.engine

                dtcs_query = (
                    db.session.query(
                        DTCCodeActive.dtc_code,
                        DTCCodeActive.created_at,
                        DTCCodeActive.severity,
                        DtcCodeMeaning.dtc_description
                    )
                    .outerjoin(DtcCodeMeaning, DTCCodeActive.dtc_code == DtcCodeMeaning.dtc_code)
                    .filter(DTCCodeActive.vin_id == vin_obj.id)
                    .order_by(DTCCodeActive.created_at.desc())
                    .all()
                )

                dtcs = [{
                    "dtc_code": d.dtc_code,
                    "description": d.dtc_description or "No description",
                    "severity": d.severity or "medium",
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                } for d in dtcs_query]

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

# =========================
# MY DEVICES
# =========================
@app.route("/api/my-devices", methods=["GET"])
@jwt_required()
def my_devices():
    """
    Zoznam zariadení prihláseného používateľa
    ---
    tags:
      - Devices
    security:
      - bearerAuth: []
    description: |
      Vráti zoznam všetkých zariadení patriacich prihlásenému používateľovi.
      
      **Testovanie cez Postman:**
      - Metóda: `GET`
      - URL: `http://car-diagnostics.onrender.com/api/my-devices`
      - Headers: 
        - `Authorization: Bearer <token>`
      - Body: žiadne
      
      **Očakávaná odpoveď:**
      ```json
      {
        "status": "success",
        "devices": [
          {
            "device_id": 12345,
            "vin": "1HGCM82633A123456",
            "status": "Online",
            "user_id": 1
          }
        ]
      }
      ```
    responses:
      200:
        description: Zoznam zariadení
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            devices:
              type: array
              items:
                type: object
                properties:
                  device_id:
                    type: integer
                  vin:
                    type: string
                  status:
                    type: string
                  user_id:
                    type: integer
      404:
        description: User not found
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

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
                "user_id": d.user_id
            })

        return jsonify({"status": "success", "devices": result}), 200

    except Exception as e:
        print("❌ MY DEVICES ERROR:", e)
        return jsonify({"error": str(e)}), 500

# =========================
# LOGIN / REGISTER
# =========================
@app.route("/api/login", methods=["POST"])
def login():
    """
    Prihlásenie používateľa
    ---
    tags:
      - Authentication
    consumes:
      - application/json
    produces:
      - application/json
      
    description: |
      Prihlási používateľa a vráti JWT token.
      
      **Testovanie cez Postman:**
      - Metóda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/login`
      - Headers: `Content-Type: application/json`
      - Body (raw JSON):
        ```json
        {
          "email": "admin@admin.com",
          "password": "admin"
        }
        ```
      
      **Očakávaná odpoveď:**
      ```json
      {
        "status": "success",
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "role": "user"
      }
      ```
      
      Token potom použiješ v ďalších requestoch ako Bearer token.
    parameters:
      - in: body
        name: body
        description: Prihlasovacie údaje
        required: true
        schema:
          type: object
          properties:
            email:
              type: string
              example: "admin@admin.com"
            password:
              type: string
              example: "admin"
    responses:
      200:
        description: Úspešné prihlásenie
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            access_token:
              type: string
            role:
              type: string
      400:
        description: Chýbajúce údaje
      401:
        description: Nesprávne prihlasovacie údaje
      415:
        description: Content-Type must be application/json
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

        access_token = create_access_token(identity=str(user.id))
        return jsonify({
            "status": "success",
            "access_token": access_token,
            "role": user.role
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/register", methods=["POST"])
def register():
    """
    Registrácia nového používateľa
    ---
    tags:
      - Authentication
    description: |
      Zaregistruje nového používateľa.
      
      **Testovanie cez Postman:**
      - Metóda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/register`
      - Headers: `Content-Type: application/json`
      - Body (raw JSON):
        ```json
        {
          "email": "user@example.com",
          "password": "heslo123"
        }
        ```
      
      **Očakávaná odpoveď:**
      ```json
      {
        "status": "success",
        "message": "User registered"
      }
      ```
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
              example: "heslo123"
    responses:
      201:
        description: Používateľ zaregistrovaný
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            message:
              type: string
      400:
        description: Chýbajúce údaje
      409:
        description: Email už existuje
      500:
        description: Server error
    """
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

# =========================
# LOAD DTC CSV
# =========================
@app.route("/api/load-dtc-codes", methods=["POST"])
def load_dtc_codes_from_csv():
    try:
        payload = request.get_json()
        csv_url = payload.get("csv_url")

        if not csv_url:
            return jsonify({"error": "Missing 'csv_url' parameter"}), 400

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

            if DtcCodeMeaning.query.filter_by(dtc_code=dtc_code).first():
                skipped += 1
                continue

            db.session.add(DtcCodeMeaning(dtc_code=dtc_code, dtc_description=dtc_description))
            inserted += 1

        db.session.commit()

        return jsonify({"status": "success", "inserted": inserted, "skipped_existing": skipped}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/dtc-description", methods=["POST"])
def get_dtc_description():
    try:
        payload = request.get_json()
        dtc_code = payload.get("dtc_code")

        if not dtc_code:
            return jsonify({"error": "Missing 'dtc_code' parameter"}), 400

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

# =========================
# HEARTBEAT / TRIGGER
# =========================
@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """
    Heartbeat od RPi (keep-alive + command polling)
    ---
    tags:
      - Device Communication
    description: |
      Endpoint pre pravidelné heartbeat requesty z RPi.
      Udržuje zariadenie online a vracia čakajúce príkazy.
      
      **Testovanie cez Postman:**
      - Metóda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/heartbeat`
      - Headers: `Content-Type: application/json`
      - Body (raw JSON):
        ```json
        {
          "device_id": 12345
        }
        ```
      
      **Očakávaná odpoveď (žiadny príkaz):**
      ```json
      {
        "status": "ok"
      }
      ```
      
      **Očakávaná odpoveď (s príkazom):**
      ```json
      {
        "command": "GET_VIN"
      }
      ```
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            device_id:
              type: integer
              example: 12345
    responses:
      200:
        description: Heartbeat OK alebo command
        schema:
          type: object
          properties:
            status:
              type: string
              example: "ok"
            command:
              type: string
              example: "GET_VIN"
      400:
        description: Chýba device_id
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")
        if not device_id:
            return jsonify({"error": "missing device_id"}), 400

        device = Device.query.get(device_id)
        if not device:
            device = Device(id=device_id, status=True)
            db.session.add(device)
        else:
            device.status = True
        db.session.commit()

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
    Manuálne spustenie príkazu na zariadení
    ---
    tags:
      - Device Communication
    description: |
      Odošle príkaz do fronty pre konkrétne zariadenie.
      Zariadenie si ho vyzdvihne pri najbližšom heartbeat.
      
      **Testovanie cez Postman:**
      - Metóda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/trigger`
      - Headers: `Content-Type: application/json`
      - Body (raw JSON):
        ```json
        {
          "device_id": 12345,
          "command": "GET_VIN"
        }
        ```
      
      **Dostupné príkazy:**
      - `GET_VIN` - načítanie VIN čísla
      - `GET_DTCS_PERM` - načítanie aktívnych DTC kódov
      - `GET_DTCS_PEND` - načítanie pending DTC kódov
      - `GET_RPM` - načítanie otáčok motora
      - `GET_TEMP` - načítanie teploty
      - `CLEAR_DTCS` - vymazanie DTC kódov
      
      **Očakávaná odpoveď:**
      ```json
      {
        "status": "queued",
        "command": "GET_VIN"
      }
      ```
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            device_id:
              type: integer
              example: 12345
            command:
              type: string
              enum: [GET_VIN, GET_DTCS_PERM, GET_DTCS_PEND, GET_RPM, GET_TEMP, CLEAR_DTCS]
              example: "GET_VIN"
    responses:
      200:
        description: Príkaz zaradený do fronty
        schema:
          type: object
          properties:
            status:
              type: string
              example: "queued"
            command:
              type: string
      400:
        description: Neplatný command
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")
        command = data.get("command")

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

# =========================
# CAN endpoint (VIN/DTC/CLEAR) + ✅ NEW: TELEMETRY via REST also pushes WS
# =========================
@app.route("/api/can", methods=["POST"])
def receive_can_packet():
    """
    Príjem CAN packetov z RPi (VIN, DTC, clear_status, telemetria)
    ---
    tags:
      - Device Communication
    description: |
      Hlavný endpoint pre príjem dát z RPi zariadenia.
      
      **Typy správ:**
      
      **1. Odoslanie VIN:**
      ```json
      {
        "device_id": 12345,
        "vin": "1HGCM82633A123456",
        "year": "2021",
        "brand": "Honda",
        "model": "Accord",
        "engine": "2.0L"
      }
      ```
      
      **2. Odoslanie DTC kódu:**
      ```json
      {
        "device_id": 12345,
        "dtc_code": "P0300"
      }
      ```
      
      **3. Potvrdenie vymazania DTC:**
      ```json
      {
        "device_id": 12345,
        "clear_status": "ok"
      }
      ```
      
      **4. Odoslanie telemetrie:**
      ```json
      {
        "device_id": 12345,
        "odometer": 123456,
        "speed": 80,
        "battery": {
          "battery_voltage": 12.6,
          "health": "good"
        },
        "engine": {
          "running": true,
          "rpm": 2500,
          "load": 45.5,
          "coolant_temp": 90
        },
        "fuel": {
          "consumption_lh": 2.5,
          "consumption_l100km": 8.2,
          "type": "gasoline"
        }
      }
      ```
      
      **Očakávané odpovede:**
      - Pre VIN: `{"status": "VIN stored", "vin": "1HGCM82633A123456", ...}`
      - Pre DTC: `{"status": "DTC stored", "vin": "...", "dtc": "P0300", "severity": "critical"}`
      - Pre clear: `{"status": "DTC cleared", "vin_id": 1}`
      - Pre telemetriu: `{"status": "telemetry stored", "device_id": 12345, "timestamp": "2025-02-15T10:30:00Z"}`
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            device_id:
              type: integer
              example: 12345
            vin:
              type: string
              example: "1HGCM82633A123456"
            dtc_code:
              type: string
              example: "P0300"
            clear_status:
              type: string
              enum: [ok, failed]
            odometer:
              type: integer
            battery:
              type: object
            engine:
              type: object
            fuel:
              type: object
            speed:
              type: integer
    responses:
      201:
        description: Dáta spracované
      200:
        description: OK
      400:
        description: Chybný request
      404:
        description: Device not found
      500:
        description: Server error
    """
    try:
        payload = request.get_json()
        device_id = payload.get("device_id")
        vin = payload.get("vin")
        dtc_code = payload.get("dtc_code")
        clear_status = payload.get("clear_status")

        # extra vehicle info
        year = payload.get("year")
        model = payload.get("model")
        brand = payload.get("brand")
        engine = payload.get("engine")

        if device_id is None:
            return jsonify({"error": "Missing 'device_id'"}), 400

        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": f"Device {device_id} not found"}), 404

        # ✅ NEW: TELEMETRY via REST
        # RPi môže poslať telemetry v tom istom /api/can payload-e:
        # odometer, battery{..}, engine{..}, fuel{..}, speed
        # V receive_can_packet funkcii, v telemetry časti:
        if any(k in payload for k in ["odometer", "battery", "engine", "fuel", "speed"]) and not payload.get("vin"):
            # 🔥 Skontroluj či máme VIN
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({
                    "error": "No VIN associated with this device",
                    "message": "Please send VIN first"
                }), 400
                
            t = _telemetry_payload(int(device_id), payload)
            device.status = True
            db.session.commit()
        
            _save_telemetry_to_db(int(device_id), t)  # Tvoja upravená funkcia
            socketio.emit("telemetry_update", t, room=f"device:{int(device_id)}")
        
            return jsonify({
                "status": "telemetry stored", 
                "device_id": int(device_id),
                "vehicle_id": state.last_vin_id,  # 🔥 Pridaj vehicle_id do odpovede
                "timestamp": t["timestamp"]
            }), 201
        # 1) CLEAR_DTCS confirmation
        if clear_status is not None:
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated for clear"}), 400

            if clear_status == "ok":
                DTCCodeActive.query.filter_by(vin_id=state.last_vin_id).delete()
                db.session.commit()
                return jsonify({"status": "DTC cleared", "vin_id": state.last_vin_id}), 200

            return jsonify({"status": "Clear failed"}), 200

        # 2) VIN
        if vin:
            vin = vin.strip().upper()
            if len(vin) != 17:
                return jsonify({"error": "VIN must be 17 characters"}), 400

            vehicle = Vehicle.query.filter_by(vin=vin).first()
            if not vehicle:
                vehicle = Vehicle(vin=vin)
                if hasattr(Vehicle, "year") and year:
                    vehicle.year = year
                if hasattr(Vehicle, "brand") and brand:
                    vehicle.brand = brand
                if hasattr(Vehicle, "model") and model:
                    vehicle.model = model
                if hasattr(Vehicle, "engine") and engine:
                    vehicle.engine = engine
                db.session.add(vehicle)
                db.session.commit()
            else:
                updated = False
                if hasattr(Vehicle, "year") and year and vehicle.year != year:
                    vehicle.year = year
                    updated = True
                if hasattr(Vehicle, "brand") and brand and vehicle.brand != brand:
                    vehicle.brand = brand
                    updated = True
                if hasattr(Vehicle, "model") and model and vehicle.model != model:
                    vehicle.model = model
                    updated = True
                if hasattr(Vehicle, "engine") and engine and vehicle.engine != engine:
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
                "brand": brand,
                "year": vehicle.year,
                "model": vehicle.model,
                "engine": vehicle.engine
            }), 201

        # 3) DTC
        if dtc_code:
            dtc_code = dtc_code.strip().upper()

            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated with this device"}), 400

            vehicle = Vehicle.query.get(state.last_vin_id)
            if not vehicle:
                return jsonify({"error": "Vehicle not found"}), 404

            meaning = DtcCodeMeaning.query.filter(
                db.func.lower(DtcCodeMeaning.dtc_code) == dtc_code.lower()
            ).first()

            description = meaning.dtc_description if meaning else ""
            severity = detect_severity_from_description(description)

            db.session.add(DTCCodeHistory(vin_id=vehicle.id, dtc_code=dtc_code, severity=severity))

            DTCCodeActive.query.filter_by(vin_id=vehicle.id, dtc_code=dtc_code).delete()
            db.session.add(DTCCodeActive(vin_id=vehicle.id, dtc_code=dtc_code, severity=severity))

            db.session.commit()

            return jsonify({"status": "DTC stored", "vin": vehicle.vin, "dtc": dtc_code, "severity": severity}), 201

        return jsonify({"status": "ignored", "message": "No recognized payload fields"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# =========================
# DTC HISTORY (simple)
# =========================
@app.route("/api/dtc-history/<vin>", methods=["GET"])
@jwt_required()
def get_dtc_history(vin):
    vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
    if not vehicle:
        return jsonify({"error": "Vehicle not found"}), 404

    history = DTCCodeHistory.query.filter_by(vin_id=vehicle.id).order_by(DTCCodeHistory.created_at.desc()).all()
    return jsonify({
        "vin": vin,
        "dtc_history": [{"dtc_code": d.dtc_code, "created_at": d.created_at.isoformat()} for d in history]
    }), 200

# =========================
# DEVICE OFFLINE
# =========================
@app.route("/api/device_offline/<int:device_id>", methods=["POST"])
def device_offline(device_id):
    try:
        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": "Device not found"}), 404

        device.status = False
        db.session.commit()
        return jsonify({"status": "success", "device_id": device_id, "message": "Device set to offline"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# =========================
# VIN decode (apiverve)
# =========================
@app.route("/api/vindecode", methods=["POST"])
def decode_vin_apiverve():
    try:
        payload = request.get_json()
        vin = payload.get("vin")

        if not vin:
            return jsonify({"error": "Missing 'vin' in body"}), 400

        api_key = os.getenv("VINDECODER_API_KEY")
        if not api_key:
            return jsonify({"error": "Missing VINDECODER_API_KEY env var on server"}), 500

        url = f"https://api.apiverve.com/v1/vindecoder?vin={vin}"
        headers = {"X-API-Key": api_key}

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            return jsonify({
                "error": "VIN decoder API error",
                "status": response.status_code,
                "details": response.text
            }), response.status_code

        data = response.json()
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

        return jsonify({"status": "success", "source": "apiverve", "data": cleaned}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================
# GET "last known telemetry" endpoints (ponechane)
# =========================
def _get_vehicle_id_from_device(device_id: int) -> int | None:
    """Pomocná funkcia na získanie vehicle_id z device_id"""
    device_vehicle = DeviceVehicle.query.filter_by(device_id=device_id).first()
    if device_vehicle and device_vehicle.last_vin_id:
        return device_vehicle.last_vin_id
    return None

def _get_latest_telemetry(device_id: int) -> VehicleTelemetry | None:  # 🔥 Zmena návratového typu
    """Získa najnovšiu telemetriu pre zariadenie (cez vehicle_id)"""
    vehicle_id = _get_vehicle_id_from_device(device_id)
    if not vehicle_id:
        return None
    
    return (
        VehicleTelemetry.query  # 🔥 Použi nový model
        .filter_by(vehicle_id=vehicle_id)
        .order_by(VehicleTelemetry.created_at.desc())
        .first()
    )

@app.route("/api/device/<int:device_id>/odometer", methods=["GET"])
@jwt_required()
def get_device_odometer(device_id):
    """
    Získanie posledného známeho stavu odometra
    """
    # Skontroluj vlastníctvo zariadenia
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    
    if user.role != "admin":
        device = Device.query.filter_by(id=device_id, user_id=user_id).first()
        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404
    
    t = _get_latest_telemetry(device_id)
    if not t or t.odometer is None:
        return jsonify({"error": "No odometer data"}), 404
    
    return jsonify({
        "status": "success", 
        "device_id": device_id,
        "vehicle_id": t.vehicle_id,  # 🔥 Pridaj vehicle_id do odpovede
        "odometer": int(t.odometer), 
        "timestamp": _iso(t.created_at)
    }), 200
@app.route("/api/device/<int:device_id>/battery", methods=["GET"])
@jwt_required()
def get_device_battery(device_id):
    # 🔥 KONTROLA VLASTNÍCTVA
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    
    if user.role != "admin":
        device = Device.query.filter_by(id=device_id, user_id=user_id).first()
        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404
    
    t = _get_latest_telemetry(device_id)
    if not t or t.battery_voltage is None:
        return jsonify({"error": "No battery data"}), 404
    
    return jsonify({
        "status": "success",
        "device_id": device_id,
        "vehicle_id": t.vehicle_id,  # 🔥 PRIDAJ vehicle_id
        "battery_voltage": float(t.battery_voltage),
        "health": t.battery_health or "unknown",
        "timestamp": _iso(t.created_at)
    }), 200


@app.route("/api/device/<int:device_id>/engine", methods=["GET"])
@jwt_required()
def get_device_engine(device_id):
    # 🔥 KONTROLA VLASTNÍCTVA
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    
    if user.role != "admin":
        device = Device.query.filter_by(id=device_id, user_id=user_id).first()
        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404
    
    t = _get_latest_telemetry(device_id)
    if not t or all(v is None for v in [t.engine_running, t.engine_rpm, t.engine_load, t.coolant_temp, t.oil_temp, t.intake_air_temp]):
        return jsonify({"error": "No engine data"}), 404
    
    return jsonify({
        "status": "success",
        "device_id": device_id,
        "vehicle_id": t.vehicle_id,  # 🔥 PRIDAJ vehicle_id
        "engine": {
            "running": bool(t.engine_running) if t.engine_running is not None else None,
            "rpm": t.engine_rpm,
            "load": t.engine_load,
            "coolant_temp": t.coolant_temp,
            "oil_temp": t.oil_temp,
            "intake_air_temp": t.intake_air_temp
        },
        "timestamp": _iso(t.created_at)
    }), 200


@app.route("/api/device/<int:device_id>/fuel", methods=["GET"])
@jwt_required()
def get_device_fuel(device_id):
    # 🔥 KONTROLA VLASTNÍCTVA
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    
    if user.role != "admin":
        device = Device.query.filter_by(id=device_id, user_id=user_id).first()
        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404
    
    t = _get_latest_telemetry(device_id)
    if not t or all(v is None for v in [t.consumption_lh, t.consumption_l100km, t.maf, t.fuel_type]):
        return jsonify({"error": "No fuel data"}), 404
    
    return jsonify({
        "status": "success",
        "device_id": device_id,
        "vehicle_id": t.vehicle_id,  # 🔥 PRIDAJ vehicle_id
        "fuel": {
            "consumption_lh": t.consumption_lh,
            "consumption_l100km": t.consumption_l100km,
            "maf": t.maf,
            "type": t.fuel_type or "unknown"
        },
        "timestamp": _iso(t.created_at)
    }), 200


@app.route("/api/device/<int:device_id>/speed", methods=["GET"])
@jwt_required()
def get_device_speed(device_id):
    # 🔥 KONTROLA VLASTNÍCTVA
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    
    if user.role != "admin":
        device = Device.query.filter_by(id=device_id, user_id=user_id).first()
        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404
    
    t = _get_latest_telemetry(device_id)
    if not t or t.speed is None:
        return jsonify({"error": "No speed data"}), 404
    
    return jsonify({
        "status": "success", 
        "device_id": device_id,
        "vehicle_id": t.vehicle_id,  # 🔥 PRIDAJ vehicle_id
        "speed": int(t.speed), 
        "timestamp": _iso(t.created_at)
    }), 200



@app.route("/api/vehicles/telemetry-comparison", methods=["GET"])
@jwt_required()
def vehicles_telemetry_comparison():
    """
    Porovnanie telemetrie pre všetky vozidlá používateľa
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    description: |
        Vráti najnovšie telemetrické údaje pre všetky vozidlá,
        ktoré patria prihlásenému používateľovi.
        
        **Testovanie cez Postman:**
        - Metóda: `GET`
        - URL: `http://car-diagnostics.onrender.com/api/vehicles/telemetry-comparison`
        - Headers: `Authorization: Bearer <token>`
        
        **Očakávaná odpoveď:**
        ```json
        {
          "status": "success",
          "vehicles": [
            {
              "device_id": 12345,
              "vin": "1HGCM82633A123456",
              "brand": "Honda",
              "model": "Accord",
              "year": "2021",
              "online": true,
              "telemetry": {
                "odometer": 123456,
                "battery_voltage": 12.6,
                "battery_health": "good",
                "engine_running": true,
                "engine_rpm": 2500,
                "engine_load": 45.5,
                "coolant_temp": 90,
                "oil_temp": 95,
                "intake_air_temp": 25,
                "consumption_l100km": 8.2,
                "speed": 80,
                "timestamp": "2025-02-17T10:30:00Z"
              }
            }
          ],
          "summary": {
            "total_vehicles": 3,
            "online_vehicles": 2,
            "avg_consumption": 7.8,
            "avg_speed": 65,
            "total_odometer": 345678
          }
        }
        ```
    responses:
      200:
        description: Zoznam vozidiel s telemetriou
      404:
        description: User not found
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        # Získať všetky zariadenia používateľa
        if user.role == "admin":
            devices = Device.query.all()
        else:
            devices = Device.query.filter_by(user_id=user_id).all()

        vehicles_data = []
        total_odometer = 0
        vehicles_with_speed = 0
        total_speed = 0
        vehicles_with_consumption = 0
        total_consumption = 0
        online_count = 0

        for device in devices:
            # Získať VIN pre zariadenie
            device_vehicle = DeviceVehicle.query.filter_by(device_id=device.id).first()
            if not device_vehicle or not device_vehicle.last_vin_id:
                # Zariadenie bez priradeného VIN
                vehicles_data.append({
                    "device_id": device.id,
                    "vin": None,
                    "brand": None,
                    "model": None,
                    "year": None,
                    "online": device.status,
                    "telemetry": None,
                    "message": "No VIN assigned"
                })
                continue

            vehicle = Vehicle.query.get(device_vehicle.last_vin_id)
            if not vehicle:
                continue

            # Získať najnovšiu telemetriu pre toto vozidlo
            latest_telemetry = (
                VehicleTelemetry.query
                .filter_by(vehicle_id=vehicle.id)
                .order_by(VehicleTelemetry.created_at.desc())
                .first()
            )

            # Telemetria pre toto vozidlo
            telemetry_data = None
            if latest_telemetry:
                telemetry_data = {
                    "odometer": latest_telemetry.odometer,
                    "battery_voltage": latest_telemetry.battery_voltage,
                    "battery_health": latest_telemetry.battery_health,
                    "engine_running": latest_telemetry.engine_running,
                    "engine_rpm": latest_telemetry.engine_rpm,
                    "engine_load": latest_telemetry.engine_load,
                    "coolant_temp": latest_telemetry.coolant_temp,
                    "oil_temp": latest_telemetry.oil_temp,
                    "intake_air_temp": latest_telemetry.intake_air_temp,
                    "consumption_lh": latest_telemetry.consumption_lh,
                    "consumption_l100km": latest_telemetry.consumption_l100km,
                    "maf": latest_telemetry.maf,
                    "fuel_type": latest_telemetry.fuel_type,
                    "speed": latest_telemetry.speed,
                    "timestamp": _iso(latest_telemetry.created_at)
                }

                # Agregácia pre štatistiky
                if latest_telemetry.odometer:
                    total_odometer += latest_telemetry.odometer
                
                if latest_telemetry.speed:
                    total_speed += latest_telemetry.speed
                    vehicles_with_speed += 1
                
                if latest_telemetry.consumption_l100km:
                    total_consumption += latest_telemetry.consumption_l100km
                    vehicles_with_consumption += 1

            if device.status:
                online_count += 1

            vehicles_data.append({
                "device_id": device.id,
                "vin": vehicle.vin,
                "brand": vehicle.brand,
                "model": vehicle.model,
                "year": vehicle.year,
                "engine": vehicle.engine,
                "online": device.status,
                "telemetry": telemetry_data
            })

        # Vypočítať priemery
        summary = {
            "total_vehicles": len(vehicles_data),
            "online_vehicles": online_count,
            "avg_consumption": round(total_consumption / vehicles_with_consumption, 1) if vehicles_with_consumption > 0 else None,
            "avg_speed": round(total_speed / vehicles_with_speed) if vehicles_with_speed > 0 else None,
            "total_odometer": total_odometer
        }

        return jsonify({
            "status": "success",
            "vehicles": vehicles_data,
            "summary": summary
        }), 200

    except Exception as e:
        print("❌ VEHICLES TELEMETRY COMPARISON ERROR:", e)
        return jsonify({"error": str(e)}), 500
# =========================
# SHOW ALL
# =========================
@app.route("/api/all", methods=["GET"])
def show_all():
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

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    # ✅ MUST: socketio.run (nie app.run)
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
