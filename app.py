import eventlet
eventlet.monkey_patch()
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
from sqlalchemy import func, or_
from flask_socketio import SocketIO, emit, join_room
import re
app = Flask(__name__)
CORS(app, origins=[
    "https://car-diagnostics-frontend.onrender.com",
    "https://car-diagnostics.onrender.com",
    "http://localhost:5000",
    "http://localhost:3000"
])
@app.before_request
def ensure_json_content_type():
    if request.method in ['POST', 'PUT', 'PATCH']:
        if request.method == 'OPTIONS':
            return
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
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response
app.config['SWAGGER'] = {
    'title': 'Inteligentna diagnostika API',
    'uiversion': 3,
    'openapi': '3.0.2',
    'doc_expansion': 'list',
    'description': '''
        API pre bakalarsku pracu - diagnostika vozidiel
        ## Testovanie cez Postman
        Vsetky endpointy je mozne testovat v Postmane podla nasledujucich prikladov:
        ### Autentifikacia
        1. Zaregistruj sa: `POST /api/register`
        2. Prihlas sa: `POST /api/login` → ziskas JWT token
        3. Pre autorizovane endpointy pridaj header: `Authorization: Bearer <token>`
        ### Zariadenia
        - Pridanie zariadenia: `POST /api/add-device` s JSON body `{"device_id": 12345}`
        - Zoznam mojich zariadeni: `GET /api/my-devices`
        - Diagnostika: `GET /api/device/12345/diagnostics`
        ### Komunikacia s RPi
        - Heartbeat: `POST /api/heartbeat` s JSON `{"device_id": 12345}`
        - Trigger príkazu: `POST /api/trigger` s JSON `{"device_id": 12345, "command": "GET_VIN"}`
        - CAN packet: `POST /api/can` s JSON `{"device_id": 12345, "vin": "1HGCM82633A123456"}`
    ''',
    'version': '1.0.0',
    'termsOfService': '',
    'contact': {
        'name': 'Jozef Kusnier',
        'email': '120957@stuba.sk'
    },
    'license': {
        'name': 'MIT'
    },
    'servers': [
        {
            'url': 'https://car-diagnostics.onrender.com',
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
app.config['SWAGGER_UI_TRY_IT_OUT'] = False
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet"
)
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "your-secret-key")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)
jwt = JWTManager(app)
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="user")
    email = db.Column(db.String(120), unique=True, nullable=False)
    devices = db.relationship("Device", backref="user", lazy=True, cascade="all, delete")
class Device(db.Model):
    __tablename__ = "device"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime, nullable=True)
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
class Trip(db.Model):
    __tablename__ = "trips"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("trips", lazy="dynamic"))
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)
    samples_count = db.Column(db.Integer, default=0)
    start_odometer = db.Column(db.Integer, nullable=True)
    end_odometer = db.Column(db.Integer, nullable=True)
    distance_km = db.Column(db.Float, nullable=True)
    avg_speed = db.Column(db.Float, nullable=True)
    max_speed = db.Column(db.Integer, nullable=True)
    avg_rpm = db.Column(db.Float, nullable=True)
    max_rpm = db.Column(db.Integer, nullable=True)
    min_rpm = db.Column(db.Integer, nullable=True)
    avg_consumption_l100km = db.Column(db.Float, nullable=True)
    total_fuel_used_l = db.Column(db.Float, nullable=True)
    avg_coolant_temp = db.Column(db.Float, nullable=True)
    max_coolant_temp = db.Column(db.Integer, nullable=True)
    avg_oil_temp = db.Column(db.Float, nullable=True)
    max_oil_temp = db.Column(db.Integer, nullable=True)
    engine_starts = db.Column(db.Integer, default=1)
    is_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
class DTCCodeHistory(db.Model):
    __tablename__ = "dtc_codes_history"
    id = db.Column(db.Integer, primary_key=True)
    vin_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)
    severity = db.Column(db.String(20), default="medium")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
class UserVehicle(db.Model):
    __tablename__ = "user_vehicles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    user = db.relationship("User", backref=db.backref("owned_vehicles", lazy="dynamic"))
    vehicle = db.relationship("Vehicle", backref=db.backref("owners", lazy="dynamic"))
    __table_args__ = (
        db.UniqueConstraint('user_id', 'vehicle_id', name='unique_user_vehicle'),
    )
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
    source_url = db.Column(db.Text, nullable=True)
class DtcPatternLink(db.Model):
    __tablename__ = "dtc_pattern_links"
    id = db.Column(db.Integer, primary_key=True)
    pattern_id = db.Column(db.Integer, db.ForeignKey("dtc_patterns.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)
class VehicleTelemetry(db.Model):
    __tablename__ = "vehicle_telemetry"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("telemetry", lazy="dynamic"))
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
class VehicleTelemetryLive(db.Model):
    __tablename__ = "vehicle_telemetry_live"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, unique=True, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("live_telemetry", uselist=False))
    odometer = db.Column(db.Integer, nullable=True)
    odometer_source = db.Column(db.String(20), nullable=False, default="rpi")
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
class VehicleTelemetryHistory(db.Model):
    __tablename__ = "vehicle_telemetry_history"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("telemetry_history", lazy="dynamic"))
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id"), nullable=True, index=True)
    trip = db.relationship("Trip", backref=db.backref("telemetry_samples", lazy="dynamic"))
    odometer = db.Column(db.Integer, nullable=True)
    odometer_source = db.Column(db.String(20), nullable=False, default="rpi")
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
class VehicleLocationHistory(db.Model):
    __tablename__ = "vehicle_location_history"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("location_history", lazy="dynamic"))
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id"), nullable=True, index=True)
    trip = db.relationship("Trip", backref=db.backref("location_points", lazy="dynamic"))
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
class DrivingEvent(db.Model):
    __tablename__ = "driving_events"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=True, index=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    event_timestamp = db.Column(db.DateTime, nullable=False, index=True)
    speed_kmh = db.Column(db.Float, nullable=True)
    g_force = db.Column(db.Float, nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    accel_x = db.Column(db.Float, nullable=True)
    accel_y = db.Column(db.Float, nullable=True)
    accel_z = db.Column(db.Float, nullable=True)
    gyro_x = db.Column(db.Float, nullable=True)
    gyro_y = db.Column(db.Float, nullable=True)
    gyro_z = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    device = db.relationship("Device", backref=db.backref("driving_events", lazy="dynamic"))
    vehicle = db.relationship("Vehicle", backref=db.backref("driving_events", lazy="dynamic"))
ALLOWED_DRIVING_EVENT_TYPES = {
    "HARD_BRAKE",
    "SHARP_ACCELERATION",
    "HARD_TURN",
    "CRASH",
}
DEVICE_ONLINE_TIMEOUT_SECONDS = 120
def mark_device_online(device):
    device.status = True
    device.last_seen = datetime.utcnow()
def refresh_stale_device_statuses():
    cutoff = datetime.utcnow() - timedelta(seconds=DEVICE_ONLINE_TIMEOUT_SECONDS)
    stale_devices = Device.query.filter(
        Device.status == True,
        Device.last_seen.isnot(None),
        Device.last_seen < cutoff
    ).all()
    changed = False
    for device in stale_devices:
        device.status = False
        changed = True
    if changed:
        db.session.commit()
def serialize_driving_event(event: DrivingEvent) -> dict:
    return {
        "id": event.id,
        "device_id": event.device_id,
        "vehicle_id": event.vehicle_id,
        "vin": event.vehicle.vin if event.vehicle else None,
        "event_type": event.event_type,
        "event_timestamp": _iso(event.event_timestamp),
        "speed_kmh": event.speed_kmh,
        "g_force": event.g_force,
        "latitude": event.latitude,
        "longitude": event.longitude,
        "accel": {
            "x": event.accel_x,
            "y": event.accel_y,
            "z": event.accel_z,
        },
        "gyro": {
            "x": event.gyro_x,
            "y": event.gyro_y,
            "z": event.gyro_z,
        },
        "created_at": _iso(event.created_at),
    }
@app.route("/api/driving-event", methods=["POST"])
def receive_driving_event():
    """
    Prijem jazdnych udalosti z RPi / gyroskopu / akcelerometra
    ---
    tags:
      - Driving Events
    description: |
      Endpoint na ulozenie jazdnych udalosti ako:
      - HARD_BRAKE
      - SHARP_ACCELERATION
      - HARD_TURN
      - CRASH
      Data mozu obsahovat VIN priamo, alebo sa vozidlo dohlada podla device_id.
      **Priklad requestu:**
      ```json
      {
        "device_id": 12345,
        "vin": "WF0XXXXX12345678",
        "event_type": "HARD_BRAKE",
        "timestamp": 1710500000.123,
        "g_force": 0.72,
        "speed_kmh": 85,
        "latitude": 48.1486,
        "longitude": 17.1077,
        "accel": {
          "x": -7.06,
          "y": 0.12,
          "z": 9.81
        },
        "gyro": {
          "x": 0.5,
          "y": -0.3,
          "z": 0.1
        }
      }
      ```
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - device_id
            - event_type
          properties:
            device_id:
              type: integer
              example: 12345
            vin:
              type: string
              example: "WF0XXXXX12345678"
            event_type:
              type: string
              enum: [HARD_BRAKE, SHARP_ACCELERATION, HARD_TURN, CRASH]
              example: "HARD_BRAKE"
            timestamp:
              type: number
              example: 1710500000.123
            g_force:
              type: number
              example: 0.72
            speed_kmh:
              type: number
              example: 85
            latitude:
              type: number
              example: 48.1486
            longitude:
              type: number
              example: 17.1077
            accel:
              type: object
              properties:
                x:
                  type: number
                  example: -7.06
                y:
                  type: number
                  example: 0.12
                z:
                  type: number
                  example: 9.81
            gyro:
              type: object
              properties:
                x:
                  type: number
                  example: 0.5
                y:
                  type: number
                  example: -0.3
                z:
                  type: number
                  example: 0.1
    responses:
      201:
        description: Event uspesne ulozeny
      400:
        description: Chybajuce alebo neplatne data
      404:
        description: Device alebo vehicle neexistuje
      500:
        description: Server error
    """
    try:
        payload = request.get_json()
        device_id_raw = payload.get("device_id")
        vin = payload.get("vin")
        event_type = str(payload.get("event_type") or "").upper().strip()
        timestamp_raw = payload.get("timestamp")
        g_force = payload.get("g_force")
        speed_kmh = payload.get("speed_kmh")
        latitude_raw = payload.get("latitude")
        longitude_raw = payload.get("longitude")
        accel = payload.get("accel") or {}
        gyro = payload.get("gyro") or {}
        try:
            device_id = int(device_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "device_id must be an integer"}), 400
        if not event_type:
            return jsonify({"error": "Missing event_type"}), 400
        if event_type not in ALLOWED_DRIVING_EVENT_TYPES:
            return jsonify({
                "error": "Invalid event_type",
                "allowed": sorted(list(ALLOWED_DRIVING_EVENT_TYPES))
            }), 400
        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": f"Device {device_id} not found"}), 404
        vehicle = None
        if vin:
            vehicle = Vehicle.query.filter_by(vin=vin.strip().upper()).first()
            if not vehicle:
                return jsonify({"error": "Vehicle not found for provided VIN"}), 404
        else:
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if state and state.last_vin_id:
                vehicle = Vehicle.query.get(state.last_vin_id)
        if timestamp_raw is None:
            event_timestamp = datetime.utcnow()
        else:
            try:
                event_timestamp = datetime.utcfromtimestamp(float(timestamp_raw))
            except (TypeError, ValueError):
                return jsonify({"error": "timestamp must be unix timestamp in seconds"}), 400
        try:
            g_force_val = float(g_force) if g_force is not None else None
        except (TypeError, ValueError):
            return jsonify({"error": "g_force must be a number"}), 400
        try:
            speed_kmh_val = float(speed_kmh) if speed_kmh is not None else None
        except (TypeError, ValueError):
            return jsonify({"error": "speed_kmh must be a number"}), 400
        try:
            latitude_val = float(latitude_raw) if latitude_raw is not None else None
        except (TypeError, ValueError):
            return jsonify({"error": "latitude must be a number"}), 400
        try:
            longitude_val = float(longitude_raw) if longitude_raw is not None else None
        except (TypeError, ValueError):
            return jsonify({"error": "longitude must be a number"}), 400
        if latitude_val is not None and (latitude_val < -90 or latitude_val > 90):
            return jsonify({"error": "latitude must be between -90 and 90"}), 400
        if longitude_val is not None and (longitude_val < -180 or longitude_val > 180):
            return jsonify({"error": "longitude must be between -180 and 180"}), 400
        def _to_float(v):
            if v is None:
                return None
            return float(v)
        new_event = DrivingEvent(
            device_id=device_id,
            vehicle_id=vehicle.id if vehicle else None,
            event_type=event_type,
            event_timestamp=event_timestamp,
            speed_kmh=speed_kmh_val,
            g_force=g_force_val,
            latitude=latitude_val,
            longitude=longitude_val,
            accel_x=_to_float(accel.get("x")),
            accel_y=_to_float(accel.get("y")),
            accel_z=_to_float(accel.get("z")),
            gyro_x=_to_float(gyro.get("x")),
            gyro_y=_to_float(gyro.get("y")),
            gyro_z=_to_float(gyro.get("z")),
        )
        db.session.add(new_event)
        mark_device_online(device)
        db.session.commit()
        event_payload = serialize_driving_event(new_event)
        socketio.emit("driving_event", event_payload, room=f"device:{device_id}")
        return jsonify({
            "status": "success",
            "message": "Driving event stored",
            "event": event_payload
        }), 201
    except Exception as e:
        db.session.rollback()
        print("❌ DRIVING EVENT ERROR:", e)
        return jsonify({"error": str(e)}), 500
