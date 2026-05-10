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

# Dashboard z viacerych tabuliek sklada rychly prehlad pre prihlaseneho pouzivatela.
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
        # Pred skladanim dashboardu sa najprv upravia zariadenia, ktore uz davno neposlali heartbeat.
        refresh_stale_device_statuses()
        # Identita z JWT sa konvertuje na int, lebo v DB je user id cislo.
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            # Odpoved je jeden agregovany JSON, aby frontend nemusel volat viac endpointov naraz.
            return jsonify({"error": "User not found"}), 404
        # Admin vidi celkovy stav systemu, bez filtrovania iba na vlastne auta.
        if user.role == "admin":
            devices = Device.query.all()
        else:
            devices = Device.query.filter_by(user_id=user_id).all()
        # Pocty sa pocitaju az po filtrovani podla roly pouzivatela.
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
        # ID vozidiel sa pouziju na hromadne filtrovanie DTC kodov.
        vehicle_ids = [v.id for v in user_vehicles]
        active_dtcs_count = 0
        vehicles_with_issues = []
        # Ak pouzivatel nema ziadne vozidla, DTC dotazy sa zbytocne nespustaju.
        if vehicle_ids:
            active_dtcs_count = (
                DTCCodeActive.query
                .filter(DTCCodeActive.vin_id.in_(vehicle_ids))
                .count()
            )
            # DTC kody sa zoskupia podla vozidla, aby sa vedelo ktore auta maju problem.
            dtc_counts = (
                db.session.query(
                    DTCCodeActive.vin_id,
                    func.count(DTCCodeActive.id).label("dtc_count")
                )
                .filter(DTCCodeActive.vin_id.in_(vehicle_ids))
                .group_by(DTCCodeActive.vin_id)
                .all()
            )
            # Mapa zrychli skladanie zoznamu vozidiel s chybami.
            dtc_count_map = {row.vin_id: row.dtc_count for row in dtc_counts}
            for vehicle in user_vehicles:
                dtc_count = dtc_count_map.get(vehicle.id, 0)
                # Vozidla bez aktivnych chyb sa do problemoveho zoznamu nedavaju.
                if dtc_count <= 0:
                    continue
                # Cez posledny link sa zisti, ktore zariadenie naposledy posielalo data pre vozidlo.
                device_link = DeviceVehicle.query.filter_by(last_vin_id=vehicle.id).first()
                linked_device = Device.query.get(device_link.device_id) if device_link else None
                # Pri vozidle sa vracaju aj vlastnici, aby admin videl komu auto patri.
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
        # Najproblematickejsie vozidla idu navrch podla poctu aktivnych DTC.
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
