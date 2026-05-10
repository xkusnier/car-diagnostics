import os
import re
import requests
from datetime import datetime, timedelta
from extensions import db
from models import *

# Povolene typy udalosti su drzane na jednom mieste, aby sa validacia nerozchadzala medzi endpointmi.
ALLOWED_DRIVING_EVENT_TYPES = {
    "HARD_BRAKE",
    "SHARP_ACCELERATION",
    "HARD_TURN",
    "CRASH",
}
# Po tomto case bez heartbeat spravy sa zariadenie povazuje za offline.
DEVICE_ONLINE_TIMEOUT_SECONDS = 120
# Tabulka pre preklad znakov VIN na cisla pri vypocte kontrolnej cislice.
VIN_TRANSLITERATION = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
}
VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
VIN_ALLOWED_REGEX = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
# Jednoduche klucove slova pre odhad zavaznosti DTC, ked nie je dostupna explicitna uroven.
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
# Pri kazdej platnej komunikacii zariadenia sa obnovi online stav aj cas posledneho kontaktu.
def mark_device_online(device):
    device.status = True
    device.last_seen = datetime.utcnow()

# Pravidelna kontrola zariadeni, ktore uz dlhsie neposlali heartbeat.
def refresh_stale_device_statuses():
    cutoff = datetime.utcnow() - timedelta(seconds=DEVICE_ONLINE_TIMEOUT_SECONDS)
    # Stale zariadenia su tie, ktore su oznacene online, ale posledny kontakt je prilis stary.
    stale_devices = Device.query.filter(
        Device.status == True,
        Device.last_seen.isnot(None),
        Device.last_seen < cutoff
    ).all()
    # Commit sa robi iba vtedy, ked sa realne zmenil aspon jeden stav.
    changed = False
    for device in stale_devices:
        device.status = False
        changed = True
    if changed:
        db.session.commit()

# Prevod ORM objektu na JSON tvar pouzivany vo frontend odpovediach.
def serialize_driving_event(event: DrivingEvent) -> dict:
    # Serializer vracia vnoreny JSON s akcelerometrom a gyroskopom pre jednoduchsi frontend.
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

# Vypocet kontrolnej cislice VIN podla standardnych vah a modulo 11.
# AI: Tato funkcia bola ciastocne generovana pomocou ChatGPT a nasledne upravena autorom.
def compute_vin_check_digit(vin: str) -> str | None:
    vin = (vin or "").strip().upper()
    # Regex odfiltruje zakazane znaky VIN ako I, O alebo Q.
    if not VIN_ALLOWED_REGEX.match(vin):
        return None
    total = 0
    # Deviata pozicia VIN ma vahu 0, lebo tam sa nachadza samotna kontrolna cislica.
    # Kontrolna cislica VIN sa pocita ako vazeny sucet transliterovanych znakov.
    for i, ch in enumerate(vin):
        value = VIN_TRANSLITERATION.get(ch)
        if value is None:
            return None
        total += value * VIN_WEIGHTS[i]
    # Zvysok 10 sa pri VIN zapisuje ako znak X.
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)

# Validacia vracia jednotny slovnik, aby endpoint vedel rozlisit format, checksum a platny stav.
# AI: Tato funkcia bola ciastocne generovana pomocou ChatGPT a nasledne upravena autorom.
def validate_vin_value(vin: str) -> dict:
    vin = (vin or "").strip().upper()
    if len(vin) != 17:
        # Payload zjednocuje nazvy z CAN spravy do jedneho interneho formatu.
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
    # Deviaty znak VIN je kontrolna cislica podla standardneho vypoctu.
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

# Ked CSV nema priamu zavaznost, skusim ju odhadnut podla textu popisu chyby.
# AI: Tato funkcia bola ciastocne generovana pomocou ChatGPT a nasledne upravena autorom.
def detect_severity_from_description(description: str) -> str:
    if not description:
        return "medium"
    # Popis chyby sa porovnava malymi pismenami, aby nezalezalo na zapise v CSV.
    text = description.lower()
    # Kriticke slova maju prednost pred nizkou zavaznostou.
    for word in CRITICAL_KEYWORDS:
        if word in text:
            # Pri najdeni kritickeho slova sa dalsie hladanie uz neriesi.
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

