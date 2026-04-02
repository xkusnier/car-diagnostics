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
# Pridaj tento import k ostatným importom
from sqlalchemy import func, or_
# ✅ NEW: WebSocket (Socket.IO)
from flask_socketio import SocketIO, emit, join_room
import eventlet
import re
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
    # ✅ ODSTRÁŇ TENTO RIADOK: response.headers.add('Access-Control-Allow-Origin', '*')
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

class Trip(db.Model):
    __tablename__ = "trips"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("trips", lazy="dynamic"))
    
    # Časové údaje
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)  # v sekundách
    
    # Počet záznamov v tejto jazde
    samples_count = db.Column(db.Integer, default=0)
    
    # Odometer
    start_odometer = db.Column(db.Integer, nullable=True)
    end_odometer = db.Column(db.Integer, nullable=True)
    distance_km = db.Column(db.Float, nullable=True)  # prejdená vzdialenosť
    
    # Štatistiky rýchlosti
    avg_speed = db.Column(db.Float, nullable=True)
    max_speed = db.Column(db.Integer, nullable=True)
    
    # Štatistiky otáčok
    avg_rpm = db.Column(db.Float, nullable=True)
    max_rpm = db.Column(db.Integer, nullable=True)
    min_rpm = db.Column(db.Integer, nullable=True)
    
    # Štatistiky spotreby
    avg_consumption_l100km = db.Column(db.Float, nullable=True)
    total_fuel_used_l = db.Column(db.Float, nullable=True)  # celková spotreba v litroch
    
    # Teploty
    avg_coolant_temp = db.Column(db.Float, nullable=True)
    max_coolant_temp = db.Column(db.Integer, nullable=True)
    avg_oil_temp = db.Column(db.Float, nullable=True)
    max_oil_temp = db.Column(db.Integer, nullable=True)
    
    # Stav motora
    engine_starts = db.Column(db.Integer, default=1)  # počet štartov v tejto jazde
    
    # Či je jazda dokončená
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
    
    # Vzťahy
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


class VehicleTelemetryLive(db.Model):
    __tablename__ = "vehicle_telemetry_live"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, unique=True, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("live_telemetry", uselist=False))
    
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    

class VehicleTelemetryHistory(db.Model):
    __tablename__ = "vehicle_telemetry_history"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("telemetry_history", lazy="dynamic"))
    
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id"), nullable=True, index=True)
    trip = db.relationship("Trip", backref=db.backref("telemetry_samples", lazy="dynamic"))
    
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

    accel_x = db.Column(db.Float, nullable=True)
    accel_y = db.Column(db.Float, nullable=True)
    accel_z = db.Column(db.Float, nullable=True)

    gyro_x = db.Column(db.Float, nullable=True)
    gyro_y = db.Column(db.Float, nullable=True)
    gyro_z = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    device = db.relationship("Device", backref=db.backref("driving_events", lazy="dynamic"))
    vehicle = db.relationship("Vehicle", backref=db.backref("driving_events", lazy="dynamic"))
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

ALLOWED_DRIVING_EVENT_TYPES = {
    "HARD_BRAKE",
    "SHARP_ACCELERATION",
    "HARD_TURN",
    "CRASH",
}


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
    Príjem jazdných udalostí z RPi / gyroskopu / akcelerometra
    ---
    tags:
      - Driving Events
    description: |
      Endpoint na uloženie jazdných udalostí ako:
      - HARD_BRAKE
      - SHARP_ACCELERATION
      - HARD_TURN
      - CRASH

      Dáta môžu obsahovať VIN priamo, alebo sa vozidlo dohľadá podľa device_id.

      **Príklad requestu:**
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
        description: Event úspešne uložený
      400:
        description: Chýbajúce alebo neplatné dáta
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
        device.status = True
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
    Validácia VIN formátu a checksumu
    ---
    tags:
      - VIN
    security:
      - bearerAuth: []
    description: |
      Overí:
      - či VIN má správny formát
      - či má správny checksum
      - či sa vozidlo nachádza v databáze

      Možné stavy:
      - invalid_format
      - invalid_checksum
      - not_found
      - valid

      **Príklad requestu:**
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
        description: Validácia vykonaná
      400:
        description: Chýbajúci VIN
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
    História jazdných udalostí pre konkrétne zariadenie
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
        description: Zoznam udalostí zariadenia
      404:
        description: Device neexistuje alebo nepatrí používateľovi
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
    História jazdných udalostí pre konkrétne vozidlo podľa VIN
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
        description: Zoznam udalostí vozidla
      403:
        description: Vozidlo nepatrí používateľovi
      404:
        description: Vehicle neexistuje
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
# =========================
# INIT / HEALTH
# =========================
@app.route("/api/health", methods=["GET"])
def health_check():
    """
    Jednoduchý health check endpoint na prebúdzanie servera
    """
    return jsonify({"status": "ok"}), 200

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