VIN_TRANSLITERATION = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
}
VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
VIN_ALLOWED_REGEX = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
@app.route("/api/vin/validate", methods=["POST"])
@jwt_required(optional=True)
def validate_vin_endpoint():
    """
    Validacia VIN formatu a checksumu
    ---
    tags:
      - VIN
    security:
      - bearerAuth: []
    description: |
      Overi:
      - ci VIN ma spravny format
      - ci ma spravny checksum
      - ci sa vozidlo nachadza v databaze
      Mozne stavy:
      - invalid_format
      - invalid_checksum
      - not_found
      - valid
      **Priklad requestu:**
      ```json
      {
        "vin": "WF0XXXXX12345678"
      }
      ```
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - vin
          properties:
            vin:
              type: string
              example: "1HGCM82633A123456"
    responses:
      200:
        description: Validacia vykonana
      400:
        description: Chybajuci VIN
      500:
        description: Server error
    """
    try:
        payload = request.get_json()
        vin = (payload.get("vin") or "").strip().upper()
        if not vin:
            return jsonify({"error": "Missing 'vin' parameter"}), 400
        validation = validate_vin_value(vin)
        if not validation["valid"]:
            return jsonify({
                "status": "invalid",
                "vin": vin,
                **validation
            }), 200
        vehicle = Vehicle.query.filter_by(vin=vin).first()
        if not vehicle:
            return jsonify({
                "status": "not_found",
                "vin": vin,
                "valid": True,
                "reason": "not_found",
                "message": "Vozidlo nie je v našej databáze."
            }), 200
        return jsonify({
            "status": "valid",
            "vin": vin,
            "valid": True,
            "reason": None,
            "message": "VIN is valid",
            "vehicle_exists": True
        }), 200
    except Exception as e:
        print("❌ VIN VALIDATION ERROR:", e)
        return jsonify({"error": str(e)}), 500
def compute_vin_check_digit(vin: str) -> str | None:
    vin = (vin or "").strip().upper()
    if not VIN_ALLOWED_REGEX.match(vin):
        return None
    total = 0
    for i, ch in enumerate(vin):
        value = VIN_TRANSLITERATION.get(ch)
        if value is None:
            return None
        total += value * VIN_WEIGHTS[i]
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)
def validate_vin_value(vin: str) -> dict:
    vin = (vin or "").strip().upper()
    if len(vin) != 17:
        return {
            "valid": False,
            "reason": "invalid_format",
            "message": "Takéto VIN nemôže existovať."
        }
    if not VIN_ALLOWED_REGEX.match(vin):
        return {
            "valid": False,
            "reason": "invalid_format",
            "message": "Takéto VIN nemôže existovať."
        }
    expected_check_digit = compute_vin_check_digit(vin)
    if expected_check_digit is None:
        return {
            "valid": False,
            "reason": "invalid_format",
            "message": "Takéto VIN nemôže existovať."
        }
    actual_check_digit = vin[8]
    if actual_check_digit != expected_check_digit:
        return {
            "valid": False,
            "reason": "invalid_checksum",
            "message": "Zlý VIN checksum.",
            "expected_check_digit": expected_check_digit,
            "actual_check_digit": actual_check_digit,
        }
    return {
        "valid": True,
        "reason": None,
        "message": "VIN is valid",
        "expected_check_digit": expected_check_digit,
        "actual_check_digit": actual_check_digit,
    }
@app.route("/api/device/<int:device_id>/events", methods=["GET"])
@jwt_required()
def get_device_events(device_id):
    """
    Historia jazdnych udalosti pre konkretne zariadenie
    ---
    tags:
      - Driving Events
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
      - in: query
        name: limit
        required: false
        type: integer
        example: 50
      - in: query
        name: event_type
        required: false
        type: string
        example: "HARD_BRAKE"
    responses:
      200:
        description: Zoznam udalosti zariadenia
      404:
        description: Device neexistuje alebo nepatri pouzivatelovi
      500:
        description: Server error
    """
    try:
        refresh_stale_device_statuses()
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
        limit = request.args.get("limit", default=50, type=int)
        event_type = request.args.get("event_type", default=None, type=str)
        query = DrivingEvent.query.filter_by(device_id=device_id)
        if event_type:
            query = query.filter(DrivingEvent.event_type == event_type.upper().strip())
        events = (
            query.order_by(DrivingEvent.event_timestamp.desc())
            .limit(min(limit, 200))
            .all()
        )
        return jsonify({
            "status": "success",
            "device_id": device_id,
            "count": len(events),
            "events": [serialize_driving_event(e) for e in events]
        }), 200
    except Exception as e:
        print("❌ GET DEVICE EVENTS ERROR:", e)
        return jsonify({"error": str(e)}), 500
