from datetime import datetime
from extensions import db

# Pouzivatel aplikacie, ku ktoremu sa viazu zariadenia a vozidla.
class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="user")
    email = db.Column(db.String(120), unique=True, nullable=False)
    # Pri zmazani pouzivatela sa odstrania aj jeho priradene zariadenia.
    devices = db.relationship("Device", backref="user", lazy=True, cascade="all, delete")

# Fyzicke diagnosticke zariadenie, ktore posiela data z auta na backend.
class Device(db.Model):
    __tablename__ = "device"
    id = db.Column(db.Integer, primary_key=True)
    # Zariadenie moze existovat aj bez pouzivatela, napriklad po prvom pripojeni z RPi.
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status = db.Column(db.Boolean, default=False)
    # last_seen sa pouziva na odhad, ci je zariadenie stale online.
    last_seen = db.Column(db.DateTime, nullable=True)
    # Link tabulka drzi posledne vozidlo, aby dalsie data nemuseli stale posielat VIN.
    link = db.relationship("DeviceVehicle", backref="device", lazy=True, cascade="all, delete")

# Vazba medzi zariadenim a poslednym vozidlom, z ktoreho zariadenie posielalo data.
class DeviceVehicle(db.Model):
    __tablename__ = "device_vehicle"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False)
    # Posledny VIN je ulozeny ako FK, aby sa telemetry a DTC vedeli naviazat na vozidlo.
    last_vin_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=True)
    # updated_at sa automaticky meni pri kazdej zmene prepojenia zariadenia a vozidla.
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Vozidlo identifikovane podla VIN, na ktore sa viazu DTC kody, jazdy a telemetria.
class Vehicle(db.Model):
    __tablename__ = "vehicles"
    id = db.Column(db.Integer, primary_key=True)
    # VIN je unikatny identifikator vozidla v celej databaze.
    vin = db.Column(db.String(50), unique=True, nullable=False)
    year = db.Column(db.String(10), nullable=True)
    brand = db.Column(db.String(10), nullable=True)
    model = db.Column(db.String(100), nullable=True)
    engine = db.Column(db.String(100), nullable=True)
    # Aktivne a historicke DTC su oddelene, lebo frontend zobrazuje oba typy inak.
    dtcs_active = db.relationship("DTCCodeActive", backref="vehicle", lazy=True, cascade="all, delete")
    dtcs_history = db.relationship("DTCCodeHistory", backref="vehicle", lazy=True, cascade="all, delete")

# Aktivne DTC kody reprezentuju aktualne chyby, ktore este neboli vymazane.
class DTCCodeActive(db.Model):
    __tablename__ = "dtc_codes_active"
    id = db.Column(db.Integer, primary_key=True)
    vin_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)
    # Ak sa zavaznost nepodari urcit presne, pouzije sa stredna hodnota.
    severity = db.Column(db.String(20), default="medium")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Jedna jazda vozidla; vznikne pri spusteni motora a uzavrie sa po jeho vypnuti.
class Trip(db.Model):
    __tablename__ = "trips"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("trips", lazy="dynamic"))
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True)
    # Trvanie jazdy sa dopocita postupne z prijatych vzoriek telemetrie.
    duration_seconds = db.Column(db.Integer, nullable=True)
    samples_count = db.Column(db.Integer, default=0)
    # Odometer umoznuje pocitat vzdialenost presnejsie ako iba zo speed/time odhadu.
    start_odometer = db.Column(db.Integer, nullable=True)
    end_odometer = db.Column(db.Integer, nullable=True)
    distance_km = db.Column(db.Float, nullable=True)
    # Priemery a maxima sa ukladaju az do suhrnu jazdy, aby sa nemuseli pocitat stale nanovo.
    avg_speed = db.Column(db.Float, nullable=True)
    max_speed = db.Column(db.Integer, nullable=True)
    avg_rpm = db.Column(db.Float, nullable=True)
    max_rpm = db.Column(db.Integer, nullable=True)
    min_rpm = db.Column(db.Integer, nullable=True)
    avg_consumption_l100km = db.Column(db.Float, nullable=True)
    total_fuel_used_l = db.Column(db.Float, nullable=True)
    avg_coolant_temp = db.Column(db.Float, nullable=True)
    max_coolant_temp = db.Column(db.Integer, nullable=True)
    avg_oil_temp = db.Column(db.Float, nullable=True)
    max_oil_temp = db.Column(db.Integer, nullable=True)
    engine_starts = db.Column(db.Integer, default=1)
    # Neuzavreta jazda znamena, ze motor podla poslednych dat este bezal.
    is_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Historia DTC sluzi na spatne zobrazenie uz zachytenych alebo vymazanych chyb.
class DTCCodeHistory(db.Model):
    __tablename__ = "dtc_codes_history"
    id = db.Column(db.Integer, primary_key=True)
    vin_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)
    severity = db.Column(db.String(20), default="medium")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Samostatna vazba pouzivatela na vozidlo umoznuje jedno auto priradit viacerym uctom.
class UserVehicle(db.Model):
    __tablename__ = "user_vehicles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    user = db.relationship("User", backref=db.backref("owned_vehicles", lazy="dynamic"))
    vehicle = db.relationship("Vehicle", backref=db.backref("owners", lazy="dynamic"))
    __table_args__ = (
        db.UniqueConstraint('user_id', 'vehicle_id', name='unique_user_vehicle'),
    )

# Cakajuce prikazy posiela webova aplikacia a RPi si ich nasledne vyzdvihne.
class PendingCommand(db.Model):
    __tablename__ = "pending_commands"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False)
    # PendingCommand sluzi ako jednoducha fronta prikazov pre diagnosticke zariadenie.
    command = db.Column(db.String(50), nullable=False)
    # Po prevzati prikazu zariadenim sa zaznam iba oznaci ako vykonany.
    executed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Lokalna databaza vyznamov DTC kodov importovana z CSV suboru.
