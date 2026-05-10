from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from sqlalchemy import func, or_
from datetime import datetime
import csv
import requests
from io import StringIO
from extensions import db, socketio
from models import *
from utils import *

bp = Blueprint("telemetry", __name__)

# Jednotlive endpointy telemetrie vracaju mensie casti live stavu pre frontend karty.
def get_device_odometer(device_id):
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
            # Kazdy maly endpoint vracia iba cast telemetrie potrebnu pre konkretny widget.
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

# Bateria sa cita z poslednej live telemetrie priradeneho vozidla.
def get_device_battery(device_id):
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
    # Live endpoint zjednocuje viac hodnot naraz pre hlavnu obrazovku detailu.
    return jsonify({
        "status": "success",
        "device_id": device_id,
        "vehicle_id": t.vehicle_id,
        "battery_voltage": float(t.battery_voltage),
        "health": t.battery_health or "unknown",
        "timestamp": _iso(t.created_at)
    }), 200

# Motorove udaje su oddelene, aby frontend nemusel nacitavat cely live payload.
def get_device_engine(device_id):
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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

# Spotreba a palivove udaje sa vracaju samostatne pre prehladne zobrazenie.
def get_device_fuel(device_id):
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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

# Rychlost sa cita z live riadku, teda z poslednej prijatej vzorky.
def get_device_speed(device_id):
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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

# Poloha berie posledny ulozeny GPS zaznam pre vozidlo priradene k zariadeniu.
def get_device_location(device_id):
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
        # Telemetria sa hlada cez posledne vozidlo priradene k zariadeniu.
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

# Kompletny live endpoint sklada jeden vacsi JSON pre detail zariadenia alebo dashboard.
def get_device_live(device_id):
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
        # Pri polohe sa tiez vychadza z aktualneho vozidla pre dane zariadenie.
        vehicle_id = _get_vehicle_id_from_device(device_id)
        if not vehicle_id:
            return jsonify({"error": "No VIN associated"}), 404
        live = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle_id).first()
        # Ak este neexistuje ziadna vzorka, frontend dostane 404 namiesto prazdnych hodnot.
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

# URL rules
bp.add_url_rule('/api/device/<int:device_id>/odometer', endpoint='get_device_odometer', view_func=jwt_required()(get_device_odometer), methods=['GET'])
bp.add_url_rule('/api/device/<int:device_id>/battery', endpoint='get_device_battery', view_func=jwt_required()(get_device_battery), methods=['GET'])
bp.add_url_rule('/api/device/<int:device_id>/engine', endpoint='get_device_engine', view_func=jwt_required()(get_device_engine), methods=['GET'])
bp.add_url_rule('/api/device/<int:device_id>/fuel', endpoint='get_device_fuel', view_func=jwt_required()(get_device_fuel), methods=['GET'])
bp.add_url_rule('/api/device/<int:device_id>/speed', endpoint='get_device_speed', view_func=jwt_required()(get_device_speed), methods=['GET'])
bp.add_url_rule('/api/device/<int:device_id>/location', endpoint='get_device_location', view_func=jwt_required()(get_device_location), methods=['GET'])
bp.add_url_rule('/api/device/<int:device_id>/live', endpoint='get_device_live', view_func=jwt_required()(get_device_live), methods=['GET'])

# --- Suhrn vyuzitia AI ---
# AI: V tomto subore bola pomocou ChatGPT vygenerovana a nasledne autorom upravena Swagger dokumentacia oznacenych endpointov.
# AI: Oznacene endpointy boli ciastocne generovane pomocou ChatGPT a nasledne upravene autorom.