@app.route("/api/vehicle/<string:vin>/events", methods=["GET"])
@jwt_required()
def get_vehicle_events(vin):
    """
    Historia jazdnych udalosti pre konkretne vozidlo podla VIN
    ---
    tags:
      - Driving Events
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
      - in: query
        name: limit
        required: false
        type: integer
        example: 50
      - in: query
        name: event_type
        required: false
        type: string
        example: "CRASH"
    responses:
      200:
        description: Zoznam udalosti vozidla
      403:
        description: Vozidlo nepatri pouzivatelovi
      404:
        description: Vehicle neexistuje
      500:
        description: Server error
    """
    try:
        refresh_stale_device_statuses()
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
        if user.role != "admin":
            user_vehicle = UserVehicle.query.filter_by(
                user_id=user_id,
                vehicle_id=vehicle.id
            ).first()
            if not user_vehicle:
                return jsonify({"error": "Vehicle not owned by user"}), 403
        limit = request.args.get("limit", default=50, type=int)
        event_type = request.args.get("event_type", default=None, type=str)
        query = DrivingEvent.query.filter_by(vehicle_id=vehicle.id)
        if event_type:
            query = query.filter(DrivingEvent.event_type == event_type.upper().strip())
        events = (
            query.order_by(DrivingEvent.event_timestamp.desc())
            .limit(min(limit, 200))
            .all()
        )
        summary = {
            "hard_brake": sum(1 for e in events if e.event_type == "HARD_BRAKE"),
            "sharp_acceleration": sum(1 for e in events if e.event_type == "SHARP_ACCELERATION"),
            "hard_turn": sum(1 for e in events if e.event_type == "HARD_TURN"),
            "crash": sum(1 for e in events if e.event_type == "CRASH"),
        }
        return jsonify({
            "status": "success",
            "vin": vehicle.vin,
            "vehicle": {
                "brand": vehicle.brand,
                "model": vehicle.model,
                "year": vehicle.year
            },
            "count": len(events),
            "summary": summary,
            "events": [serialize_driving_event(e) for e in events]
        }), 200
    except Exception as e:
        print("❌ GET VEHICLE EVENTS ERROR:", e)
        return jsonify({"error": str(e)}), 500
@app.route("/api/health", methods=["GET"])
def health_check():
    """
    Health check endpoint
    ---
    tags:
      - System
    description: Overi, ci server bezi a vrati jednoduchy stav aplikacie.
    responses:
      200:
        description: Server je dostupny
        schema:
          type: object
          properties:
            status:
              type: string
              example: ok
    """
    return jsonify({"status": "ok"}), 200
@app.route("/init-db")
def init_db():
    """
    Inicializacia databazy
    ---
    tags:
      - System
    description: |
      Vytvori vsetky tabulky v databaze podla definovanych modelov.
      **Testovanie cez Postman:**
      - Metoda: `GET`
      - URL: `http://car-diagnostics.onrender.com/init-db`
      - Headers: ziadne
      - Body: ziadne
    responses:
      200:
        description: Databaza uspesne vytvorena
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
      Overenie, ci server bezi.
      **Testovanie cez Postman:**
      - Metoda: `GET`
      - URL: `http://car-diagnostics.onrender.com/`
      - Headers: ziadne
      - Body: ziadne
      **Ocakavana odpoved:**
      ```json
      {
        "status": "ok",
        "message": "Flask bezi"
      }
      ```
    responses:
      200:
        description: Server bezi
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
def get_recommended_action(severity: str) -> str:
    severity = (severity or "medium").lower()
    if severity == "low":
        return "Continue driving and monitor the vehicle"
    if severity == "medium":
        return "Visit a service center soon"
    if severity == "critical":
        return "Stop immediately and do not continue driving"
    return "Visit a service center soon"
def send_dtc_email_notification(user_email: str, vehicle_info: dict, dtc_code: str, description: str, severity: str):
    """Pošle DTC notifikáciu cez Brevo Transactional Email API.
    Nepoužíva SMTP porty, ale HTTPS request na Brevo API, takže je vhodné aj pre Render Free.
    V Renderi nastav minimálne BREVO_API_KEY a BREVO_SENDER_EMAIL.
    """
    try:
        brevo_api_key = os.environ.get("BREVO_API_KEY")
        sender_email = (
            os.environ.get("BREVO_SENDER_EMAIL")
            or os.environ.get("SMTP_SENDER")
            or "kusnier.jozo@gmail.com"
        )
        sender_name = os.environ.get("BREVO_SENDER_NAME", "Car-Diagnostics")
        if not brevo_api_key:
            print("⚠️ BREVO_API_KEY is not configured, skipping email notification")
            return
        if not sender_email:
            print("⚠️ BREVO_SENDER_EMAIL is not configured, skipping email notification")
            return
        recommended_action = get_recommended_action(severity)
        subject = f"Car-Diagnostics alert: {dtc_code} ({severity.upper()})"
        text_content = f"""
