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

bp = Blueprint("dashboard", __name__)

def dashboard_summary():
    """
    Domovska stranka - suhrn pouzivatelskych dat a vozidla s aktivnymi chybami
    ---
    tags:
      - Dashboard
    security:
      - bearerAuth: []
    responses:
      200:
        description: Dashboard summary
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
            devices = Device.query.filter_by(user_id=user_id).all()
        total_devices = len(devices)
        if user.role == "admin":
            user_vehicles = Vehicle.query.all()
        else:
            user_vehicles = (
                db.session.query(Vehicle)
                .join(UserVehicle, UserVehicle.vehicle_id == Vehicle.id)
                .filter(UserVehicle.user_id == user_id)
                .all()
            )
        total_vehicles = len(user_vehicles)
        vehicle_ids = [v.id for v in user_vehicles]
        active_dtcs_count = 0
        vehicles_with_issues = []
        if vehicle_ids:
            active_dtcs_count = (
                DTCCodeActive.query
                .filter(DTCCodeActive.vin_id.in_(vehicle_ids))
                .count()
            )
            dtc_counts = (
                db.session.query(
                    DTCCodeActive.vin_id,
                    func.count(DTCCodeActive.id).label("dtc_count")
                )
                .filter(DTCCodeActive.vin_id.in_(vehicle_ids))
                .group_by(DTCCodeActive.vin_id)
                .all()
            )
            dtc_count_map = {row.vin_id: row.dtc_count for row in dtc_counts}
            for vehicle in user_vehicles:
                dtc_count = dtc_count_map.get(vehicle.id, 0)
                if dtc_count <= 0:
                    continue
                device_link = DeviceVehicle.query.filter_by(last_vin_id=vehicle.id).first()
                linked_device = Device.query.get(device_link.device_id) if device_link else None
                owner_links = UserVehicle.query.filter_by(vehicle_id=vehicle.id).all()
                owner_user_ids = [link.user_id for link in owner_links]
                primary_user_id = owner_user_ids[0] if owner_user_ids else None
                vehicles_with_issues.append({
                    "vehicle_id": vehicle.id,
                    "vin": vehicle.vin,
                    "brand": vehicle.brand,
                    "model": vehicle.model,
                    "year": vehicle.year,
                    "engine": vehicle.engine,
                    "dtc_count": dtc_count,
                    "device_id": linked_device.id if linked_device else None,
                    "online": linked_device.status if linked_device else False,
                    "user_id": primary_user_id,
                    "owner_user_ids": owner_user_ids
                })
        vehicles_with_issues.sort(key=lambda x: x["dtc_count"], reverse=True)
        return jsonify({
            "status": "success",
            "summary": {
                "total_devices": total_devices,
                "total_vehicles": total_vehicles,
                "active_dtcs": active_dtcs_count,
                "vehicles_with_issues": len(vehicles_with_issues)
            },
            "vehicles_with_issues_list": vehicles_with_issues
        }), 200
    except Exception as e:
        print("❌ DASHBOARD SUMMARY ERROR:", e)
        return jsonify({"error": str(e)}), 500

# URL rules
bp.add_url_rule('/api/dashboard-summary', endpoint='dashboard_summary', view_func=jwt_required()(dashboard_summary), methods=['GET'])
bp.add_url_rule('/api/dashboard/summary', endpoint='dashboard_summary_alt', view_func=jwt_required()(dashboard_summary), methods=['GET'])
