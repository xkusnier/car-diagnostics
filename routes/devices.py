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

bp = Blueprint("devices", __name__)

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
