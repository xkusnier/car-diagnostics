from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import Device, DeviceVehicle, Vehicle, PendingCommand, User, db

device_bp = Blueprint("device", __name__, url_prefix="/api")

# ---------------------------------------------------------------------
# ✅ Pridanie zariadenia
# ---------------------------------------------------------------------
@device_bp.route("/add-device", methods=["POST"])
@jwt_required()
def add_device():
    """
    Pridá nové zariadenie (device) do databázy.
    """
    try:
        payload = request.get_json()
        device_id_raw = payload.get("device_id")
        target_user_id = payload.get("user_id")
        current_user_id = int(get_jwt_identity())
        current_user = User.query.get(current_user_id)

        if not current_user:
            return jsonify({"error": "User not found"}), 404

        # Ak nie je admin, môže pridať len sebe
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
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------
# ✅ Získanie zoznamu zariadení
# ---------------------------------------------------------------------
@device_bp.route("/my-devices", methods=["GET"])
@jwt_required()
def my_devices():
    """
    Vráti všetky zariadenia prihláseného používateľa (alebo všetky pre admina).
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
                "user_id": d.user_id if user.role == "admin" else None
            })

        return jsonify({"status": "success", "devices": result}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------
# ✅ Diagnostika konkrétneho zariadenia
# ---------------------------------------------------------------------
@device_bp.route("/device/<int:device_id>/diagnostics", methods=["GET"])
@jwt_required()
def device_diagnostics(device_id):
    """
    Vráti DTC kódy a VIN pre konkrétne zariadenie.
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)

        if user.role == "admin":
            device = Device.query.get(device_id)
        else:
            device = Device.query.filter_by(id=device_id, user_id=user_id).first()

        if not device:
            return jsonify({"error": "Device not found or not owned by user"}), 404

        vin = None
        dtcs = []
        if device.link and len(device.link) > 0 and device.link[0].last_vin_id:
            vin_obj = Vehicle.query.get(device.link[0].last_vin_id)
            if vin_obj:
                vin = vin_obj.vin
                dtcs = [d.dtc_code for d in vin_obj.dtcs]

        return jsonify({
            "status": "success",
            "device_id": device.id,
            "vin": vin,
            "dtc_codes": dtcs,
            "online": device.status
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------
# ✅ Heartbeat – zariadenie sa hlási
# ---------------------------------------------------------------------
@device_bp.route("/heartbeat", methods=["POST"])
def heartbeat():
    """
    RPi sa hlási každých 30 sekúnd. Server vráti príkaz (ak existuje).
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

        # skontroluj čakajúci príkaz
        cmd = PendingCommand.query.filter_by(device_id=device_id, executed=False).first()
        if cmd:
            cmd.executed = True
            db.session.commit()
            return jsonify({"command": cmd.command}), 200

        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------
# ✅ Trigger command – admin pošle príkaz RPi
# ---------------------------------------------------------------------
@device_bp.route("/trigger", methods=["POST"])
def trigger_command():
    """
    Admin pošle príkaz na zariadenie.
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")
        command = data.get("command")

        if command not in ["GET_VIN", "GET_DTCS_PERM", "GET_DTCS_PEND", "GET_RPM", "GET_TEMP"]:
            return jsonify({"error": "invalid command"}), 400

        cmd = PendingCommand(device_id=device_id, command=command)
        db.session.add(cmd)
        db.session.commit()

        return jsonify({"status": "queued", "command": command}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------
# ✅ Device offline – manuálne nastavenie
# ---------------------------------------------------------------------
@device_bp.route("/device_offline/<int:device_id>", methods=["POST"])
def device_offline(device_id):
    """
    Nastaví zariadenie ako offline.
    """
    try:
        device = Device.query.get(device_id)
        if not device:
            return jsonify({"error": "Device not found"}), 404

        device.status = False
        db.session.commit()

        return jsonify({
            "status": "success",
            "device_id": device_id,
            "message": "Device set to offline"
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
