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

# Mazanie zariadenia je obmedzene na vlastnika alebo administratora.
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
        # Mazanie zariadenia je viazane na prihlaseneho pouzivatela alebo admina.
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        if user.role == "admin":
            # Najprv sa overi existencia zariadenia, az potom prava pouzivatela.
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()
        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404
        try:
            # Najprv sa rucne odstrani vazba na vozidlo, aby po zariadeni neostal stary link.
            DeviceVehicle.query.filter_by(device_id=device_id).delete()
            # Cakajuce prikazy sa pri zmazani zariadenia uz nesmu dalej vykonat.
            PendingCommand.query.filter_by(device_id=device_id).delete()
            # Vdaka cascade vztahom sa pri zmazani odstrania aj naviazane pomocne zaznamy.
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

# Manualne pridanie zariadenia z frontendu, ked este nebolo vytvorene cez RPi komunikaciu.
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
        # Telo requestu obsahuje device_id a pri adminovi volitelne aj cieloveho pouzivatela.
        payload = request.get_json()
        device_id_raw = payload.get("device_id")
        target_user_id = payload.get("user_id")
        current_user_id = int(get_jwt_identity())
        current_user = User.query.get(current_user_id)
        if not current_user:
            return jsonify({"error": "User not found"}), 404
        # Bezny pouzivatel nemoze priradovat zariadenie inemu uctu.
        if current_user.role != "admin":
            target_user_id = current_user.id
        if not target_user_id:
            return jsonify({"error": "Missing user_id (admin only)"}), 400
        try:
            # Device ID sa uklada ako cislo, preto sa validuje este pred zapisom.
            device_id = int(device_id_raw)
        except (ValueError, TypeError):
            return jsonify({"error": "Device ID must be an integer"}), 400
        # Existujuci device sa iba priradi k uctu, nevytvara sa duplicita.
        existing = Device.query.get(device_id)
        if existing:
            return jsonify({"error": f"Device ID {device_id} already exists"}), 409
        # Novo pridane zariadenie zacina ako offline, kym neposle komunikaciu.
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

# Detail diagnostiky zariadenia spaja online stav, vozidlo, aktivne DTC a telemetriu.
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
        # Pred vypisom zariadeni sa najprv opravia stare online stavy.
        # Pred diagnostikou sa aktualizuje online/offline stav zariadenia.
        refresh_stale_device_statuses()
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        if user.role == "admin":
            # Diagnostika sa sklada zo stavu zariadenia, posledneho VIN a DTC kodov.
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()
        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404
        # Predvolene hodnoty ostanu None, ak zariadenie este nema priradene vozidlo.
        vin = year = brand = model = engine = None
        dtcs = []
        if device.link and len(device.link) > 0 and device.link[0].last_vin_id:
            # VIN sa ziska cez posledne priradene vozidlo zariadenia.
            vin_obj = Vehicle.query.get(device.link[0].last_vin_id)
            if vin_obj:
                vin = vin_obj.vin
                brand = vin_obj.brand
                year = vin_obj.year
                model = vin_obj.model
                engine = vin_obj.engine
                # Aktivne DTC sa nacitaju spolu s popisom cez outer join.
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
                # Vysledne DTC sa mapuju na format, ktory priamo pouziva obrazovka diagnostiky.
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

# Zoznam zariadeni sa filtruje podla roly, admin vidi viac ako bezny pouzivatel.
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
            # Beznemu pouzivatelovi sa vracaju iba jeho vlastne zariadenia.
            devices = Device.query.filter_by(user_id=user_id).all()
        # Zoznam zariadeni sa sklada rucne, lebo obsahuje aj vypocitane polia.
        result = []
        # Kazde zariadenie sa doplni o posledne vozidlo a pocet aktivnych chyb.
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

# Zariadenie moze explicitne oznamit odpojenie, aby sa stav nemusel menit az po timeout-e.
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
        # Offline endpoint nastavuje iba stav, ostatne data zariadenia ostavaju zachovane.
        device.status = False
        db.session.commit()
        return jsonify({"status": "success", "device_id": device_id, "message": "Device set to offline"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# URL rules
bp.add_url_rule('/api/device/<int:device_id>', endpoint='delete_device', view_func=jwt_required()(delete_device), methods=['DELETE'])
bp.add_url_rule('/api/add-device', endpoint='add_device', view_func=jwt_required()(add_device), methods=['POST'])
bp.add_url_rule('/api/device/<int:device_id>/diagnostics', endpoint='device_diagnostics', view_func=jwt_required()(device_diagnostics), methods=['GET'])
bp.add_url_rule('/api/my-devices', endpoint='my_devices', view_func=jwt_required()(my_devices), methods=['GET'])
bp.add_url_rule('/api/device/<int:device_id>/offline', endpoint='device_offline', view_func=device_offline, methods=['POST'])
bp.add_url_rule('/api/device_offline/<int:device_id>', endpoint='device_offline_alt', view_func=device_offline, methods=['POST'])