@app.route("/api/dashboard-summary", methods=["GET"])
@jwt_required()
def dashboard_summary():
    """
    Domovská stránka - súhrn používateľských dát a vozidlá s aktívnymi chybami
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
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)

        if not user:
            return jsonify({"error": "User not found"}), 404

        # Devices
        if user.role == "admin":
            devices = Device.query.all()
        else:
            devices = Device.query.filter_by(user_id=user_id).all()

        total_devices = len(devices)

        # Vehicles
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
    Získanie všetkých jázd pre konkrétne vozidlo
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
        description: Zoznam jázd
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        # Skontroluj či vozidlo patrí používateľovi
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
            
        user_vehicle = UserVehicle.query.filter_by(
            user_id=user_id, 
            vehicle_id=vehicle.id
        ).first()
        
        if not user_vehicle and user.role != "admin":
            return jsonify({"error": "Vehicle not owned by user"}), 403

        # Získaj všetky dokončené jazdy pre toto vozidlo
        trips = Trip.query.filter_by(
            vehicle_id=vehicle.id, 
            is_completed=True
        ).order_by(Trip.start_time.desc()).all()
        
        # Priprav odpoveď
        trips_data = []
        for trip in trips:
            # Zisti priemernú spotrebu z histórie ak nie je vypočítaná
            if not trip.avg_consumption_l100km and trip.samples_count > 0:
                consumptions = db.session.query(
                    func.avg(VehicleTelemetryHistory.consumption_l100km)
                ).filter(
                    VehicleTelemetryHistory.trip_id == trip.id,
                    VehicleTelemetryHistory.consumption_l100km.isnot(None)
                ).scalar()
                trip.avg_consumption_l100km = consumptions
            
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
                "end_odometer": trip.end_odometer
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
        
        # Detekcia jazdy podľa stavu motora
        engine_running = engine.get("running")
        
        # Získať aktuálnu aktívnu jazdu pre toto vozidlo
        active_trip = Trip.query.filter_by(
            vehicle_id=vehicle_id, 
            is_completed=False
        ).first()
        
        current_time = datetime.utcnow()
        
        # Ak motor práve naštartoval (predchádzajúci stav nebol running, teraz je running)
        if engine_running and not active_trip:
            # Vytvor novú jazdu
            active_trip = Trip(
                vehicle_id=vehicle_id,
                start_time=current_time,
                start_odometer=t.get("odometer"),
                engine_starts=1,
                is_completed=False
            )
            db.session.add(active_trip)
            db.session.flush()  # Získa ID bez commitu
            print(f"✅ New trip started for vehicle {vehicle_id} at {current_time}")
        
        # trip_id pre history záznam
        trip_id = active_trip.id if active_trip else None

        # 1️⃣ ULOŽ DO HISTORY (každý packet)
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
            trip_id=trip_id  # Priradíme trip_id
        )
        db.session.add(history_row)
        
        # Aktualizuj štatistiky aktívnej jazdy
        if active_trip:
            # Základné počítadlá
            active_trip.samples_count += 1
            active_trip.end_time = current_time
            
            # Odometer a vzdialenosť
            current_odometer = t.get("odometer")
            if current_odometer:
                if active_trip.start_odometer is None:
                    active_trip.start_odometer = current_odometer
                active_trip.end_odometer = current_odometer
                if active_trip.start_odometer and active_trip.end_odometer:
                    active_trip.distance_km = (active_trip.end_odometer - active_trip.start_odometer)
            
            # Rýchlosť
            current_speed = t.get("speed")
            if current_speed is not None:
                if active_trip.max_speed is None or current_speed > active_trip.max_speed:
                    active_trip.max_speed = current_speed
                # Pre priemer budeme počítať až na konci
            
            # Otáčky
            current_rpm = engine.get("rpm")
            if current_rpm:
                if active_trip.max_rpm is None or current_rpm > active_trip.max_rpm:
                    active_trip.max_rpm = current_rpm
                if active_trip.min_rpm is None or current_rpm < active_trip.min_rpm:
                    active_trip.min_rpm = current_rpm
            
            # Teploty
            current_coolant = engine.get("coolant_temp")
            if current_coolant:
                if active_trip.max_coolant_temp is None or current_coolant > active_trip.max_coolant_temp:
                    active_trip.max_coolant_temp = current_coolant
            
            current_oil = engine.get("oil_temp")
            if current_oil:
                if active_trip.max_oil_temp is None or current_oil > active_trip.max_oil_temp:
                    active_trip.max_oil_temp = current_oil
            
            # Spotreba (ak máme consumption_l100km, môžeme počítať)
            current_consumption = fuel.get("consumption_l100km")
            if current_consumption:
                # Pre priemer budeme počítať až na konci
                pass
            # Trvanie
            if active_trip.start_time:
                delta = current_time - active_trip.start_time
                active_trip.duration_seconds = int(delta.total_seconds())
        
        # Ak motor práve zastavil (bežal a teraz nebeží)
        if not engine_running and active_trip:
            # Vypočítaj priemery pred ukončením
            # Získaj všetky záznamy z tejto jazdy
            trip_samples = VehicleTelemetryHistory.query.filter_by(trip_id=active_trip.id).all()
            
            if trip_samples:
                # Priemerná rýchlosť (len keď speed > 0)
                speeds = [s.speed for s in trip_samples if s.speed and s.speed > 0]
                if speeds:
                    active_trip.avg_speed = sum(speeds) / len(speeds)
                
                # Priemerné otáčky
                rpms = [s.engine_rpm for s in trip_samples if s.engine_rpm]
                if rpms:
                    active_trip.avg_rpm = sum(rpms) / len(rpms)
                
                # Priemerná spotreba
                consumptions = [s.consumption_l100km for s in trip_samples if s.consumption_l100km]
                if consumptions:
                    active_trip.avg_consumption_l100km = sum(consumptions) / len(consumptions)
                
                # Priemerné teploty
                coolants = [s.coolant_temp for s in trip_samples if s.coolant_temp]
                if coolants:
                    active_trip.avg_coolant_temp = sum(coolants) / len(coolants)
                
                oils = [s.oil_temp for s in trip_samples if s.oil_temp]
                if oils:
                    active_trip.avg_oil_temp = sum(oils) / len(oils)
                
                # Celková spotreba v litroch (približne)
                # consumption_l100km je spotreba na 100km, prepočet na litre podľa vzdialenosti
                if active_trip.distance_km and active_trip.avg_consumption_l100km:
                    active_trip.total_fuel_used_l = (active_trip.distance_km / 100) * active_trip.avg_consumption_l100km
            
            # Ukonči jazdu
            active_trip.is_completed = True
            print(f"✅ Trip completed for vehicle {vehicle_id}, duration: {active_trip.duration_seconds}s")

        # 2️⃣ ULOŽ DO LIVE (posledný packet) - UPDATE alebo INSERT
        live_row = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle_id).first()
        if live_row:
            # Update existujúceho
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
        else:
            # Nový záznam
            live_row = VehicleTelemetryLive(
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
            )
            db.session.add(live_row)

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

@app.route("/api/user-vehicle/<string:vin>", methods=["DELETE"])
@jwt_required()
def delete_user_vehicle(vin):
    """
    Vymazanie vzťahu medzi používateľom a vozidlom
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    description: |
        Vymaže záznam z tabuľky user_vehicles pre prihláseného používateľa a dané VIN.
        Tým sa vozidlo odstráni zo zoznamu vozidiel používateľa.
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        description: VIN číslo vozidla
    responses:
      200:
        description: Vozidlo úspešne odstránené
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            message:
              type: string
      404:
        description: Vozidlo neexistuje alebo nie je priradené používateľovi
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
        # V receive_can_packet funkcii, v časti clear_status:
        if clear_status is not None:
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated for clear"}), 400
        
        if clear_status == "ok":
            DTCCodeActive.query.filter_by(vin_id=state.last_vin_id).delete()
            db.session.commit()
            
            # ✅ POSIELAME WEBSOCKET EVENT
            socketio.emit("clear_confirmation", {
                "device_id": device_id,
                "status": "success",
                "vin_id": state.last_vin_id,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            return jsonify({"status": "DTC cleared", "vin_id": state.last_vin_id}), 200

        # 2) VIN
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
        
            # 🔥 TVORBA DeviceVehicle (už existuje)
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state:
                state = DeviceVehicle(device_id=device_id, last_vin_id=vehicle.id)
                db.session.add(state)
            else:
                state.last_vin_id = vehicle.id
        
            # 🔥 PRIDÁME TVORBU UserVehicle (na rovnakom mieste)
            if device.user_id:
                # Skontroluj či už existuje vzťah user-vehicle
                user_vehicle = UserVehicle.query.filter_by(
                    user_id=device.user_id,
                    vehicle_id=vehicle.id
                ).first()
                
                if not user_vehicle:
                    # Vytvor nový permanentný vzťah
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
        # 3) DTC
        # 3) DTC
        # 3) DTC
        if dtc_code:
            dtc_code = dtc_code.strip().upper()
        
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated with this device"}), 400
        
            vehicle = Vehicle.query.get(state.last_vin_id)
            if not vehicle:
                return jsonify({"error": "Vehicle not found"}), 404
                # špeciálny packet = ECU nevrátila žiadne DTC
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
        
            db.session.add(DTCCodeHistory(vin_id=vehicle.id, dtc_code=dtc_code, severity=severity))
        
            DTCCodeActive.query.filter_by(vin_id=vehicle.id, dtc_code=dtc_code).delete()
            db.session.add(DTCCodeActive(vin_id=vehicle.id, dtc_code=dtc_code, severity=severity))
        
            db.session.commit()
            
            # ✅ PRIDAJ TOTO - WebSocket pre read DTC
            socketio.emit("dtc_update", {
                "device_id": device_id,
                "dtc_code": dtc_code,
                "severity": severity,
                "description": description,
                "timestamp": datetime.utcnow().isoformat()
            })
        
            return jsonify({"status": "DTC stored", "vin": vehicle.vin, "dtc": dtc_code, "severity": severity}), 201
        return jsonify({"status": "ignored", "message": "No recognized payload fields"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/location", methods=["POST"])
def receive_location():
    """
    Príjem GPS polohy z RPi
    ---
    tags:
      - Device Communication
    description: |
      Samostatný endpoint pre príjem GPS polohy z RPi.

      **Príklad requestu:**
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
        description: Poloha uložená
      400:
        description: Chybné dáta
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

        device.status = True
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
# GET "last known telemetry" endpoints (ponechane)
# =========================
def _get_vehicle_id_from_device(device_id: int) -> int | None:
    """Pomocná funkcia na získanie vehicle_id z device_id"""
    device_vehicle = DeviceVehicle.query.filter_by(device_id=device_id).first()
    if device_vehicle and device_vehicle.last_vin_id:
        return device_vehicle.last_vin_id
    return None

def _get_latest_telemetry(device_id: int) -> VehicleTelemetryLive | None:  # 🔥 Zmena návratového typu
    """Získa najnovšiu live telemetriu pre zariadenie (cez vehicle_id)."""
    vehicle_id = _get_vehicle_id_from_device(device_id)
    if not vehicle_id:
        return None

    return VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle_id).first()

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


@app.route("/api/device/<int:device_id>/location", methods=["GET"])
@jwt_required()
def get_device_location(device_id):
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


# Nový endpoint pre live data
@app.route("/api/device/<int:device_id>/live", methods=["GET"])
@jwt_required()
def get_device_live(device_id):
    """Získa kompletné live data pre zariadenie (z LIVE tabuľky)"""
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
    Porovnanie telemetrie pre všetky vozidlá používateľa
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    description: |
        Vráti štatistické údaje (priemery) z historických telemetrických dát.
        Zobrazuje všetky vozidlá, ktoré boli kedy priradené k používateľovi.
        Admin vidí všetky vozidlá.
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        vehicles_data = []
        online_count = 0

        if user.role == "admin":
            vehicles = Vehicle.query.all()
        else:
            user_vehicles = UserVehicle.query.filter_by(user_id=user_id).all()
            vehicles = [uv.vehicle for uv in user_vehicles]

        for vehicle in vehicles:
            owner_links = UserVehicle.query.filter_by(vehicle_id=vehicle.id).all()
            owner_user_ids = [link.user_id for link in owner_links]
            primary_user_id = owner_user_ids[0] if owner_user_ids else None

            # aktuálne zariadenie priradené k vozidlu
            device_vehicle = DeviceVehicle.query.filter_by(last_vin_id=vehicle.id).first()
            device_status = False
            device_id = None

            if device_vehicle:
                device = Device.query.get(device_vehicle.device_id)
                if device:
                    device_status = device.status
                    device_id = device.id
                    if device_status:
                        online_count += 1

            # štatistiky z history tabuľky
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

            # live odometer
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
                    "avg_rpm": round(stats.avg_rpm) if stats and stats.avg_rpm else None,
                    "avg_speed": round(stats.avg_speed) if stats and stats.avg_speed else None,
                    "avg_consumption": round(stats.avg_consumption, 1) if stats and stats.avg_consumption else None,
                    "max_rpm": stats.max_rpm if stats and stats.max_rpm else None,
                    "min_rpm": stats.min_rpm if stats and stats.min_rpm else None,
                    "total_odometer": live.odometer if live else None,
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
