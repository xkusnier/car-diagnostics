from flask import Blueprint, request, jsonify
from app import db
from models import Device, Vehicle, DTCCode, DtcCodeMeaning, DeviceVehicle
import requests
import os
import csv
from io import StringIO

vin_bp = Blueprint("vin", __name__, url_prefix="/api")

# ---------------------------------------------------------------------
# ✅ VIN decoding cez NHTSA (bez API key)
# ---------------------------------------------------------------------
@vin_bp.route("/vin/nhtsa", methods=["POST"])
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
            "model": vehicle_info.get("Model"),
            "year": vehicle_info.get("ModelYear"),
            "engine": vehicle_info.get("EngineModel"),
            "bodyClass": vehicle_info.get("BodyClass"),
            "manufacturer": vehicle_info.get("ManufacturerName"),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------
# ✅ VIN decoding cez Apiverve API
# ---------------------------------------------------------------------
@vin_bp.route("/vindecode", methods=["POST"])
def decode_vin_apiverve():
    """
    Dekóduje VIN pomocou apiverve VIN Decoder API.
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


# ---------------------------------------------------------------------
# ✅ Prijímanie CAN packetov (VIN/DTC od RPi)
# ---------------------------------------------------------------------
@vin_bp.route("/can", methods=["POST"])
def receive_can_packet():
    """
    Raspberry odošle VIN alebo DTC dáta ako TEXT (už dekódované na RPi).
    """
    try:
        payload = request.get_json()
        device_id = payload.get("device_id")
        vin = payload.get("vin")
        dtc_code = payload.get("dtc_code")

        if device_id is None:
            return jsonify({"error": "Missing 'device_id'"}), 400

        if not vin and not dtc_code:
            return jsonify({"error": "Missing 'vin' or 'dtc_code'"}), 400
        if vin and dtc_code:
            return jsonify({"error": "Provide either 'vin' or 'dtc_code', not both"}), 400

        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": f"Device {device_id} not found"}), 404

        # --- Spracovanie VIN ---
        if vin:
            vin = vin.strip().upper()
            if len(vin) != 17:
                return jsonify({"error": "VIN must be 17 characters"}), 400

            vehicle = Vehicle.query.filter_by(vin=vin).first()
            if not vehicle:
                vehicle = Vehicle(vin=vin)
                db.session.add(vehicle)
                db.session.commit()

            # ✅ Označ zariadenie ako online
            device.status = True

            # Aktualizuj last_vin_id
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
                "device_status": "Online"
            }), 201

        # --- Spracovanie DTC ---
        if dtc_code:
            dtc_code = dtc_code.strip().upper()
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated with this device"}), 400

            vehicle = Vehicle.query.get(state.last_vin_id)
            if not vehicle:
                return jsonify({"error": "Vehicle not found"}), 404

            new_dtc = DTCCode(vin_id=vehicle.id, dtc_code=dtc_code)
            db.session.add(new_dtc)
            db.session.commit()

            return jsonify({
                "status": "DTC stored",
                "vin": vehicle.vin,
                "dtc": dtc_code
            }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------
# ✅ Načítanie DTC kódov z CSV
# ---------------------------------------------------------------------
@vin_bp.route("/load-dtc-codes", methods=["POST"])
def load_dtc_codes_from_csv():
    """
    Načíta DTC kódy z CSV súboru (napr. z GitHubu) a uloží ich do databázy.
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


# ---------------------------------------------------------------------
# ✅ Vyhľadanie popisu DTC kódu
# ---------------------------------------------------------------------
@vin_bp.route("/dtc-description", methods=["POST"])
def get_dtc_description():
    """
    Získa textový popis DTC kódu z databázy.
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


# ---------------------------------------------------------------------
# ✅ Zobrazenie všetkých VIN a DTC
# ---------------------------------------------------------------------
@vin_bp.route("/all", methods=["GET"])
def show_all():
    """
    Zobrazí všetky VIN a priradené DTC kódy.
    """
    vehicles = Vehicle.query.all()
    data = []
    for v in vehicles:
        data.append({
            "vin": v.vin,
            "dtc_codes": [d.dtc_code for d in v.dtcs]
        })
    return jsonify(data), 200
