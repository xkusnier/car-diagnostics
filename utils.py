import os
import re
import requests
from datetime import datetime, timedelta
from extensions import db
from models import *

ALLOWED_DRIVING_EVENT_TYPES = {
    "HARD_BRAKE",
    "SHARP_ACCELERATION",
    "HARD_TURN",
    "CRASH",
}
DEVICE_ONLINE_TIMEOUT_SECONDS = 120
VIN_TRANSLITERATION = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
}
VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
VIN_ALLOWED_REGEX = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
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