# Odoslanie notifikacie je izolovane, aby endpoint nemal priamo v sebe celu logiku emailu.
# AI: Tato funkcia bola ciastocne generovana pomocou ChatGPT a nasledne upravena autorom.
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
        # Predmet emailu obsahuje kod aj zavaznost, aby bol jasny uz v inboxe.
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
        # Brevo API ocakava iny payload ako SMTP, preto sa sprava sklada do ich JSON formatu.
        # Brevo API ocakava konkretne polia sender, to, subject a htmlContent.
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
        # Email sa posiela cez externu API sluzbu nastavenu cez environment premenne.
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

# Z raw payloadu zlozim jednotny tvar, s ktorym dalej pracuje WebSocket aj REST cast.
# AI: Tato funkcia bola ciastocne generovana pomocou ChatGPT a nasledne upravena autorom.
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

# Najdolezitejsia pomocna funkcia: uklada live data, historiu a zaroven pocita jazdu.
# AI: Tato funkcia bola ciastocne generovana pomocou ChatGPT a nasledne upravena autorom.
def _save_telemetry_to_db(device_id: int, t: dict) -> None:
    """Uloží telemetriu do DB - live (posledná) + history (všetky) + trip detection"""
    try:
        # Telemetriu neukladam priamo k zariadeniu, ale k vozidlu, ktore je k nemu aktualne priradene.
        # Bez posledneho VIN sa telemetria nema ku ktoremu vozidlu ulozit.
        device_vehicle = DeviceVehicle.query.filter_by(device_id=device_id).first()
        if not device_vehicle or not device_vehicle.last_vin_id:
            print(f"❌ No vehicle associated with device {device_id}")
            return
        # Dalej sa pracuje uz iba s internym vehicle_id, nie s textovym VIN.
        vehicle_id = device_vehicle.last_vin_id
        battery = t.get("battery") or {}
        engine = t.get("engine") or {}
        fuel = t.get("fuel") or {}
        # Live tabulka drzi iba posledny stav vozidla.
        live_row = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle_id).first()
        # Pri prvej vzorke sa zalozi live riadok a potom sa iba aktualizuje.
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
        # Otvorena jazda je taka, ktora este nema is_completed nastavene na True.
        active_trip = Trip.query.filter_by(
            vehicle_id=vehicle_id,
            is_completed=False
        ).first()
        current_time = datetime.utcnow()
        # Ak motor prave bezi a ziadna jazda nie je otvorena, zacina sa nova jazda.
        # Prva vzorka so zapnutym motorom otvori novu jazdu.
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
        # Kazda prijata vzorka ide do historie, aby bolo mozne neskor pocitat statistiky a grafy.
        # Kazda vzorka sa uklada do historie, aj ked live tabulka drzi iba poslednu.
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
        # Pri otvorenej jazde sa priebezne aktualizuju maxima, trvanie a vzdialenost.
        # Ak existuje aktivna jazda, aktualizuju sa jej priebezne statistiky.
        if active_trip:
            active_trip.samples_count += 1
            active_trip.end_time = current_time
            # Pri RPi zdroji sa vzdialenost pocas jazdy berie priamo z prichadzajuceho odometra.
            # Pri RPi odometri sa vzdialenost pocita priamo z rozdielu hodnot.
            if odometer_source == "rpi":
                current_odometer = t.get("odometer")
                if current_odometer is not None:
                    if active_trip.start_odometer is None:
                        active_trip.start_odometer = current_odometer
                    active_trip.end_odometer = current_odometer
                    if active_trip.start_odometer is not None and active_trip.end_odometer is not None:
                        active_trip.distance_km = (active_trip.end_odometer - active_trip.start_odometer)
            current_speed = t.get("speed")
            # Maximum rychlosti sa aktualizuje len ked prisla platna hodnota.
            if current_speed is not None:
                if active_trip.max_speed is None or current_speed > active_trip.max_speed:
                    active_trip.max_speed = current_speed
            current_rpm = engine.get("rpm")
            # RPM statistiky sa menia iba pri nenulovej hodnote motora.
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
            # Trvanie jazdy sa priebezne prepocitava od start_time po poslednu vzorku.
            if active_trip.start_time:
                delta = current_time - active_trip.start_time
                active_trip.duration_seconds = int(delta.total_seconds())
        # Vypnutie motora ukonci jazdu a dopoctu sa priemerne hodnoty z ulozenych vzoriek.
        # Vypnuty motor uzavrie aktivnu jazdu a dopocita suhrnne hodnoty.
        if not engine_running and active_trip:
            # Pri ukonceni jazdy sa priemery pocitaju z ulozenych historickych vzoriek.
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
            # Pri manualnom odometri sa vzdialenost odhaduje z priemernej rychlosti a casu.
            if odometer_source == "manual":
                if active_trip.avg_speed is not None and active_trip.duration_seconds is not None:
                    estimated_distance = active_trip.avg_speed * (active_trip.duration_seconds / 3600.0)
                    active_trip.distance_km = estimated_distance
                    start_odometer = active_trip.start_odometer if active_trip.start_odometer is not None else live_row.odometer
                    start_odometer = start_odometer or 0
                    active_trip.start_odometer = int(round(start_odometer))
                    active_trip.end_odometer = int(round(start_odometer + estimated_distance))
                    live_row.odometer = active_trip.end_odometer
            # Spotrebovane palivo sa dopocita az ked je znama vzdialenost aj priemerna spotreba.
            if active_trip.distance_km and active_trip.avg_consumption_l100km:
                active_trip.total_fuel_used_l = (active_trip.distance_km / 100) * active_trip.avg_consumption_l100km
            # Po uzavreti sa jazda uz nebude znovu vyberat ako aktivna.
            active_trip.is_completed = True
            print(f"✅ Trip completed for vehicle {vehicle_id}, duration: {active_trip.duration_seconds}s")
        # Pri RPi zdroji sa live odometer berie priamo z prichadzajucej telemetrie.
        # RPi odometer sa po ulozeni vzorky prenesie aj do live stavu vozidla.
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

