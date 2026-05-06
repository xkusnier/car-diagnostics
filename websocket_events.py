from flask_socketio import emit, join_room
from extensions import db, socketio
from models import *
from utils import *

def ws_connect():
    emit("server_ready", {"status": "ok"})

def ws_subscribe_device(data):
    """
    FE: socket.emit("subscribe_device", {device_id: 1})
    """
    try:
        device_id = int(data.get("device_id"))
    except Exception:
        emit("error", {"error": "invalid device_id"})
        return
    join_room(f"device:{device_id}")
    emit("subscribed", {"device_id": device_id})

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
    device = Device.query.get(device_id)
    if not device:
        emit("error", {"error": "device not found"})
        return
    t = _telemetry_payload(device_id, data)
    try:
        mark_device_online(device)
        db.session.commit()
    except Exception:
        db.session.rollback()
    _save_telemetry_to_db(device_id, t)
    socketio.emit("telemetry_update", t, room=f"device:{device_id}")
    emit("telemetry_ack", {"ok": True, "timestamp": t["timestamp"]})
