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

bp = Blueprint("dtc", __name__)

# Kontrola vzorov hlada kombinacie aktivnych DTC, ktore mozu znamenat spolocny problem.
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
    # VIN z URL sa pouzije na najdenie konkretneho vozidla v databaze.
    vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
    if not vehicle:
        return jsonify({"error": "Vehicle not found"}), 404
    # Set sa pouziva preto, aby sa pri porovnavani patternov neriesili duplicity kodov.
    active_dtcs = set(d.dtc_code.upper() for d in DTCCodeActive.query.filter_by(vin_id=vehicle.id))
    # Ak vozidlo nema aktivne kody, patterny nema zmysel dalej vyhodnocovat.
    if not active_dtcs:
        return jsonify({
            "vin": vin,
            "active_dtcs": [],
            "matched_patterns": [],
            "message": "Vehicle has no active DTC codes"
        }), 200
    matched_patterns = []
    # Patterny predstavuju zname kombinacie kodov a ich pravdepodobnu pricinu.
    patterns = DtcPattern.query.all()
    for pattern in patterns:
        # Pre kazdy pattern sa nacita mnozina kodov, ktore musia byt aktivne naraz.
        pattern_codes = set(
            l.dtc_code.upper()
            for l in DtcPatternLink.query.filter_by(pattern_id=pattern.id)
        )
        # Pattern sa berie ako najdeny iba vtedy, ked su pritomne vsetky jeho povinne kody.
        if pattern_codes.issubset(active_dtcs):
            matched_patterns.append({
                "pattern_id": pattern.id,
                "pattern_name": pattern.name,
                "primary_cause": pattern.primary_cause,
                "required_codes": list(pattern_codes),
                "vehicle_codes": list(active_dtcs)
            })
    return jsonify({
        "vin": vin,
        "active_dtc_codes": list(active_dtcs),
        "matched_patterns": matched_patterns
    }), 200

# Vymazanie DTC sa uklada ako prikaz pre zariadenie, nie ako okamzita zmena v aute.
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
        # Prikaz clear/read moze zadat iba prihlaseny pouzivatel.
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        # Admin moze pracovat s hociktorym zariadenim, bezny pouzivatel iba so svojim.
        if user.role == "admin":
            # Mazanie DTC sa spusta pre konkretne zariadenie, nie priamo pre vozidlo.
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()
        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404
        # CLEAR_DTCS sa iba zaradi do fronty, skutocne vymazanie potvrdi az RPi.
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

# Nacitanie DTC vytvori prikaz, ktory si RPi vyzdvihne cez heartbeat.
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
        # GET_DTCS_PERM poziada zariadenie o aktualne permanentne diagnosticke kody.
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

# Plna historia DTC je pouzita pre admin/prehladove obrazovky a podporuje aj filtre.
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
        # silent=True zabrani chybe pri prazdnom alebo nespravnom JSON tele.
        data = request.get_json(silent=True) or {}
        vin = data.get("vin")
        if not vin:
            return jsonify({"error": "Missing 'vin' parameter"}), 400
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
        # Historia sa sklada cez outer join, aby sa vratili aj kody bez popisu.
        history = (
            db.session.query(
                DTCCodeHistory.dtc_code,
                DTCCodeHistory.created_at,
                DtcCodeMeaning.dtc_description
            )
            # Popis DTC je volitelny, preto sa pouziva outer join namiesto klasickeho joinu.
            .outerjoin(DtcCodeMeaning, DTCCodeHistory.dtc_code == DtcCodeMeaning.dtc_code)
            .filter(DTCCodeHistory.vin_id == vehicle.id)
            .order_by(DTCCodeHistory.created_at.desc())
            .all()
        )
        # ORM vysledky sa prepisuju na jednoduchy JSON zoznam.
        results = [{
            "dtc_code": h.dtc_code,
            "description": h.dtc_description or "No description available",
            "created_at": h.created_at.isoformat()
        } for h in history]
        return jsonify({"status": "success", "vin": vin.upper(), "history": results}), 200
    except Exception as e:
        print("❌ DTC HISTORY FULL ERROR:", e)
        return jsonify({"error": str(e)}), 500

