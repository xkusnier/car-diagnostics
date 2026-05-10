from flask_socketio import emit, join_room
from extensions import db, socketio
from models import *
from utils import *

# Socket.IO event pre zakladne pripojenie klienta.
def ws_connect():
    # Po pripojeni klient hned dostane potvrdenie, ze websocket spojenie funguje.
    emit("server_ready", {"status": "ok"})

# Klient sa prihlasi do miestnosti podla device_id, aby dostaval iba data pre dane zariadenie.
def ws_subscribe_device(data):
    """
    FE: socket.emit("subscribe_device", {device_id: 1})
    """
    try:
        # Device id sa prevadza na cislo, aby nazov roomky bol konzistentny.
        device_id = int(data.get("device_id"))
    except Exception:
        emit("error", {"error": "invalid device_id"})
        return
    # Roomka oddeluje data roznych zariadeni medzi pripojenymi klientmi.
    join_room(f"device:{device_id}")
    emit("subscribed", {"device_id": device_id})

# Telemetria prijata cez WebSocket sa najprv ulozi a potom sa rozosle ostatnym klientom.
def ws_telemetry(data):
    """
    RPi (alebo test client) posiela realtime telemetry cez WS.
    Server to uloží + pushne FE.
    """
    try:
        device_id = int(data.get("device_id"))
    except Exception:
        emit("error", {"error": "missing/invalid device_id"})
        return
    # Telemetria cez WS sa prijima iba od znameho zariadenia.
    device = Device.query.get(device_id)
    if not device:
        emit("error", {"error": "device not found"})
        return
    # Rovnaky normalizacny kod sa pouziva pre HTTP aj WebSocket telemetriu.
    t = _telemetry_payload(device_id, data)
    try:
        # Kazda WS telemetria zaroven obnovi online stav zariadenia.
        mark_device_online(device)
        db.session.commit()
    except Exception:
        db.session.rollback()
    # Ulozenie do DB je spolocne s HTTP endpointom, aby nevznikli rozdiely v spracovani.
    _save_telemetry_to_db(device_id, t)
    # Aktualizacia sa posiela iba klientom prihlasenym na konkretne zariadenie.
    socketio.emit("telemetry_update", t, room=f"device:{device_id}")
    emit("telemetry_ack", {"ok": True, "timestamp": t["timestamp"]})