Car-Diagnostics detected a diagnostic trouble code on your vehicle.
Vehicle:
VIN: {vehicle_info.get("vin")}
Brand: {vehicle_info.get("brand") or "Unknown"}
Model: {vehicle_info.get("model") or "Unknown"}
Year: {vehicle_info.get("year") or "Unknown"}
Detected fault:
DTC code: {dtc_code}
Description: {description or "No description available"}
Severity: {severity.upper()}
Recommended action: {recommended_action}
This is an automatic notification from Car-Diagnostics.
""".strip()
        payload = {
            "sender": {
                "name": sender_name,
                "email": sender_email
            },
            "to": [
                {
                    "email": user_email
                }
            ],
            "subject": subject,
            "textContent": text_content
        }
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": brevo_api_key,
                "content-type": "application/json"
            },
            json=payload,
            timeout=10
        )
        if response.status_code not in (200, 201, 202):
            print(f"❌ BREVO API ERROR {response.status_code}: {response.text}")
            return
        print(f"✅ DTC email notification sent to {user_email}")
    except Exception as e:
        print(f"❌ EMAIL NOTIFICATION ERROR for {user_email}: {e}")
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
@app.route("/api/dashboard-summary", methods=["GET"])
@jwt_required()
def dashboard_summary():
    """
    Domovska stranka - suhrn pouzivatelskych dat a vozidla s aktivnymi chybami
    ---
    tags:
      - Dashboard
    security:
      - bearerAuth: []
    responses:
      200:
        description: Dashboard summary
    """
    try:
        refresh_stale_device_statuses()
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        if user.role == "admin":
            devices = Device.query.all()
        else:
            devices = Device.query.filter_by(user_id=user_id).all()
        total_devices = len(devices)
        if user.role == "admin":
            user_vehicles = Vehicle.query.all()
        else:
            user_vehicles = (
                db.session.query(Vehicle)
                .join(UserVehicle, UserVehicle.vehicle_id == Vehicle.id)
                .filter(UserVehicle.user_id == user_id)
                .all()
            )
        total_vehicles = len(user_vehicles)
        vehicle_ids = [v.id for v in user_vehicles]
        active_dtcs_count = 0
        vehicles_with_issues = []
        if vehicle_ids:
            active_dtcs_count = (
                DTCCodeActive.query
                .filter(DTCCodeActive.vin_id.in_(vehicle_ids))
                .count()
            )
            dtc_counts = (
                db.session.query(
                    DTCCodeActive.vin_id,
                    func.count(DTCCodeActive.id).label("dtc_count")
                )
                .filter(DTCCodeActive.vin_id.in_(vehicle_ids))
                .group_by(DTCCodeActive.vin_id)
                .all()
            )
            dtc_count_map = {row.vin_id: row.dtc_count for row in dtc_counts}
            for vehicle in user_vehicles:
                dtc_count = dtc_count_map.get(vehicle.id, 0)
                if dtc_count <= 0:
                    continue
                device_link = DeviceVehicle.query.filter_by(last_vin_id=vehicle.id).first()
                linked_device = Device.query.get(device_link.device_id) if device_link else None
                owner_links = UserVehicle.query.filter_by(vehicle_id=vehicle.id).all()
                owner_user_ids = [link.user_id for link in owner_links]
                primary_user_id = owner_user_ids[0] if owner_user_ids else None
                vehicles_with_issues.append({
                    "vehicle_id": vehicle.id,
                    "vin": vehicle.vin,
                    "brand": vehicle.brand,
                    "model": vehicle.model,
                    "year": vehicle.year,
                    "engine": vehicle.engine,
                    "dtc_count": dtc_count,
                    "device_id": linked_device.id if linked_device else None,
                    "online": linked_device.status if linked_device else False,
                    "user_id": primary_user_id,
                    "owner_user_ids": owner_user_ids
                })
        vehicles_with_issues.sort(key=lambda x: x["dtc_count"], reverse=True)
        return jsonify({
            "status": "success",
            "summary": {
                "total_devices": total_devices,
                "total_vehicles": total_vehicles,
                "active_dtcs": active_dtcs_count,
                "vehicles_with_issues": len(vehicles_with_issues)
            },
            "vehicles_with_issues_list": vehicles_with_issues
        }), 200
    except Exception as e:
        print("❌ DASHBOARD SUMMARY ERROR:", e)
        return jsonify({"error": str(e)}), 500
@app.route("/api/vehicle/<string:vin>/trips", methods=["GET"])
@jwt_required()
def get_vehicle_trips(vin):
    """
    Ziskanie vsetkych jazd pre konkretne vozidlo
    ---
    tags:
      - Trips
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
    responses:
      200:
        description: Zoznam jazd
    """
    try:
        refresh_stale_device_statuses()
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
        user_vehicle = UserVehicle.query.filter_by(
            user_id=user_id,
            vehicle_id=vehicle.id
        ).first()
        if not user_vehicle and user.role != "admin":
            return jsonify({"error": "Vehicle not owned by user"}), 403
        trips = Trip.query.filter_by(
            vehicle_id=vehicle.id,
            is_completed=True
        ).order_by(Trip.start_time.desc()).all()
        trips_data = []
        for trip in trips:
            if not trip.avg_consumption_l100km and trip.samples_count > 0:
                consumptions = db.session.query(
                    func.avg(VehicleTelemetryHistory.consumption_l100km)
                ).filter(
                    VehicleTelemetryHistory.trip_id == trip.id,
                    VehicleTelemetryHistory.consumption_l100km.isnot(None)
                ).scalar()
                trip.avg_consumption_l100km = consumptions
            location_points = (
                VehicleLocationHistory.query
                .filter_by(trip_id=trip.id)
                .order_by(VehicleLocationHistory.created_at.asc())
                .all()
            )
            trips_data.append({
                "id": trip.id,
                "start_time": _iso(trip.start_time),
                "end_time": _iso(trip.end_time),
                "duration_seconds": trip.duration_seconds,
                "samples_count": trip.samples_count,
                "distance_km": round(trip.distance_km, 1) if trip.distance_km else None,
                "avg_speed": round(trip.avg_speed, 1) if trip.avg_speed else None,
                "max_speed": trip.max_speed,
                "avg_rpm": round(trip.avg_rpm) if trip.avg_rpm else None,
                "max_rpm": trip.max_rpm,
                "min_rpm": trip.min_rpm,
                "avg_consumption_l100km": round(trip.avg_consumption_l100km, 1) if trip.avg_consumption_l100km else None,
                "total_fuel_used_l": round(trip.total_fuel_used_l, 2) if trip.total_fuel_used_l else None,
                "avg_coolant_temp": round(trip.avg_coolant_temp) if trip.avg_coolant_temp else None,
                "max_coolant_temp": trip.max_coolant_temp,
                "avg_oil_temp": round(trip.avg_oil_temp) if trip.avg_oil_temp else None,
                "max_oil_temp": trip.max_oil_temp,
                "engine_starts": trip.engine_starts,
                "start_odometer": trip.start_odometer,
                "end_odometer": trip.end_odometer,
                "location_points": [
                    {
                        "latitude": p.latitude,
                        "longitude": p.longitude,
                        "timestamp": _iso(p.created_at)
                    }
                    for p in location_points
                ]
            })
        return jsonify({
            "status": "success",
            "vin": vin,
            "vehicle": {
                "brand": vehicle.brand,
                "model": vehicle.model,
                "year": vehicle.year
            },
            "trips": trips_data,
            "total_trips": len(trips_data)
        }), 200
    except Exception as e:
        print("❌ GET VEHICLE TRIPS ERROR:", e)
        return jsonify({"error": str(e)}), 500
def _save_telemetry_to_db(device_id: int, t: dict) -> None:
    """Uloží telemetriu do DB - live (posledná) + history (všetky) + trip detection"""
    try:
        device_vehicle = DeviceVehicle.query.filter_by(device_id=device_id).first()
        if not device_vehicle or not device_vehicle.last_vin_id:
            print(f"❌ No vehicle associated with device {device_id}")
            return
        vehicle_id = device_vehicle.last_vin_id
        battery = t.get("battery") or {}
        engine = t.get("engine") or {}
        fuel = t.get("fuel") or {}
        live_row = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle_id).first()
        if not live_row:
            live_row = VehicleTelemetryLive(
                vehicle_id=vehicle_id,
                odometer=t.get("odometer"),
                odometer_source="rpi"
            )
            db.session.add(live_row)
            db.session.flush()
        odometer_source = (live_row.odometer_source or "rpi").lower()
        engine_running = engine.get("running")
        active_trip = Trip.query.filter_by(
            vehicle_id=vehicle_id,
            is_completed=False
        ).first()
        current_time = datetime.utcnow()
        if engine_running and not active_trip:
            start_odometer = live_row.odometer if odometer_source == "manual" else t.get("odometer")
            active_trip = Trip(
                vehicle_id=vehicle_id,
                start_time=current_time,
                start_odometer=start_odometer,
                engine_starts=1,
                is_completed=False
            )
            db.session.add(active_trip)
            db.session.flush()
            print(f"✅ New trip started for vehicle {vehicle_id} at {current_time}")
        trip_id = active_trip.id if active_trip else None
        history_row = VehicleTelemetryHistory(
            vehicle_id=vehicle_id,
            odometer=t.get("odometer"),
            battery_voltage=battery.get("battery_voltage"),
            battery_health=battery.get("health"),
            engine_running=engine_running,
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
            created_at=current_time,
            trip_id=trip_id
        )
        db.session.add(history_row)
        if active_trip:
            active_trip.samples_count += 1
            active_trip.end_time = current_time
            if odometer_source == "rpi":
                current_odometer = t.get("odometer")
                if current_odometer is not None:
                    if active_trip.start_odometer is None:
                        active_trip.start_odometer = current_odometer
                    active_trip.end_odometer = current_odometer
                    if active_trip.start_odometer is not None and active_trip.end_odometer is not None:
                        active_trip.distance_km = (active_trip.end_odometer - active_trip.start_odometer)
            current_speed = t.get("speed")
            if current_speed is not None:
                if active_trip.max_speed is None or current_speed > active_trip.max_speed:
                    active_trip.max_speed = current_speed
            current_rpm = engine.get("rpm")
            if current_rpm:
                if active_trip.max_rpm is None or current_rpm > active_trip.max_rpm:
                    active_trip.max_rpm = current_rpm
                if active_trip.min_rpm is None or current_rpm < active_trip.min_rpm:
                    active_trip.min_rpm = current_rpm
            current_coolant = engine.get("coolant_temp")
            if current_coolant:
                if active_trip.max_coolant_temp is None or current_coolant > active_trip.max_coolant_temp:
                    active_trip.max_coolant_temp = current_coolant
            current_oil = engine.get("oil_temp")
            if current_oil:
                if active_trip.max_oil_temp is None or current_oil > active_trip.max_oil_temp:
                    active_trip.max_oil_temp = current_oil
            if active_trip.start_time:
                delta = current_time - active_trip.start_time
                active_trip.duration_seconds = int(delta.total_seconds())
        if not engine_running and active_trip:
            trip_samples = VehicleTelemetryHistory.query.filter_by(trip_id=active_trip.id).all()
            if trip_samples:
                speeds = [s.speed for s in trip_samples if s.speed is not None]
                if speeds:
                    active_trip.avg_speed = sum(speeds) / len(speeds)
                rpms = [s.engine_rpm for s in trip_samples if s.engine_rpm]
                if rpms:
                    active_trip.avg_rpm = sum(rpms) / len(rpms)
                consumptions = [s.consumption_l100km for s in trip_samples if s.consumption_l100km]
                if consumptions:
                    active_trip.avg_consumption_l100km = sum(consumptions) / len(consumptions)
                coolants = [s.coolant_temp for s in trip_samples if s.coolant_temp]
                if coolants:
                    active_trip.avg_coolant_temp = sum(coolants) / len(coolants)
                oils = [s.oil_temp for s in trip_samples if s.oil_temp]
                if oils:
                    active_trip.avg_oil_temp = sum(oils) / len(oils)
            if odometer_source == "manual":
                if active_trip.avg_speed is not None and active_trip.duration_seconds is not None:
                    estimated_distance = active_trip.avg_speed * (active_trip.duration_seconds / 3600.0)
                    active_trip.distance_km = estimated_distance
                    start_odometer = active_trip.start_odometer if active_trip.start_odometer is not None else live_row.odometer
                    start_odometer = start_odometer or 0
                    active_trip.start_odometer = int(round(start_odometer))
                    active_trip.end_odometer = int(round(start_odometer + estimated_distance))
                    live_row.odometer = active_trip.end_odometer
            if active_trip.distance_km and active_trip.avg_consumption_l100km:
                active_trip.total_fuel_used_l = (active_trip.distance_km / 100) * active_trip.avg_consumption_l100km
            active_trip.is_completed = True
            print(f"✅ Trip completed for vehicle {vehicle_id}, duration: {active_trip.duration_seconds}s")
        if odometer_source == "rpi":
            live_row.odometer = t.get("odometer")
        live_row.battery_voltage = battery.get("battery_voltage")
        live_row.battery_health = battery.get("health")
        live_row.engine_running = engine_running
        live_row.engine_rpm = engine.get("rpm")
        live_row.engine_load = engine.get("load")
        live_row.coolant_temp = engine.get("coolant_temp")
        live_row.oil_temp = engine.get("oil_temp")
        live_row.intake_air_temp = engine.get("intake_air_temp")
        live_row.consumption_lh = fuel.get("consumption_lh")
        live_row.consumption_l100km = fuel.get("consumption_l100km")
        live_row.maf = fuel.get("maf")
        live_row.fuel_type = fuel.get("type")
        live_row.speed = t.get("speed")
        live_row.created_at = current_time
        db.session.commit()
        print(f"✅ Telemetry saved for vehicle_id: {vehicle_id} (live + history) with trip_id: {trip_id}")
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error saving telemetry: {e}")
def _save_location_to_db(device_id: int, latitude: float, longitude: float, timestamp: datetime | None = None) -> int | None:
    try:
        device_vehicle = DeviceVehicle.query.filter_by(device_id=device_id).first()
        if not device_vehicle or not device_vehicle.last_vin_id:
            print(f"❌ No vehicle associated with device {device_id}")
            return None
        vehicle_id = device_vehicle.last_vin_id
        current_time = timestamp or datetime.utcnow()
        active_trip = Trip.query.filter_by(
            vehicle_id=vehicle_id,
            is_completed=False
        ).first()
        location_row = VehicleLocationHistory(
            vehicle_id=vehicle_id,
            trip_id=active_trip.id if active_trip else None,
            latitude=latitude,
            longitude=longitude,
            created_at=current_time
        )
        db.session.add(location_row)
        db.session.commit()
        print(f"✅ Location saved for vehicle_id: {vehicle_id}")
        return vehicle_id
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error saving location: {e}")
        return None
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
    t = _telemetry_payload(device_id, data)
    try:
        mark_device_online(device)
        db.session.commit()
    except Exception:
        db.session.rollback()
    _save_telemetry_to_db(device_id, t)
    socketio.emit("telemetry_update", t, room=f"device:{device_id}")
    emit("telemetry_ack", {"ok": True, "timestamp": t["timestamp"]})
@app.route("/api/connect", methods=["POST"])
def device_connect_syn():
    """
    3-way handshake - SYN
    ---
    tags:
      - Device Communication
    description: |
      Prvy krok trojcestneho handshaku pri pripajani zariadenia.
      **Testovanie cez Postman:**
      - Metoda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/connect`
      - Headers: `Content-Type: application/json`
      - Body (raw JSON):
        ```json
        {
          "device_id": 12345
        }
        ```
      **Ocakavana odpoved:**
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
        description: SYN-ACK odpoved
        schema:
          type: object
          properties:
            handshake:
              type: string
              example: "SYN-ACK"
            device_id:
              type: integer
      400:
        description: Chyba device_id
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
      Druhy krok trojcestneho handshaku - potvrdenie pripojenia.
      **Testovanie cez Postman:**
      - Metoda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/connect/ack`
      - Headers: `Content-Type: application/json`
      - Body (raw JSON):
        ```json
        {
          "device_id": 12345
        }
        ```
      **Ocakavana odpoved:**
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
        description: Pripojenie dokoncene
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
        description: Chyba device_id
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
        mark_device_online(device)
        db.session.commit()
        return jsonify({"status": "online", "device_id": device_id, "handshake": "ACK-complete"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route("/api/dtc/pattern-check/<vin>", methods=["GET"])
@jwt_required(optional=True)
def check_dtc_patterns(vin):
    """
    Kontrola kombinacii DTC kodov podla vzorov
    ---
    tags:
      - DTC
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        description: VIN vozidla
    responses:
      200:
        description: Zoznam najdenych vzorov alebo informacia, ze vozidlo nema aktivne DTC kody
      404:
        description: Vozidlo neexistuje
      500:
        description: Server error
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
                "vehicle_codes": list(active_dtcs)
            })
    return jsonify({
        "vin": vin,
        "active_dtc_codes": list(active_dtcs),
        "matched_patterns": matched_patterns
    }), 200
@app.route("/api/device/<int:device_id>/clear-dtcs", methods=["POST"])
@jwt_required()
def clear_device_dtcs(device_id):
    """
    Odoslanie prikazu na vymazanie DTC kodov
    ---
    tags:
      - DTC
    security:
      - bearerAuth: []
    description: |
      Odosle prikaz na vymazanie DTC kodov pre konkretne zariadenie.
      **Testovanie cez Postman:**
      - Metoda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/device/12345/clear-dtcs`
      - Headers:
        - `Content-Type: application/json`
        - `Authorization: Bearer <token>`
      - Body: ziadne
      **Ocakavana odpoved:**
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
        description: Prikaz odoslany
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
@app.route("/api/user-vehicle/<string:vin>", methods=["DELETE"])
@jwt_required()
def delete_user_vehicle(vin):
    """
    Vymazanie vztahu medzi pouzivatelom a vozidlom
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    description: |
        Vymaze zaznam z tabulky user_vehicles pre prihlaseneho pouzivatela a dane VIN.
        Tym sa vozidlo odstrani zo zoznamu vozidiel pouzivatela.
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        description: VIN cislo vozidla
    responses:
      200:
        description: Vozidlo uspesne odstranene
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            message:
              type: string
      404:
        description: Vozidlo neexistuje alebo nie je priradene pouzivatelovi
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
        if user.role == "admin":
            user_vehicle = UserVehicle.query.filter_by(vehicle_id=vehicle.id).first()
        else:
            user_vehicle = UserVehicle.query.filter_by(
                user_id=user_id,
                vehicle_id=vehicle.id
            ).first()
        if not user_vehicle:
            return jsonify({"error": "Vehicle not associated with this user"}), 404
        db.session.delete(user_vehicle)
        db.session.commit()
        return jsonify({
            "status": "success",
            "message": f"Vehicle {vin} removed from your list"
        }), 200
    except Exception as e:
        db.session.rollback()
        print("❌ DELETE USER VEHICLE ERROR:", e)
        return jsonify({"error": str(e)}), 500
@app.route("/api/device/<int:device_id>/read-dtcs", methods=["POST"])
@jwt_required()
def read_device_dtcs(device_id):
    """
    Odoslanie prikazu na nacitanie DTC kodov
    ---
    tags:
      - DTC
    security:
      - bearerAuth: []
    description: |
      Odosle prikaz na nacitanie aktualnych DTC kodov.
      **Testovanie cez Postman:**
      - Metoda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/device/12345/read-dtcs`
      - Headers:
        - `Content-Type: application/json`
        - `Authorization: Bearer <token>`
      - Body: ziadne
      **Ocakavana odpoved:**
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
        description: Prikaz odoslany
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
@app.route("/api/dtc-history-full", methods=["POST"])
@jwt_required(optional=True)
def dtc_history_full():
    """
    Kompletna historia DTC kodov podla VIN
    ---
    tags:
      - DTC
    security:
      - bearerAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - vin
          properties:
            vin:
              type: string
              example: "1HGCM82633A123456"
    responses:
      200:
        description: Kompletna historia DTC kodov s popisom
      400:
        description: Chyba VIN parameter
      404:
        description: Vozidlo neexistuje
      500:
        description: Server error
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
        print("❌ DTC HISTORY FULL ERROR:", e)
        return jsonify({"error": str(e)}), 500
@app.route("/api/device/<int:device_id>", methods=["DELETE"])
@jwt_required()
def delete_device(device_id):
    """
    Odstranenie zariadenia
    ---
    tags:
      - Devices
    security:
      - bearerAuth: []
    description: |
        Odstrani zariadenie a vsetky suvisiace data.
        **Testovanie cez Postman:**
        - Metoda: `DELETE`
        - URL: `http://car-diagnostics.onrender.com/api/device/12345`
        - Headers: `Authorization: Bearer <token>`
        **Ocakavana odpoved:**
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
        description: Zariadenie odstranene
      403:
        description: Nemate opravnenie odstranit toto zariadenie
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
        if user.role == "admin":
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()
        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404
        try:
            DeviceVehicle.query.filter_by(device_id=device_id).delete()
            PendingCommand.query.filter_by(device_id=device_id).delete()
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
@app.route("/api/add-device", methods=["POST"])
@jwt_required()
def add_device():
    """
    Pridanie noveho zariadenia
    ---
    tags:
      - Devices
    security:
      - bearerAuth: []
    description: |
      Prida nove diagnosticke zariadenie a priradi ho pouzivatelovi.
      **Testovanie cez Postman:**
      - Metoda: `POST`
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
      Pre admina je mozne priradit zariadenie inemu pouzivatelovi:
      ```json
      {
        "device_id": 12345,
        "user_id": 2
      }
      ```
      **Ocakavana odpoved:**
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
        description: Zariadenie pridane
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
        description: Chybny request
      401:
        description: Neautorizovany
      409:
        description: Zariadenie uz existuje
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
@app.route("/api/device/<int:device_id>/diagnostics", methods=["GET"])
@jwt_required()
def device_diagnostics(device_id):
    """
    Ziskanie diagnostickych udajov pre zariadenie
    ---
    tags:
      - Devices
    security:
      - bearerAuth: []
    description: |
      Vrati kompletne diagnosticke informacie pre zariadenie vratane VIN a DTC kodov.
      **Testovanie cez Postman:**
      - Metoda: `GET`
      - URL: `http://car-diagnostics.onrender.com/api/device/12345/diagnostics`
      - Headers:
        - `Authorization: Bearer <token>`
      - Body: ziadne
      **Ocakavana odpoved:**
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
        description: Diagnosticke udaje
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
        refresh_stale_device_statuses()
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
                    "recommended_action": get_recommended_action(d.severity or "medium"),
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
@app.route("/api/my-devices", methods=["GET"])
@jwt_required()
def my_devices():
    """
    Zoznam zariadeni prihlaseneho pouzivatela
    ---
    tags:
      - Devices
    security:
      - bearerAuth: []
    description: |
      Vrati zoznam vsetkych zariadeni patriacich prihlasenemu pouzivatelovi.
      **Testovanie cez Postman:**
      - Metoda: `GET`
      - URL: `http://car-diagnostics.onrender.com/api/my-devices`
      - Headers:
        - `Authorization: Bearer <token>`
      - Body: ziadne
      **Ocakavana odpoved:**
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
        description: Zoznam zariadeni
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
        refresh_stale_device_statuses()
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
@app.route("/api/login", methods=["POST"])
def login():
    """
    Prihlasenie pouzivatela
    ---
    tags:
      - Authentication
    consumes:
      - application/json
    produces:
      - application/json
    description: |
      Prihlasi pouzivatela podla emailu alebo username a vrati JWT token.
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - identifier
            - password
          properties:
            identifier:
              type: string
              description: Email alebo username pouzivatela
              example: "user@example.com"
            password:
              type: string
              example: "heslo123"
    responses:
      200:
        description: Uspesne prihlasenie
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            access_token:
              type: string
            role:
              type: string
            username:
              type: string
            email:
              type: string
      400:
        description: Chybajuce prihlasovacie udaje
      401:
        description: Nespravne prihlasovacie udaje
      415:
        description: Content-Type must be application/json
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        identifier = data.get("identifier")
        password = data.get("password")
        if not identifier or not password:
            return jsonify({"error": "Missing identifier or password"}), 400
        identifier = identifier.strip()
        user = User.query.filter(
            or_(
                func.lower(User.email) == identifier.lower(),
                func.lower(User.username) == identifier.lower()
            )
        ).first()
        if not user or user.password != password:
            return jsonify({"error": "Invalid credentials"}), 401
        access_token = create_access_token(identity=str(user.id))
        return jsonify({
            "status": "success",
            "access_token": access_token,
            "role": user.role,
            "username": user.username,
            "email": user.email
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route("/api/register", methods=["POST"])
def register():
    """
    Registracia noveho pouzivatela
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - username
            - email
            - password
          properties:
            username:
              type: string
              example: "jozef"
            email:
              type: string
              example: "user@example.com"
            password:
              type: string
              example: "heslo123"
    responses:
      201:
        description: Pouzivatel bol zaregistrovany
      400:
        description: Chybajuce alebo neplatne udaje
      409:
        description: Email alebo username uz existuje
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        username = data.get("username")
        email = data.get("email")
        password = data.get("password")
        if not username or not email or not password:
            return jsonify({"error": "Missing username, email or password"}), 400
        username = username.strip()
        if len(username) < 3:
            return jsonify({"error": "Username must be at least 3 characters long"}), 400
        if User.query.filter_by(email=email).first():
            return jsonify({"error": "Email already exists"}), 409
        if User.query.filter_by(username=username).first():
            return jsonify({"error": "Username already exists"}), 409
        new_user = User(username=username, email=email, password=password, role="user")
        db.session.add(new_user)
        db.session.commit()
        return jsonify({
            "status": "success",
            "message": "User registered"
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
@app.route("/api/load-dtc-codes", methods=["POST"])
def load_dtc_codes_from_csv():
    """
    Nacitanie DTC kodov z CSV suboru
    ---
    tags:
      - DTC
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - csv_url
          properties:
            csv_url:
              type: string
              example: "https://example.com/dtc.csv"
    responses:
      200:
        description: DTC kody boli nacitane
      400:
        description: Chyba csv_url alebo sa CSV nepodarilo stiahnut
      500:
        description: Server error
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
    Ziskanie popisu DTC kodu
    ---
    tags:
      - DTC
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - dtc_code
          properties:
            dtc_code:
              type: string
              example: "P0300"
    responses:
      200:
        description: Popis DTC kodu najdeny
      400:
        description: Chyba dtc_code parameter
      404:
        description: DTC kod sa nenasiel v databaze
      500:
        description: Server error
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
@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """
    Heartbeat od RPi (keep-alive + command polling)
    ---
    tags:
      - Device Communication
    description: |
      Endpoint pre pravidelne heartbeat requesty z RPi.
      Udrzuje zariadenie online a vracia cakajuce prikazy.
      **Testovanie cez Postman:**
      - Metoda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/heartbeat`
      - Headers: `Content-Type: application/json`
      - Body (raw JSON):
        ```json
        {
          "device_id": 12345
        }
        ```
      **Ocakavana odpoved (ziadny prikaz):**
      ```json
      {
        "status": "ok"
      }
      ```
      **Ocakavana odpoved (s prikazom):**
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
        description: Chyba device_id
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
            device = Device(id=device_id, status=True, last_seen=datetime.utcnow())
            db.session.add(device)
        else:
            mark_device_online(device)
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
    Manualne spustenie prikazu na zariadeni
    ---
    tags:
      - Device Communication
    description: |
      Odosle prikaz do fronty pre konkretne zariadenie.
      Zariadenie si ho vyzdvihne pri najblizsom heartbeat.
      **Testovanie cez Postman:**
      - Metoda: `POST`
      - URL: `http://car-diagnostics.onrender.com/api/trigger`
      - Headers: `Content-Type: application/json`
      - Body (raw JSON):
        ```json
        {
          "device_id": 12345,
          "command": "GET_VIN"
        }
        ```
      **Dostupne prikazy:**
      - `GET_VIN` - nacitanie VIN cisla
      - `GET_DTCS_PERM` - nacitanie aktivnych DTC kodov
      - `GET_DTCS_PEND` - nacitanie pending DTC kodov
      - `GET_RPM` - nacitanie otacok motora
      - `GET_TEMP` - nacitanie teploty
      - `CLEAR_DTCS` - vymazanie DTC kodov
      **Ocakavana odpoved:**
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
        description: Prikaz zaradeny do fronty
        schema:
          type: object
          properties:
            status:
              type: string
              example: "queued"
            command:
              type: string
      400:
        description: Neplatny command
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
@app.route("/api/can", methods=["POST"])
def receive_can_packet():
    """
    Prijem CAN packetov z RPi (VIN, DTC, clear_status, telemetria)
    ---
    tags:
      - Device Communication
    description: |
      Hlavny endpoint pre prijem dat z RPi zariadenia.
      **Typy sprav:**
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
      **2. Odoslanie DTC kodu:**
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
      **Ocakavane odpovede:**
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
        description: Data spracovane
      200:
        description: OK
      400:
        description: Chybny request
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
        year = payload.get("year")
        model = payload.get("model")
        brand = payload.get("brand")
        engine = payload.get("engine")
        if device_id is None:
            return jsonify({"error": "Missing 'device_id'"}), 400
        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": f"Device {device_id} not found"}), 404
        if any(k in payload for k in ["odometer", "battery", "engine", "fuel", "speed"]) and not payload.get("vin"):
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({
                    "error": "No VIN associated with this device",
                    "message": "Please send VIN first"
                }), 400
            t = _telemetry_payload(int(device_id), payload)
            mark_device_online(device)
            db.session.commit()
            _save_telemetry_to_db(int(device_id), t)
            socketio.emit("telemetry_update", t, room=f"device:{int(device_id)}")
            return jsonify({
                "status": "telemetry stored",
                "device_id": int(device_id),
                "vehicle_id": state.last_vin_id,
                "timestamp": t["timestamp"]
            }), 201
        if clear_status is not None:
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated for clear"}), 400
        if clear_status == "ok":
            DTCCodeActive.query.filter_by(vin_id=state.last_vin_id).delete()
            db.session.commit()
            socketio.emit("clear_confirmation", {
                "device_id": device_id,
                "status": "success",
                "vin_id": state.last_vin_id,
                "timestamp": datetime.utcnow().isoformat()
            })
            return jsonify({"status": "DTC cleared", "vin_id": state.last_vin_id}), 200
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
            mark_device_online(device)
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state:
                state = DeviceVehicle(device_id=device_id, last_vin_id=vehicle.id)
                db.session.add(state)
            else:
                state.last_vin_id = vehicle.id
            if device.user_id:
                user_vehicle = UserVehicle.query.filter_by(
                    user_id=device.user_id,
                    vehicle_id=vehicle.id
                ).first()
                if not user_vehicle:
                    user_vehicle = UserVehicle(
                        user_id=device.user_id,
                        vehicle_id=vehicle.id
                    )
                    db.session.add(user_vehicle)
                    print(f"✅ UserVehicle created: user {device.user_id} - vehicle {vehicle.id}")
            db.session.commit()
            return jsonify({
                "status": "VIN stored",
                "vin": vin,
                "brand": brand,
                "year": vehicle.year,
                "model": vehicle.model,
                "engine": vehicle.engine
            }), 201
        if dtc_code:
            dtc_code = dtc_code.strip().upper()
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated with this device"}), 400
            vehicle = Vehicle.query.get(state.last_vin_id)
            if not vehicle:
                return jsonify({"error": "Vehicle not found"}), 404
            if dtc_code in {"NO_DTCS", "NO_DTCS", "NO CODES", "NO_CODES"}:
                DTCCodeActive.query.filter_by(vin_id=vehicle.id).delete()
                db.session.commit()
                socketio.emit("dtc_update", {
                    "device_id": device_id,
                    "dtc_code": None,
                    "severity": None,
                    "description": None,
                    "timestamp": datetime.utcnow().isoformat(),
                    "active_dtcs_cleared": True
                })
                return jsonify({
                    "status": "no_dtcs",
                    "message": "No active DTCs reported",
                    "vin": vehicle.vin
                }), 200
            meaning = DtcCodeMeaning.query.filter(
                db.func.lower(DtcCodeMeaning.dtc_code) == dtc_code.lower()
            ).first()
            description = meaning.dtc_description if meaning else ""
            severity = detect_severity_from_description(description)
            recommended_action = get_recommended_action(severity)
            db.session.add(DTCCodeHistory(vin_id=vehicle.id, dtc_code=dtc_code, severity=severity))
            DTCCodeActive.query.filter_by(vin_id=vehicle.id, dtc_code=dtc_code).delete()
            db.session.add(DTCCodeActive(vin_id=vehicle.id, dtc_code=dtc_code, severity=severity))
            db.session.commit()
            owner_links = UserVehicle.query.filter_by(vehicle_id=vehicle.id).all()
            vehicle_info = {
                "vin": vehicle.vin,
                "brand": vehicle.brand,
                "model": vehicle.model,
                "year": vehicle.year,
            }
            for owner_link in owner_links:
                owner = User.query.get(owner_link.user_id)
                if owner and owner.email:
                    socketio.start_background_task(
                        send_dtc_email_notification,
                        owner.email,
                        vehicle_info,
                        dtc_code,
                        description,
                        severity
                    )
            socketio.emit("dtc_update", {
                "device_id": device_id,
                "dtc_code": dtc_code,
                "severity": severity,
                "recommended_action": recommended_action,
                "description": description,
                "timestamp": datetime.utcnow().isoformat()
            })
            return jsonify({
                "status": "DTC stored",
                "vin": vehicle.vin,
                "dtc": dtc_code,
                "severity": severity,
                "recommended_action": recommended_action
            }), 201
        return jsonify({"status": "ignored", "message": "No recognized payload fields"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
@app.route("/api/location", methods=["POST"])
def receive_location():
    """
    Prijem GPS polohy z RPi
    ---
    tags:
      - Device Communication
    description: |
      Samostatny endpoint pre prijem GPS polohy z RPi.
      **Priklad requestu:**
      ```json
      {
        "device_id": 12345,
        "latitude": 48.1486,
        "longitude": 17.1077
      }
      ```
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - device_id
            - latitude
            - longitude
          properties:
            device_id:
              type: integer
              example: 12345
            latitude:
              type: number
              example: 48.1486
            longitude:
              type: number
              example: 17.1077
            timestamp:
              type: number
              example: 1710500000.123
    responses:
      201:
        description: Poloha ulozena
      400:
        description: Chybne data
      404:
        description: Device neexistuje
      500:
        description: Server error
    """
    try:
        payload = request.get_json()
        device_id_raw = payload.get("device_id")
        latitude_raw = payload.get("latitude")
        longitude_raw = payload.get("longitude")
        timestamp_raw = payload.get("timestamp")
        try:
            device_id = int(device_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "device_id must be an integer"}), 400
        try:
            latitude = float(latitude_raw)
            longitude = float(longitude_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "latitude and longitude must be numbers"}), 400
        if latitude < -90 or latitude > 90:
            return jsonify({"error": "latitude must be between -90 and 90"}), 400
        if longitude < -180 or longitude > 180:
            return jsonify({"error": "longitude must be between -180 and 180"}), 400
        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": f"Device {device_id} not found"}), 404
        if timestamp_raw is None:
            location_timestamp = datetime.utcnow()
        else:
            try:
                location_timestamp = datetime.utcfromtimestamp(float(timestamp_raw))
            except (TypeError, ValueError):
                return jsonify({"error": "timestamp must be unix timestamp in seconds"}), 400
        vehicle_id = _save_location_to_db(
            device_id=device_id,
            latitude=latitude,
            longitude=longitude,
            timestamp=location_timestamp
        )
        if not vehicle_id:
            return jsonify({
                "error": "No VIN associated with this device",
                "message": "Please send VIN first"
            }), 400
        mark_device_online(device)
        db.session.commit()
        ws_payload = {
            "device_id": device_id,
            "vehicle_id": vehicle_id,
            "latitude": latitude,
            "longitude": longitude,
            "timestamp": _iso(location_timestamp)
        }
        socketio.emit("location_update", ws_payload, room=f"device:{device_id}")
        return jsonify({
            "status": "location stored",
            "device_id": device_id,
            "vehicle_id": vehicle_id,
            "latitude": latitude,
            "longitude": longitude,
            "timestamp": _iso(location_timestamp)
        }), 201
    except Exception as e:
        db.session.rollback()
        print("❌ LOCATION ERROR:", e)
        return jsonify({"error": str(e)}), 500
@app.route("/api/vehicle/<string:vin>/odometer", methods=["GET"])
@jwt_required()
def get_vehicle_odometer(vin):
    """
    Ziskanie odometra vozidla podla VIN
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        description: VIN vozidla
    responses:
      200:
        description: Aktualny odometer vozidla alebo prazdna hodnota
      403:
        description: Vozidlo nepatri pouzivatelovi
      404:
        description: Pouzivatel alebo vozidlo neexistuje
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
        if user.role != "admin":
            user_vehicle = UserVehicle.query.filter_by(user_id=user_id, vehicle_id=vehicle.id).first()
            if not user_vehicle:
                return jsonify({"error": "Vehicle not owned by user"}), 403
        live_row = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle.id).first()
        if not live_row:
            return jsonify({
                "status": "success",
                "vin": vehicle.vin,
                "vehicle_id": vehicle.id,
                "odometer": None,
                "odometer_source": "rpi"
            }), 200
        return jsonify({
            "status": "success",
            "vin": vehicle.vin,
            "vehicle_id": vehicle.id,
            "odometer": live_row.odometer,
            "odometer_source": live_row.odometer_source or "rpi"
        }), 200
    except Exception as e:
        print("❌ GET VEHICLE ODOMETER ERROR:", e)
        return jsonify({"error": str(e)}), 500
@app.route("/api/vehicle/<string:vin>/odometer", methods=["PUT"])
@jwt_required()
def update_vehicle_odometer(vin):
    """
    Aktualizacia zdroja a hodnoty odometra vozidla
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        description: VIN vozidla
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - odometer_source
          properties:
            odometer_source:
              type: string
              enum: [manual, rpi]
              example: manual
            odometer:
              type: number
              example: 123456
    responses:
      200:
        description: Odometer bol aktualizovany
      400:
        description: Neplatny zdroj alebo hodnota odometra
      403:
        description: Vozidlo nepatri pouzivatelovi
      404:
        description: Pouzivatel alebo vozidlo neexistuje
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
        if user.role != "admin":
            user_vehicle = UserVehicle.query.filter_by(user_id=user_id, vehicle_id=vehicle.id).first()
            if not user_vehicle:
                return jsonify({"error": "Vehicle not owned by user"}), 403
        payload = request.get_json()
        odometer_source = (payload.get("odometer_source") or "").strip().lower()
        odometer_value = payload.get("odometer")
        if odometer_source not in {"manual", "rpi"}:
            return jsonify({"error": "odometer_source must be 'manual' or 'rpi'"}), 400
        live_row = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle.id).first()
        if not live_row:
            live_row = VehicleTelemetryLive(vehicle_id=vehicle.id)
            db.session.add(live_row)
            db.session.flush()
        live_row.odometer_source = odometer_source
        if odometer_source == "manual":
            if odometer_value is None:
                return jsonify({"error": "Missing odometer for manual source"}), 400
            try:
                live_row.odometer = int(round(float(odometer_value)))
            except (TypeError, ValueError):
                return jsonify({"error": "odometer must be a number"}), 400
        db.session.commit()
        return jsonify({
            "status": "success",
            "vin": vehicle.vin,
            "vehicle_id": vehicle.id,
            "odometer": live_row.odometer,
            "odometer_source": live_row.odometer_source
        }), 200
    except Exception as e:
        db.session.rollback()
        print("❌ UPDATE VEHICLE ODOMETER ERROR:", e)
        return jsonify({"error": str(e)}), 500
@app.route("/api/dtc-history/<vin>", methods=["GET"])
@jwt_required()
def get_dtc_history(vin):
    """
    Jednoducha historia DTC kodov podla VIN
    ---
    tags:
      - DTC
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        description: VIN vozidla
    responses:
      200:
        description: Historia DTC kodov
      404:
        description: Vozidlo neexistuje
    """
    vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
    if not vehicle:
        return jsonify({"error": "Vehicle not found"}), 404
    history = DTCCodeHistory.query.filter_by(vin_id=vehicle.id).order_by(DTCCodeHistory.created_at.desc()).all()
    return jsonify({
        "vin": vin,
        "dtc_history": [{"dtc_code": d.dtc_code, "created_at": d.created_at.isoformat()} for d in history]
    }), 200
@app.route("/api/device_offline/<int:device_id>", methods=["POST"])
def device_offline(device_id):
    """
    Nastavenie zariadenia ako offline
    ---
    tags:
      - Devices
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
        description: ID zariadenia
    responses:
      200:
        description: Zariadenie bolo nastavene ako offline
      404:
        description: Zariadenie neexistuje
      500:
        description: Server error
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
def _get_vehicle_id_from_device(device_id: int) -> int | None:
    """Pomocná funkcia na získanie vehicle_id z device_id"""
    device_vehicle = DeviceVehicle.query.filter_by(device_id=device_id).first()
    if device_vehicle and device_vehicle.last_vin_id:
        return device_vehicle.last_vin_id
    return None
def _get_latest_telemetry(device_id: int) -> VehicleTelemetryLive | None:
    """Získa najnovšiu live telemetriu pre zariadenie (cez vehicle_id)."""
    vehicle_id = _get_vehicle_id_from_device(device_id)
    if not vehicle_id:
        return None
    return VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle_id).first()
@app.route("/api/device/<int:device_id>/odometer", methods=["GET"])
@jwt_required()
def get_device_odometer(device_id):
    """
    Ziskanie posledneho znameho stavu odometra zariadenia
    ---
    tags:
      - Telemetry
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Posledny znamy odometer
      404:
        description: Zariadenie neexistuje alebo nema odometer data
    """
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
        "vehicle_id": t.vehicle_id,
        "odometer": int(t.odometer),
        "odometer_source": t.odometer_source or "rpi",
        "timestamp": _iso(t.created_at)
    }), 200
@app.route("/api/device/<int:device_id>/battery", methods=["GET"])
@jwt_required()
def get_device_battery(device_id):
    """
    Ziskanie poslednych bateriovych dat zariadenia
    ---
    tags:
      - Telemetry
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Posledne bateriove data
      404:
        description: Zariadenie neexistuje alebo nema battery data
    """
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
        "vehicle_id": t.vehicle_id,
        "battery_voltage": float(t.battery_voltage),
        "health": t.battery_health or "unknown",
        "timestamp": _iso(t.created_at)
    }), 200
@app.route("/api/device/<int:device_id>/engine", methods=["GET"])
@jwt_required()
def get_device_engine(device_id):
    """
    Ziskanie poslednych motorovych dat zariadenia
    ---
    tags:
      - Telemetry
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Posledne motorove data
      404:
        description: Zariadenie neexistuje alebo nema engine data
    """
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
        "vehicle_id": t.vehicle_id,
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
    Ziskanie poslednych palivovych dat zariadenia
    ---
    tags:
      - Telemetry
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Posledne palivove data
      404:
        description: Zariadenie neexistuje alebo nema fuel data
    """
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
        "vehicle_id": t.vehicle_id,
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
    Ziskanie poslednej rychlosti zariadenia
    ---
    tags:
      - Telemetry
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Posledna rychlost
      404:
        description: Zariadenie neexistuje alebo nema speed data
    """
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
        "vehicle_id": t.vehicle_id,
        "speed": int(t.speed),
        "timestamp": _iso(t.created_at)
    }), 200
@app.route("/api/device/<int:device_id>/location", methods=["GET"])
@jwt_required()
def get_device_location(device_id):
    """
    Ziskanie poslednej GPS polohy zariadenia
    ---
    tags:
      - Telemetry
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Posledna znama GPS poloha
      404:
        description: Zariadenie, VIN alebo location data neexistuju
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        if user.role != "admin":
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()
            if not device:
                return jsonify({"error": "Device not found or not owned by user"}), 404
        vehicle_id = _get_vehicle_id_from_device(device_id)
        if not vehicle_id:
            return jsonify({"error": "No VIN associated"}), 404
        latest_location = (
            VehicleLocationHistory.query
            .filter_by(vehicle_id=vehicle_id)
            .order_by(VehicleLocationHistory.created_at.desc())
            .first()
        )
        if not latest_location:
            return jsonify({"error": "No location data"}), 404
        return jsonify({
            "status": "success",
            "device_id": device_id,
            "vehicle_id": vehicle_id,
            "location": {
                "latitude": latest_location.latitude,
                "longitude": latest_location.longitude
            },
            "timestamp": _iso(latest_location.created_at)
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route("/api/device/<int:device_id>/live", methods=["GET"])
@jwt_required()
def get_device_live(device_id):
    """
    Ziskanie kompletnych live dat zariadenia
    ---
    tags:
      - Telemetry
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: device_id
        required: true
        type: integer
    responses:
      200:
        description: Kompletne live data zariadenia
      404:
        description: Zariadenie, VIN alebo live data neexistuju
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if user.role != "admin":
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()
            if not device:
                return jsonify({"error": "Device not found"}), 404
        vehicle_id = _get_vehicle_id_from_device(device_id)
        if not vehicle_id:
            return jsonify({"error": "No VIN associated"}), 404
        live = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle_id).first()
        if not live:
            return jsonify({"error": "No live data"}), 404
        return jsonify({
            "status": "success",
            "device_id": device_id,
            "vehicle_id": vehicle_id,
            "odometer": live.odometer,
            "odometer_source": live.odometer_source or "rpi",
            "battery": {
                "voltage": live.battery_voltage,
                "health": live.battery_health
            },
            "engine": {
                "running": live.engine_running,
                "rpm": live.engine_rpm,
                "load": live.engine_load,
                "coolant_temp": live.coolant_temp,
                "oil_temp": live.oil_temp,
                "intake_air_temp": live.intake_air_temp
            },
            "fuel": {
                "consumption_lh": live.consumption_lh,
                "consumption_l100km": live.consumption_l100km,
                "maf": live.maf,
                "type": live.fuel_type
            },
            "speed": live.speed,
            "timestamp": _iso(live.created_at)
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route("/api/vehicles/telemetry-comparison", methods=["GET"])
@jwt_required()
def vehicles_telemetry_comparison():
    """
    Porovnanie telemetrie pre vsetky vozidla pouzivatela
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    description: |
        Vrati statisticke udaje (priemery) z historickych telemetrickych dat.
        Zobrazuje vsetky vozidla, ktore boli kedy priradene k pouzivatelovi.
        Admin vidi vsetky vozidla.
    """
    try:
        refresh_stale_device_statuses()
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        vehicles_data = []
        online_count = 0
        if user.role == "admin":
            vehicles = Vehicle.query.all()
            devices = Device.query.all()
        else:
            user_vehicles = UserVehicle.query.filter_by(user_id=user_id).all()
            vehicles = [uv.vehicle for uv in user_vehicles]
            devices = Device.query.filter_by(user_id=user_id).all()
        vehicle_device_map = {}
        for d in devices:
            if d.link and len(d.link) > 0 and d.link[0].last_vin_id:
                vehicle_id = d.link[0].last_vin_id
                existing = vehicle_device_map.get(vehicle_id)
                if existing is None:
                    vehicle_device_map[vehicle_id] = d
                else:
                    if not existing.status and d.status:
                        vehicle_device_map[vehicle_id] = d
        for vehicle in vehicles:
            owner_links = UserVehicle.query.filter_by(vehicle_id=vehicle.id).all()
            owner_user_ids = [link.user_id for link in owner_links]
            primary_user_id = owner_user_ids[0] if owner_user_ids else None
            linked_device = vehicle_device_map.get(vehicle.id)
            device_status = linked_device.status if linked_device else False
            device_id = linked_device.id if linked_device else None
            if device_status:
                online_count += 1
            stats = db.session.query(
                func.avg(VehicleTelemetryHistory.engine_rpm).label("avg_rpm"),
                func.avg(VehicleTelemetryHistory.speed).label("avg_speed"),
                func.avg(VehicleTelemetryHistory.consumption_l100km).label("avg_consumption"),
                func.max(VehicleTelemetryHistory.engine_rpm).label("max_rpm"),
                func.min(VehicleTelemetryHistory.engine_rpm).label("min_rpm"),
                func.count(VehicleTelemetryHistory.id).label("samples")
            ).filter(
                VehicleTelemetryHistory.vehicle_id == vehicle.id
            ).first()
            live = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle.id).first()
            vehicles_data.append({
                "device_id": device_id,
                "vin": vehicle.vin,
                "brand": vehicle.brand,
                "model": vehicle.model,
                "year": vehicle.year,
                "engine": vehicle.engine,
                "online": device_status,
                "user_id": primary_user_id,
                "owner_user_ids": owner_user_ids,
                "statistics": {
                    "avg_rpm": round(stats.avg_rpm) if stats and stats.avg_rpm is not None else None,
                    "avg_speed": round(stats.avg_speed) if stats and stats.avg_speed is not None else None,
                    "avg_consumption": round(stats.avg_consumption, 1) if stats and stats.avg_consumption is not None else None,
                    "max_rpm": stats.max_rpm if stats and stats.max_rpm is not None else None,
                    "min_rpm": stats.min_rpm if stats and stats.min_rpm is not None else None,
                    "total_odometer": live.odometer if live else None,
                    "odometer_source": (live.odometer_source if live and live.odometer_source else "rpi"),
                    "samples": stats.samples if stats and stats.samples else 0
                }
            })
        total_stats = {
            "total_vehicles": len(vehicles_data),
            "online_vehicles": online_count,
            "total_samples": sum(v.get("statistics", {}).get("samples", 0) for v in vehicles_data)
        }
        return jsonify({
            "status": "success",
            "vehicles": vehicles_data,
            "summary": total_stats
        }), 200
    except Exception as e:
        print("❌ VEHICLES TELEMETRY COMPARISON ERROR:", e)
        return jsonify({"error": str(e)}), 500
@app.route("/api/all", methods=["GET"])
def show_all():
    """
    Vypis vsetkych vozidiel a DTC kodov
    ---
    tags:
      - System
    responses:
      200:
        description: Zoznam vozidiel s aktivnymi a historickymi DTC kodmi
      500:
        description: Server error
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
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