# GPS poloha sa uklada samostatne, ale ak bezi jazda, priradi sa aj k nej.
# AI: Tato funkcia bola ciastocne generovana pomocou ChatGPT a nasledne upravena autorom.
def _save_location_to_db(device_id: int, latitude: float, longitude: float, timestamp: datetime | None = None) -> int | None:
    try:
        # Pri GPS polohe sa opat pouzije posledne vozidlo priradene k device_id.
        device_vehicle = DeviceVehicle.query.filter_by(device_id=device_id).first()
        if not device_vehicle or not device_vehicle.last_vin_id:
            print(f"❌ No vehicle associated with device {device_id}")
            return None
        vehicle_id = device_vehicle.last_vin_id
        current_time = timestamp or datetime.utcnow()
        # Ak prave bezi jazda, poloha sa priradi aj k nej.
        active_trip = Trip.query.filter_by(
            vehicle_id=vehicle_id,
            is_completed=False
        ).first()
        # GPS historia je oddelena od telemetrie, aby sa dala jednoducho citat pre mapu.
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
    # Pomocna funkcia vracia vehicle_id bez kopirovania rovnakeho query do endpointov.
    device_vehicle = DeviceVehicle.query.filter_by(device_id=device_id).first()
    if device_vehicle and device_vehicle.last_vin_id:
        return device_vehicle.last_vin_id
    return None

def _get_latest_telemetry(device_id: int) -> VehicleTelemetryLive | None:
    """Získa najnovšiu live telemetriu pre zariadenie (cez vehicle_id)."""
    vehicle_id = _get_vehicle_id_from_device(device_id)
    if not vehicle_id:
        return None
    # Vracia poslednu znamu telemetriu pre zariadenie, alebo None ak este neexistuje.
    return VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle_id).first()


# Explicit exports are needed because routes use `from utils import *`.
# Python normally does not import names that start with `_` via star imports.
# These helper functions are used by communication.py, telemetry.py and websocket_events.py.
__all__ = [
    "ALLOWED_DRIVING_EVENT_TYPES",
    "DEVICE_ONLINE_TIMEOUT_SECONDS",
    "VIN_TRANSLITERATION",
    "VIN_WEIGHTS",
    "VIN_ALLOWED_REGEX",
    "CRITICAL_KEYWORDS",
    "LOW_KEYWORDS",
    "mark_device_online",
    "refresh_stale_device_statuses",
    "serialize_driving_event",
    "compute_vin_check_digit",
    "validate_vin_value",
    "detect_severity_from_description",
    "get_recommended_action",
    "send_dtc_email_notification",
    "_iso",
    "_telemetry_payload",
    "_save_telemetry_to_db",
    "_save_location_to_db",
    "_get_vehicle_id_from_device",
    "_get_latest_telemetry",
]

# --- Suhrn vyuzitia AI ---
# AI: V tomto subore boli pomocou ChatGPT ciastocne generovane a nasledne autorom upravene oznacene pomocne funkcie pre validaciu VIN, odhad zavaznosti, emailove notifikacie, spracovanie telemetrie a ukladanie polohy.
