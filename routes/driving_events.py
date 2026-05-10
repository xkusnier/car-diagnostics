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

bp = Blueprint("driving_events", __name__)

# Endpoint prijima udalosti z jazdy priamo zo zariadenia a uklada ich k vozidlu.
def receive_driving_event():
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
        # Jazdna udalost sa posiela ako samostatny JSON, oddelene od beznej telemetrie.
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
            # Odpoved obsahuje aj pocet, aby frontend nemusel pocitat dlzku zoznamu sam.
            return jsonify({"error": "device_id must be an integer"}), 400
        if not event_type:
            return jsonify({"error": "Missing event_type"}), 400
        # Udalosti mimo povoleneho zoznamu neukladam, aby v databaze nevznikali nahodne typy.
        # Backend odmietne nezname typy, aby sa do DB nedostali nahodne texty.
        if event_type not in ALLOWED_DRIVING_EVENT_TYPES:
            return jsonify({
                "error": "Invalid event_type",
                "allowed": sorted(list(ALLOWED_DRIVING_EVENT_TYPES))
            }), 400
        # Udalost musi byt naviazana na existujuce diagnosticke zariadenie.
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
            # Cas udalosti moze poslat zariadenie, inak sa pouzije cas prijatia na serveri.
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
        # Do DB sa uklada aj senzorovy kontext, aby sa udalost dala neskor vyhodnotit.
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
        # Frontend dostane novy incident v realnom case cez websocket room zariadenia.
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

# Vypis udalosti podla zariadenia, vhodny pre detail konkretneho diagnostickeho modulu.
def get_device_events(device_id):
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
        # Zakladny query sa potom podla parametrov doplni o limit a zoradenie.
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

# Vypis udalosti podla VIN pouziva overenie, ci ma pouzivatel k vozidlu pristup.
def get_vehicle_events(vin):
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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

# URL rules
bp.add_url_rule('/api/driving-event', endpoint='receive_driving_event', view_func=receive_driving_event, methods=['POST'])
bp.add_url_rule('/api/device/<int:device_id>/events', endpoint='get_device_events', view_func=jwt_required()(get_device_events), methods=['GET'])
bp.add_url_rule('/api/vehicle/<vin>/events', endpoint='get_vehicle_events', view_func=jwt_required()(get_vehicle_events), methods=['GET'])

# --- Suhrn vyuzitia AI ---
# AI: V tomto subore bola pomocou ChatGPT vygenerovana a nasledne autorom upravena Swagger dokumentacia oznacenych endpointov.
# AI: Oznacene endpointy boli ciastocne generovane pomocou ChatGPT a nasledne upravene autorom.