# Import CSV doplna lokalny katalog popisov DTC kodov.
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
        # Import CSV ocakava URL suboru v JSON tele requestu.
        payload = request.get_json()
        csv_url = payload.get("csv_url")
        if not csv_url:
            return jsonify({"error": "Missing 'csv_url' parameter"}), 400
        # CSV sa stahuje dynamicky z dodanej adresy.
        response = requests.get(csv_url)
        if response.status_code != 200:
            return jsonify({"error": f"Failed to fetch CSV: {response.status_code}"}), 400
        csv_text = response.text
        # StringIO dovoli citat stiahnuty text rovnako ako subor.
        csv_reader = csv.reader(StringIO(csv_text))
        # Pocitadla sa vracaju vo vysledku, aby bolo jasne kolko kodov pribudlo.
        inserted, skipped = 0, 0
        for row in csv_reader:
            if len(row) < 2:
                continue
            dtc_code = row[0].strip()
            dtc_description = row[1].strip()
            # Existujuce kody sa preskocia, aby sa v katalogu nevytvarali duplicity.
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

# Popis DTC sa hlada v lokalnej tabulke a pri potrebe sa doplni odhad zavaznosti.
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
        payload = request.get_json(silent=True) or {}
        dtc_code = payload.get("dtc_code") or request.args.get("dtc_code")
        if not dtc_code:
            return jsonify({"error": "Missing 'dtc_code' parameter"}), 400
        # Popis kodu sa hlada case-insensitive, kedze vstup moze prist malymi pismenami.
        record = DtcCodeMeaning.query.filter(
            db.func.lower(DtcCodeMeaning.dtc_code) == dtc_code.lower()
        ).first()
        # Nenajdeny kod sa vracia ako samostatny stav, nie ako serverova chyba.
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

# Historia pre jedno vozidlo je chranena kontrolou vlastnictva alebo admin roly.
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
    # Jednoducha historia pouziva iba tabulku historie bez doplnkovych popisov.
    history = DTCCodeHistory.query.filter_by(vin_id=vehicle.id).order_by(DTCCodeHistory.created_at.desc()).all()
    return jsonify({
        "vin": vin,
        "dtc_history": [{"dtc_code": d.dtc_code, "created_at": d.created_at.isoformat()} for d in history]
    }), 200

# URL rules
bp.add_url_rule('/api/vehicle/<vin>/dtc-patterns', endpoint='check_dtc_patterns', view_func=jwt_required(optional=True)(check_dtc_patterns), methods=['GET'])
bp.add_url_rule('/api/dtc/pattern-check/<vin>', endpoint='check_dtc_patterns_alt', view_func=jwt_required(optional=True)(check_dtc_patterns), methods=['GET'])
bp.add_url_rule('/api/device/<int:device_id>/clear-dtcs', endpoint='clear_device_dtcs', view_func=jwt_required()(clear_device_dtcs), methods=['POST'])
bp.add_url_rule('/api/device/<int:device_id>/read-dtcs', endpoint='read_device_dtcs', view_func=jwt_required()(read_device_dtcs), methods=['POST'])
bp.add_url_rule('/api/dtc-history-full', endpoint='dtc_history_full', view_func=jwt_required(optional=True)(dtc_history_full), methods=['GET', 'POST'])
bp.add_url_rule('/api/load-dtc-codes', endpoint='load_dtc_codes_from_csv', view_func=load_dtc_codes_from_csv, methods=['POST'])
bp.add_url_rule('/api/dtc-description', endpoint='get_dtc_description', view_func=get_dtc_description, methods=['GET', 'POST'])
bp.add_url_rule('/api/vehicle/<vin>/dtc-history', endpoint='get_dtc_history', view_func=jwt_required()(get_dtc_history), methods=['GET'])
bp.add_url_rule('/api/dtc-history/<vin>', endpoint='get_dtc_history_alt', view_func=jwt_required()(get_dtc_history), methods=['GET'])
