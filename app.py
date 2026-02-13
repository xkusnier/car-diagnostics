from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flasgger import Swagger, swag_from
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
CORS(app)

# =========================
# ✅ SWAGGER CONFIG
# =========================
swagger_template = {
    "swagger": "2.0",
    "info": {
        "title": "Car Diagnostics API",
        "description": "REST API for RPi device and web dashboard",
        "version": "1.0.0"
    },
    "basePath": "/",
    "schemes": ["http", "https"],
}

swagger = Swagger(app, template=swagger_template)


swagger = Swagger(app, template=swagger_template)


# ✅ Socket.IO init (WS)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

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

class DtcPatternLink(db.Model):
    __tablename__ = "dtc_pattern_links"
    id = db.Column(db.Integer, primary_key=True)
    pattern_id = db.Column(db.Integer, db.ForeignKey("dtc_patterns.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)

class DeviceTelemetry(db.Model):
    __tablename__ = "device_telemetry"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False, index=True)

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
# SOCKET.IO HELPERS
# =========================
def _iso(ts: datetime | None) -> str | None:
    if not ts:
        return None
    return ts.replace(microsecond=0).isoformat() + "Z"

def _telemetry_payload(device_id: int, payload: dict) -> dict:
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
    try:
        battery = t.get("battery") or {}
        engine = t.get("engine") or {}
        fuel = t.get("fuel") or {}

        row = DeviceTelemetry(
            device_id=device_id,
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
    except Exception:
        db.session.rollback()

def _jwt_security(optional: bool = False):
    # Flasgger security block helper
    # optional=True => don't show as required, but still documents BearerAuth
    return [] if optional else [{"BearerAuth": []}]

# =========================
# SOCKET.IO EVENTS (docs-only in swagger via /api/ws-doc)
# =========================
@socketio.on("connect")
def ws_connect():
    emit("server_ready", {"status": "ok"})

@socketio.on("subscribe_device")
def ws_subscribe_device(data):
    try:
        device_id = int(data.get("device_id"))
    except Exception:
        emit("error", {"error": "invalid device_id"})
        return
    join_room(f"device:{device_id}")
    emit("subscribed", {"device_id": device_id})

@socketio.on("telemetry")
def ws_telemetry(data):
    try:
        device_id = int(data.get("device_id"))
    except Exception:
        emit("error", {"error": "missing/invalid device_id"})
        return

    device = Device.query.get(device_id)
    if not device:
        emit("error", {"error": "device not found"})
        return

    t = _telemetry_payload(device_id, data)

    try:
        device.status = True
        db.session.commit()
    except Exception:
        db.session.rollback()

    _save_telemetry_to_db(device_id, t)
    socketio.emit("telemetry_update", t, room=f"device:{device_id}")
    emit("telemetry_ack", {"ok": True, "timestamp": t["timestamp"]})

# =========================
# INIT / HEALTH
# =========================
@app.route("/init-db", methods=["GET"])
def init_db():
    """
    Create DB tables (dev utility).
    ---
    tags: [Health]
    responses:
      200:
        description: Database initialized
        schema:
          type: object
          properties:
            status:
              type: string
              example: Database ok
    """
    db.create_all()
    return jsonify({"status": "Database ok"})

@app.route("/", methods=["GET"])
def home():
    """
    Health check.
    ---
    tags: [Health]
    responses:
      200:
        description: OK
        schema:
          type: object
          properties:
            status:
              type: string
              example: ok
            message:
              type: string
              example: Flask bezi
    """
    return jsonify({"status": "ok", "message": "Flask bezi"})

@app.route("/api/ws-doc", methods=["GET"])
def ws_doc():
    """
    Socket.IO events documentation (not real REST).
    ---
    tags: [Socket.IO]
    responses:
      200:
        description: Socket.IO event contract
        schema:
          type: object
          properties:
            connect:
              type: object
            subscribe_device:
              type: object
            telemetry_in:
              type: object
            telemetry_out:
              type: object
    """
    return jsonify({
        "connect": {
            "server_emits": [
                {"event": "server_ready", "payload": {"status": "ok"}}
            ]
        },
        "subscribe_device": {
            "client_emits": [{"event": "subscribe_device", "payload": {"device_id": 1}}],
            "server_emits": [{"event": "subscribed", "payload": {"device_id": 1}}],
            "room": "device:<device_id>"
        },
        "telemetry_in": {
            "client_emits": [{
                "event": "telemetry",
                "payload": {
                    "device_id": 1,
                    "odometer": 251000,
                    "speed": 0,
                    "battery": {"battery_voltage": 12.44, "health": "good"},
                    "engine": {"running": False, "rpm": 0, "load": 10.0, "coolant_temp": 88, "oil_temp": 70, "intake_air_temp": 25},
                    "fuel": {"consumption_lh": 0.0, "consumption_l100km": 0.0, "maf": 2.1, "type": "diesel"}
                }
            }]
        },
        "telemetry_out": {
            "server_emits": [
                {"event": "telemetry_update", "payload": {"device_id": 1, "timestamp": "2026-02-13T12:34:56Z"}},
                {"event": "telemetry_ack", "payload": {"ok": True, "timestamp": "2026-02-13T12:34:56Z"}}
            ]
        }
    }), 200

# =========================
# AUTH
# =========================
@app.route("/api/login", methods=["POST"])
def login():
    """
    Login and get JWT token.
    ---
    tags: [Auth]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [email, password]
          properties:
            email:
              type: string
              example: user@test.com
            password:
              type: string
              example: 1234
    responses:
      200:
        description: JWT issued
        schema:
          type: object
          properties:
            status: {type: string, example: success}
            access_token: {type: string, example: "<JWT>"}
            role: {type: string, example: user}
      400:
        description: Missing email or password
      401:
        description: Invalid credentials
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
        return jsonify({"status": "success", "access_token": access_token, "role": user.role}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/register", methods=["POST"])
def register():
    """
    Register new user.
    ---
    tags: [Auth]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [email, password]
          properties:
            email:
              type: string
              example: user@test.com
            password:
              type: string
              example: 1234
    responses:
      201:
        description: Registered
      409:
        description: Email already exists
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
# 3-WAY HANDSHAKE
# =========================
@app.route("/api/connect", methods=["POST"])
def device_connect_syn():
    """
    RPi connect step 1 (SYN). Creates device if missing, sets status False.
    ---
    tags: [Devices]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [device_id]
          properties:
            device_id:
              type: integer
              example: 1
    responses:
      200:
        description: SYN-ACK
        schema:
          type: object
          properties:
            handshake: {type: string, example: SYN-ACK}
            device_id: {type: integer, example: 1}
      400:
        description: missing device_id
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
    RPi connect step 2 (ACK). Marks device online.
    ---
    tags: [Devices]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [device_id]
          properties:
            device_id:
              type: integer
              example: 1
    responses:
      200:
        description: ACK complete
      404:
        description: device not found
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
# HEARTBEAT / TRIGGER
# =========================
@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """
    RPi heartbeat. Returns next pending command (and marks it executed).
    ---
    tags: [Commands]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [device_id]
          properties:
            device_id:
              type: integer
              example: 1
    responses:
      200:
        description: OK or command
        schema:
          type: object
          properties:
            status: {type: string, example: ok}
            command: {type: string, example: GET_DTCS_PERM}
      400:
        description: missing device_id
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
    Queue a command for device (no JWT here).
    ---
    tags: [Commands]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [device_id, command]
          properties:
            device_id: {type: integer, example: 1}
            command:
              type: string
              example: GET_VIN
              enum: [GET_VIN, GET_DTCS_PERM, GET_DTCS_PEND, GET_RPM, GET_TEMP, CLEAR_DTCS]
    responses:
      200:
        description: Queued
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")
        command = data.get("command")

        valid_commands = [
            "GET_VIN", "GET_DTCS_PERM", "GET_DTCS_PEND",
            "GET_RPM", "GET_TEMP", "CLEAR_DTCS",
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
# DTC / PATTERNS
# =========================
@app.route("/api/dtc/pattern-check/<vin>", methods=["GET"])
@jwt_required(optional=True)
def check_dtc_patterns(vin):
    """
    Check DTC patterns for VIN (JWT optional).
    ---
    tags: [DTC]
    security: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        example: WVWZZZ1KZ6W000001
    responses:
      200:
        description: Matched patterns
      404:
        description: Vehicle not found
    """
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
                "vehicle_codes": list(active_dtcs),
            })

    return jsonify({"vin": vin, "active_dtc_codes": list(active_dtcs), "matched_patterns": matched_patterns}), 200

@app.route("/api/dtc-history-full", methods=["POST"])
@jwt_required(optional=True)
def dtc_history_full():
    """
    Full DTC history with descriptions (JWT optional).
    ---
    tags: [DTC]
    security: []
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [vin]
          properties:
            vin: {type: string, example: WVWZZZ1KZ6W000001}
    responses:
      200:
        description: History list
      404:
        description: Vehicle not found
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

        results = [{
            "dtc_code": h.dtc_code,
            "description": h.dtc_description or "No description available",
            "created_at": h.created_at.isoformat()
        } for h in history]

        return jsonify({"status": "success", "vin": vin.upper(), "history": results}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================
# DEVICES (JWT)
# =========================
@app.route("/api/my-devices", methods=["GET"])
@jwt_required()
def my_devices():
    """
    List devices for current user (admin sees all).
    ---
    tags: [Devices]
    security:
      - BearerAuth: []
    responses:
      200:
        description: List of devices
      401:
        description: Missing/invalid token
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        devices = Device.query.all() if user.role == "admin" else Device.query.filter_by(user_id=user_id).all()

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
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/<int:device_id>/diagnostics", methods=["GET"])
@jwt_required()
def device_diagnostics(device_id):
    """
    Get diagnostics for device: VIN + active DTC codes + device online status.
    ---
    tags: [Devices]
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
        example: 1
    responses:
      200:
        description: Diagnostics
      404:
        description: Device not found or not owned
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        device = Device.query.get(device_id) if user.role == "admin" else Device.query.filter_by(id=device_id, user_id=user_id).first()
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
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/<int:device_id>/read-dtcs", methods=["POST"])
@jwt_required()
def read_device_dtcs(device_id):
    """
    Queue GET_DTCS_PERM command for device.
    ---
    tags: [Commands]
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
        example: 1
    responses:
      200:
        description: Queued read command
      404:
        description: Device not found / not owned
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        device = Device.query.get(device_id) if user.role == "admin" else Device.query.filter_by(id=device_id, user_id=user_id).first()
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
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/<int:device_id>/clear-dtcs", methods=["POST"])
@jwt_required()
def clear_device_dtcs(device_id):
    """
    Queue CLEAR_DTCS command for device.
    ---
    tags: [Commands]
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
        example: 1
    responses:
      200:
        description: Queued clear command
      404:
        description: Device not found / not owned
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        device = Device.query.get(device_id) if user.role == "admin" else Device.query.filter_by(id=device_id, user_id=user_id).first()
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
        return jsonify({"error": str(e)}), 500

# =========================
# CAN ingest (RPi)
# =========================
@app.route("/api/can", methods=["POST"])
def receive_can_packet():
    """
    RPi ingest endpoint: supports VIN, DTC, clear_status, telemetry snapshot.
    Telemetry snapshot is stored to DB and pushed via Socket.IO.
    ---
    tags: [CAN]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [device_id]
          properties:
            device_id: {type: integer, example: 1}
            vin: {type: string, example: WVWZZZ1KZ6W000001}
            dtc_code: {type: string, example: P0300}
            clear_status: {type: string, example: ok}
            year: {type: string, example: "2006"}
            brand: {type: string, example: VW}
            model: {type: string, example: Golf}
            engine: {type: string, example: 1.9 TDI}
            odometer: {type: integer, example: 251000}
            speed: {type: integer, example: 0}
            battery:
              type: object
              properties:
                battery_voltage: {type: number, example: 12.44}
                health: {type: string, example: good}
            engine_data:
              type: object
              description: "Use `engine` field (object) in real payload; this is documentation only."
            engine:
              type: object
              properties:
                running: {type: boolean, example: false}
                rpm: {type: integer, example: 0}
                load: {type: number, example: 10.0}
                coolant_temp: {type: integer, example: 88}
                oil_temp: {type: integer, example: 70}
                intake_air_temp: {type: integer, example: 25}
            fuel:
              type: object
              properties:
                consumption_lh: {type: number, example: 0.0}
                consumption_l100km: {type: number, example: 0.0}
                maf: {type: number, example: 2.1}
                type: {type: string, example: diesel}
    responses:
      201:
        description: Stored VIN/DTC/Telemetry
      200:
        description: Ignored or Clear result
      404:
        description: Device not found
    """
    try:
        payload = request.get_json()
        device_id = payload.get("device_id")
        vin = payload.get("vin")
        dtc_code = payload.get("dtc_code")
        clear_status = payload.get("clear_status")

        year = payload.get("year")
        model = payload.get("model")
        brand = payload.get("brand")
        engine = payload.get("engine")

        if device_id is None:
            return jsonify({"error": "Missing 'device_id'"}), 400

        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": f"Device {device_id} not found"}), 404

        # TELEMETRY snapshot
        if any(k in payload for k in ["odometer", "battery", "engine", "fuel", "speed"]):
            t = _telemetry_payload(int(device_id), payload)
            device.status = True
            db.session.commit()

            _save_telemetry_to_db(int(device_id), t)
            socketio.emit("telemetry_update", t, room=f"device:{int(device_id)}")

            return jsonify({"status": "telemetry stored", "device_id": int(device_id), "timestamp": t["timestamp"]}), 201

        # CLEAR confirmation
        if clear_status is not None:
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated for clear"}), 400

            if clear_status == "ok":
                DTCCodeActive.query.filter_by(vin_id=state.last_vin_id).delete()
                db.session.commit()
                return jsonify({"status": "DTC cleared", "vin_id": state.last_vin_id}), 200

            return jsonify({"status": "Clear failed"}), 200

        # VIN
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

        # DTC
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
# VIN decode NHTSA
# =========================
@app.route("/api/vin/nhtsa", methods=["POST"])
def decode_vin_nhtsa():
    """
    Decode VIN using NHTSA VPIC (no API key).
    ---
    tags: [VIN]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [vin]
          properties:
            vin: {type: string, example: WVWZZZ1KZ6W000001}
    responses:
      200:
        description: VIN decoded
      400:
        description: Missing VIN
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

# =========================
# LOAD DTC CSV + DTC DESC
# =========================
@app.route("/api/load-dtc-codes", methods=["POST"])
def load_dtc_codes_from_csv():
    """
    Load DTC meanings from CSV URL (2 columns: code, description).
    ---
    tags: [DTC]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [csv_url]
          properties:
            csv_url: {type: string, example: "https://raw.githubusercontent.com/.../dtc.csv"}
    responses:
      200:
        description: Imported
    """
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
    """
    Get DTC description by code.
    ---
    tags: [DTC]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
       schema:
          type: object
          required: [dtc_code]
          properties:
            dtc_code: {type: string, example: P0300}
    responses:
      200:
        description: Found
      404:
        description: Not found
    """
    try:
        payload = request.get_json()
        dtc_code = payload.get("dtc_code")
        if not dtc_code:
            return jsonify({"error": "Missing 'dtc_code' parameter"}), 400

        record = DtcCodeMeaning.query.filter(
            db.func.lower(DtcCodeMeaning.dtc_code) == dtc_code.lower()
        ).first()

        if not record:
            return jsonify({"status": "not_found", "message": f"DTC code '{dtc_code}' not found in database."}), 404

        return jsonify({"status": "success", "dtc_code": record.dtc_code, "description": record.dtc_description}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================
# DTC HISTORY (simple, JWT)
# =========================
@app.route("/api/dtc-history/<vin>", methods=["GET"])
@jwt_required()
def get_dtc_history(vin):
    """
    Get DTC history list for VIN (JWT required).
    ---
    tags: [DTC]
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        example: WVWZZZ1KZ6W000001
    responses:
      200:
        description: History list
      404:
        description: Vehicle not found
    """
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
    """
    Mark device offline (no JWT).
    ---
    tags: [Devices]
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
        example: 1
    responses:
      200:
        description: Updated
      404:
        description: Device not found
    """
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
    """
    Decode VIN via apiverve (requires VINDECODER_API_KEY env).
    ---
    tags: [VIN]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [vin]
          properties:
            vin: {type: string, example: WVWZZZ1KZ6W000001}
    responses:
      200:
        description: Decoded
      500:
        description: Missing API key
    """
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
            return jsonify({"error": "VIN decoder API error", "status": response.status_code, "details": response.text}), response.status_code

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
            "bodyStyle": data.get("bodyStyle"),
        }

        return jsonify({"status": "success", "source": "apiverve", "data": cleaned}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================
# TELEMETRY SNAPSHOT (JWT)
# =========================
def _get_latest_telemetry(device_id: int) -> DeviceTelemetry | None:
    return (
        DeviceTelemetry.query
        .filter_by(device_id=device_id)
        .order_by(DeviceTelemetry.created_at.desc())
        .first()
    )

@app.route("/api/device/<int:device_id>/odometer", methods=["GET"])
@jwt_required()
def get_device_odometer(device_id):
    """
    Get last known odometer from telemetry (JWT).
    ---
    tags: [Telemetry]
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
        example: 1
    responses:
      200:
        description: Odometer
      404:
        description: No odometer data
    """
    t = _get_latest_telemetry(device_id)
    if not t or t.odometer is None:
        return jsonify({"error": "No odometer data"}), 404
    return jsonify({"status": "success", "device_id": device_id, "odometer": int(t.odometer), "timestamp": _iso(t.created_at)}), 200

@app.route("/api/device/<int:device_id>/battery", methods=["GET"])
@jwt_required()
def get_device_battery(device_id):
    """
    Get last known battery from telemetry (JWT).
    ---
    tags: [Telemetry]
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
        example: 1
    responses:
      200:
        description: Battery
      404:
        description: No battery data
    """
    t = _get_latest_telemetry(device_id)
    if not t or t.battery_voltage is None:
        return jsonify({"error": "No battery data"}), 404
    return jsonify({
        "status": "success",
        "device_id": device_id,
        "battery_voltage": float(t.battery_voltage),
        "health": t.battery_health or "unknown",
        "timestamp": _iso(t.created_at)
    }), 200

@app.route("/api/device/<int:device_id>/engine", methods=["GET"])
@jwt_required()
def get_device_engine(device_id):
    """
    Get last known engine data from telemetry (JWT).
    ---
    tags: [Telemetry]
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
        example: 1
    responses:
      200:
        description: Engine data
      404:
        description: No engine data
    """
    t = _get_latest_telemetry(device_id)
    if not t or all(v is None for v in [t.engine_running, t.engine_rpm, t.engine_load, t.coolant_temp, t.oil_temp, t.intake_air_temp]):
        return jsonify({"error": "No engine data"}), 404
    return jsonify({
        "status": "success",
        "device_id": device_id,
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
    """
    Get last known fuel data from telemetry (JWT).
    ---
    tags: [Telemetry]
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
        example: 1
    responses:
      200:
        description: Fuel data
      404:
        description: No fuel data
    """
    t = _get_latest_telemetry(device_id)
    if not t or all(v is None for v in [t.consumption_lh, t.consumption_l100km, t.maf, t.fuel_type]):
        return jsonify({"error": "No fuel data"}), 404
    return jsonify({
        "status": "success",
        "device_id": device_id,
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
    """
    Get last known speed from telemetry (JWT).
    ---
    tags: [Telemetry]
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
        example: 1
    responses:
      200:
        description: Speed
      404:
        description: No speed data
    """
    t = _get_latest_telemetry(device_id)
    if not t or t.speed is None:
        return jsonify({"error": "No speed data"}), 404
    return jsonify({"status": "success", "device_id": device_id, "speed": int(t.speed), "timestamp": _iso(t.created_at)}), 200

# =========================
# SHOW ALL (debug)
# =========================
@app.route("/api/all", methods=["GET"])
def show_all():
    """
    Debug endpoint: list all vehicles with active/history DTCs (no JWT).
    ---
    tags: [Admin]
    responses:
      200:
        description: List
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
        return jsonify({"error": str(e)}), 500

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
