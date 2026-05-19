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

bp = Blueprint("communication", __name__)

# Prvy krok spojenia zo zariadenia - backend vytvori alebo obnovi device zaznam.
def device_connect_syn():
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
        # Telo requestu sa cita ako JSON, lebo zariadenie posiela jednoduche datove spravy.
        data = request.get_json()
        # device_id je hlavny identifikator RPi zariadenia pri handshaku.
        device_id = data.get("device_id")
        if not device_id:
            return jsonify({"error": "missing device_id"}), 400
        # Ak device_id uz existuje, iba sa obnovi stav; inak sa vytvori novy device zaznam.
        # Najprv sa hlada existujuce zariadenie, aby sa nevytvarali duplicity.
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

# Druhy krok spojenia potvrdi zariadenie a podla VIN ho priradi k vozidlu.
def device_connect_ack():
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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

# Heartbeat iba udrzi zariadenie online a vrati mu cakajuce prikazy.
def heartbeat():
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
        # Heartbeat zaroven sluzi ako polling na prikazy cakajuce pre zariadenie.
        cmd = PendingCommand.query.filter_by(device_id=device_id, executed=False).first()
        if cmd:
            # Prikaz sa oznaci ako prevzaty hned pri odoslani zariadeniu.
            cmd.executed = True
            db.session.commit()
            return jsonify({"command": cmd.command}), 200
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Frontend cez tento endpoint vlozi prikaz, ktory si neskor precita zariadenie.
def trigger_command():
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
        # Povoluju sa iba prikazy, ktore zariadenie realne pozna.
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
        # Prikaz sa neodosiela priamo, ale ulozi sa a zariadenie si ho vyzdvihne cez heartbeat.
        cmd = PendingCommand(device_id=device_id, command=command)
        db.session.add(cmd)
        db.session.commit()
        return jsonify({"status": "queued", "command": command}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Hlavny vstup pre CAN/OBD data zo zariadenia, vratane DTC a telemetrie.
def receive_can_packet():
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
        # CAN packet moze obsahovat viac typov udajov, preto sa dalej vetvi podla dostupnych klucov.
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
        # Telemetria bez VIN sa smie ulozit iba vtedy, ked uz zariadenie ma priradene posledne vozidlo.
        if any(k in payload for k in ["odometer", "battery", "engine", "fuel", "speed"]) and not payload.get("vin"):
            # Stav zariadenie-vozidlo uklada posledny VIN pre dalsie spravy bez VIN.
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
        # Stav mazania DTC posiela zariadenie ako odpoved na predosly prikaz clear_dtc.
        if clear_status is not None:
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated for clear"}), 400
        if clear_status == "ok":
            # Pri uspesnom vymazani sa odstrania iba aktivne chyby, historia ostava zachovana.
            DTCCodeActive.query.filter_by(vin_id=state.last_vin_id).delete()
            db.session.commit()
            socketio.emit("clear_confirmation", {
                "device_id": device_id,
                "status": "success",
                "vin_id": state.last_vin_id,
                "timestamp": datetime.utcnow().isoformat()
            })
            return jsonify({"status": "DTC cleared", "vin_id": state.last_vin_id}), 200
        # VIN cast spravy vytvori alebo aktualizuje vozidlo a prepoji ho so zariadenim.
        if vin:
            # VIN sa normalizuje na velke pismena, aby sa rovnake auto neulozilo viackrat.
            vin = vin.strip().upper()
            # Zakladna kontrola dlzky odfiltruje neplatne VIN este pred zapisom do DB.
            if len(vin) != 17:
                return jsonify({"error": "VIN must be 17 characters"}), 400
            # VIN je hlavny identifikator auta, preto sa najprv hlada existujuce vozidlo.
            # Vozidlo sa hlada podla VIN, ktory je v modeli unikatny.
            vehicle = Vehicle.query.filter_by(vin=vin).first()
            # Ak auto este neexistuje, zalozi sa minimalny zaznam a doplnia sa dostupne metadata.
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
                # Pri existujucom vozidle sa menia iba metadata, ktore prisli v aktualnej sprave.
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
            # Ak je zariadenie priradene pouzivatelovi, vozidlo sa automaticky prida do jeho zoznamu.
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
        # DTC cast spravy riesi nove chyby alebo informaciu, ze ziadne chyby nie su aktivne.
        if dtc_code:
            dtc_code = dtc_code.strip().upper()
            state = DeviceVehicle.query.filter_by(device_id=device_id).first()
            if not state or not state.last_vin_id:
                return jsonify({"error": "No VIN associated with this device"}), 400
            vehicle = Vehicle.query.get(state.last_vin_id)
            if not vehicle:
                return jsonify({"error": "Vehicle not found"}), 404
            # Specialne hodnoty znamenaju, ze zariadenie nenaslo aktivne diagnosticke kody.
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
            # Popis a zavaznost sa beru z lokalnej databazy DTC kodov, ak je dostupna.
            meaning = DtcCodeMeaning.query.filter(
                db.func.lower(DtcCodeMeaning.dtc_code) == dtc_code.lower()
            ).first()
            description = meaning.dtc_description if meaning else ""
            severity = detect_severity_from_description(description)
            recommended_action = get_recommended_action(severity)
            db.session.add(DTCCodeHistory(vin_id=vehicle.id, dtc_code=dtc_code, severity=severity))
            # Pred vlozenim aktivnej chyby sa vymaze stary zaznam rovnakeho kodu, aby nebol duplicitny.
            DTCCodeActive.query.filter_by(vin_id=vehicle.id, dtc_code=dtc_code).delete()
            db.session.add(DTCCodeActive(vin_id=vehicle.id, dtc_code=dtc_code, severity=severity))
            db.session.commit()
            # Notifikacie sa posielaju vsetkym pouzivatelom, ktori maju vozidlo priradene.
            owner_links = UserVehicle.query.filter_by(vehicle_id=vehicle.id).all()
            vehicle_info = {
                "vin": vehicle.vin,
                "brand": vehicle.brand,
                "model": vehicle.model,
                "year": vehicle.year,
            }
            # Kazdy vlastnik vozidla dostane emailovu notifikaciu podla ulozenej adresy.
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

# GPS poloha sa prijima oddelene od CAN dat, aby fungovala aj pri inom tempe odosielania.
def receive_location():
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
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
        # Location endpoint prijima samostatne GPS data, aby sa poloha dala posielat aj mimo CAN packetu.
        payload = request.get_json()
        device_id_raw = payload.get("device_id")
        latitude_raw = payload.get("latitude")
        longitude_raw = payload.get("longitude")
        timestamp_raw = payload.get("timestamp")
        try:
            # device_id sa konvertuje na int kvoli konzistentnemu hladaniu v databaze.
            device_id = int(device_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "device_id must be an integer"}), 400
        try:
            # Suradnice sa validuju ako cisla, nie iba ako pritomne textove hodnoty.
            latitude = float(latitude_raw)
            longitude = float(longitude_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "latitude and longitude must be numbers"}), 400
        # Latitude ma fyzicky platny rozsah od -90 do 90 stupnov.
        if latitude < -90 or latitude > 90:
            return jsonify({"error": "latitude must be between -90 and 90"}), 400
        # Longitude ma fyzicky platny rozsah od -180 do 180 stupnov.
        if longitude < -180 or longitude > 180:
            return jsonify({"error": "longitude must be between -180 and 180"}), 400
        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": f"Device {device_id} not found"}), 404
        # Ak zariadenie neposle cas, backend pouzije aktualny cas prijatia spravy.
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

# URL rules
bp.add_url_rule('/api/connect', endpoint='device_connect_syn', view_func=device_connect_syn, methods=['POST'])
bp.add_url_rule('/api/connect/ack', endpoint='device_connect_ack', view_func=device_connect_ack, methods=['POST'])
bp.add_url_rule('/api/heartbeat', endpoint='heartbeat', view_func=heartbeat, methods=['POST'])
bp.add_url_rule('/api/trigger', endpoint='trigger_command', view_func=trigger_command, methods=['POST'])
bp.add_url_rule('/api/can', endpoint='receive_can_packet', view_func=receive_can_packet, methods=['POST'])
bp.add_url_rule('/api/location', endpoint='receive_location', view_func=receive_location, methods=['POST'])

# --- Suhrn vyuzitia AI ---
# AI: V tomto subore bola pomocou ChatGPT vygenerovana a nasledne autorom upravena Swagger dokumentacia oznacenych endpointov.
# AI: Oznacene endpointy boli ciastocne generovane pomocou ChatGPT a nasledne upravene autorom.