class DtcCodeMeaning(db.Model):
    __tablename__ = "dtc_codes_meaning"
    id = db.Column(db.Integer, primary_key=True)
    # Tabulka vyznamov DTC sluzi ako lokalna databaza popisov chyb.
    dtc_code = db.Column(db.String(20), unique=True, nullable=False)
    # Popis DTC kodu je volitelny, lebo nie kazdy kod musi byt v CSV databaze.
    dtc_description = db.Column(db.Text, nullable=True)

# Vzory DTC pomahaju zoskupit suvisiace chybove kody do zrozumitelnejsich problemov.
class DtcPattern(db.Model):
    __tablename__ = "dtc_patterns"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    primary_cause = db.Column(db.String(255), nullable=False)
    source_url = db.Column(db.Text, nullable=True)

class DtcPatternLink(db.Model):
    __tablename__ = "dtc_pattern_links"
    id = db.Column(db.Integer, primary_key=True)
    pattern_id = db.Column(db.Integer, db.ForeignKey("dtc_patterns.id"), nullable=False)
    dtc_code = db.Column(db.String(20), nullable=False)

# Live tabulka drzi iba posledny znamy stav vozidla pre rychle nacitanie dashboardu.
class VehicleTelemetryLive(db.Model):
    __tablename__ = "vehicle_telemetry_live"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, unique=True, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("live_telemetry", uselist=False))
    odometer = db.Column(db.Integer, nullable=True)
    # Zdroj odometra rozlisuje manualne zadanie a hodnotu posielanu zo zariadenia.
    odometer_source = db.Column(db.String(20), nullable=False, default="rpi")
    battery_voltage = db.Column(db.Float, nullable=True)
    battery_health = db.Column(db.String(30), nullable=True)
    # Stav motora je dolezity pre otvorenie alebo ukoncenie jazdy.
    engine_running = db.Column(db.Boolean, nullable=True)
    engine_rpm = db.Column(db.Integer, nullable=True)
    engine_load = db.Column(db.Float, nullable=True)
    coolant_temp = db.Column(db.Integer, nullable=True)
    oil_temp = db.Column(db.Integer, nullable=True)
    intake_air_temp = db.Column(db.Integer, nullable=True)
    consumption_lh = db.Column(db.Float, nullable=True)
    consumption_l100km = db.Column(db.Float, nullable=True)
    maf = db.Column(db.Float, nullable=True)
    fuel_type = db.Column(db.String(20), nullable=True)
    speed = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Historicka telemetria uchovava jednotlive vzorky, z ktorych sa potom rataju statistiky jazd.
class VehicleTelemetryHistory(db.Model):
    __tablename__ = "vehicle_telemetry_history"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("telemetry_history", lazy="dynamic"))
    # Historicka vzorka telemetrie sa moze volitelne priradit ku konkretnej jazde.
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id"), nullable=True, index=True)
    trip = db.relationship("Trip", backref=db.backref("telemetry_samples", lazy="dynamic"))
    odometer = db.Column(db.Integer, nullable=True)
    odometer_source = db.Column(db.String(20), nullable=False, default="rpi")
    battery_voltage = db.Column(db.Float, nullable=True)
    battery_health = db.Column(db.String(30), nullable=True)
    engine_running = db.Column(db.Boolean, nullable=True)
    engine_rpm = db.Column(db.Integer, nullable=True)
    engine_load = db.Column(db.Float, nullable=True)
    coolant_temp = db.Column(db.Integer, nullable=True)
    oil_temp = db.Column(db.Integer, nullable=True)
    intake_air_temp = db.Column(db.Integer, nullable=True)
    consumption_lh = db.Column(db.Float, nullable=True)
    consumption_l100km = db.Column(db.Float, nullable=True)
    maf = db.Column(db.Float, nullable=True)
    fuel_type = db.Column(db.String(20), nullable=True)
    speed = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

# Historia polohy je oddelena od telemetrie, lebo GPS data nemusia prist spolu s OBD udajmi.
class VehicleLocationHistory(db.Model):
    __tablename__ = "vehicle_location_history"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("location_history", lazy="dynamic"))
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id"), nullable=True, index=True)
    trip = db.relationship("Trip", backref=db.backref("location_points", lazy="dynamic"))
    # Poloha sa uklada samostatne, aby sa dala kreslit trasa aj bez celej telemetrie.
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

# Udalosti jazdy ukladaju prudke brzdenia, zrychlenia, zatacky alebo naraz.
class DrivingEvent(db.Model):
    __tablename__ = "driving_events"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=True, index=True)
    # Typ jazdnej udalosti je obmedzeny v utilitach na podporovane hodnoty.
    event_type = db.Column(db.String(50), nullable=False, index=True)
    event_timestamp = db.Column(db.DateTime, nullable=False, index=True)
    speed_kmh = db.Column(db.Float, nullable=True)
    # g_force pomaha odlisit beznu jazdu od prudkej brzdy, zakruty alebo narazu.
    g_force = db.Column(db.Float, nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    accel_x = db.Column(db.Float, nullable=True)
    accel_y = db.Column(db.Float, nullable=True)
    accel_z = db.Column(db.Float, nullable=True)
    gyro_x = db.Column(db.Float, nullable=True)
    gyro_y = db.Column(db.Float, nullable=True)
    gyro_z = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    device = db.relationship("Device", backref=db.backref("driving_events", lazy="dynamic"))
    vehicle = db.relationship("Vehicle", backref=db.backref("driving_events", lazy="dynamic"))
