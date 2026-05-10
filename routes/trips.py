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

bp = Blueprint("trips", __name__)

# Jazdy sa nacitavaju podla VIN a vratia sa iba pouzivatelovi s pristupom k vozidlu.
def get_vehicle_trips(vin):
    """
    Ziskanie vsetkych jazd pre konkretne vozidlo
    ---
    tags:
      - Trips
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
    responses:
      200:
        description: Zoznam jazd
    """
    try:
        refresh_stale_device_statuses()
        user_id = int(get_jwt_identity())
        # Prava sa kontroluju cez prihlaseneho pouzivatela z JWT tokenu.
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        # Jazdy sa vyhladavaju podla VIN, ktory pozna frontend aj pouzivatel.
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        # Bez vozidla nema zmysel pokracovat vo vybere jazd.
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
        user_vehicle = UserVehicle.query.filter_by(
            user_id=user_id,
            vehicle_id=vehicle.id
        ).first()
        if not user_vehicle and user.role != "admin":
            return jsonify({"error": "Vehicle not owned by user"}), 403
        # Jazdy sa vracaju od najnovsej, co je vhodne pre historiu vo frontende.
        trips = Trip.query.filter_by(
            vehicle_id=vehicle.id,
            is_completed=True
        ).order_by(Trip.start_time.desc()).all()
        trips_data = []
        # ORM objekty sa prekladaju na JSON rucne, aby mal frontend presny tvar dat.
        for trip in trips:
            if not trip.avg_consumption_l100km and trip.samples_count > 0:
                consumptions = db.session.query(
                    func.avg(VehicleTelemetryHistory.consumption_l100km)
                ).filter(
                    VehicleTelemetryHistory.trip_id == trip.id,
                    VehicleTelemetryHistory.consumption_l100km.isnot(None)
                ).scalar()
                trip.avg_consumption_l100km = consumptions
            location_points = (
                VehicleLocationHistory.query
                .filter_by(trip_id=trip.id)
                .order_by(VehicleLocationHistory.created_at.asc())
                .all()
            )
            trips_data.append({
                "id": trip.id,
                "start_time": _iso(trip.start_time),
                "end_time": _iso(trip.end_time),
                "duration_seconds": trip.duration_seconds,
                "samples_count": trip.samples_count,
                "distance_km": round(trip.distance_km, 1) if trip.distance_km else None,
                "avg_speed": round(trip.avg_speed, 1) if trip.avg_speed else None,
                "max_speed": trip.max_speed,
                "avg_rpm": round(trip.avg_rpm) if trip.avg_rpm else None,
                "max_rpm": trip.max_rpm,
                "min_rpm": trip.min_rpm,
                "avg_consumption_l100km": round(trip.avg_consumption_l100km, 1) if trip.avg_consumption_l100km else None,
                "total_fuel_used_l": round(trip.total_fuel_used_l, 2) if trip.total_fuel_used_l else None,
                "avg_coolant_temp": round(trip.avg_coolant_temp) if trip.avg_coolant_temp else None,
                "max_coolant_temp": trip.max_coolant_temp,
                "avg_oil_temp": round(trip.avg_oil_temp) if trip.avg_oil_temp else None,
                "max_oil_temp": trip.max_oil_temp,
                "engine_starts": trip.engine_starts,
                "start_odometer": trip.start_odometer,
                "end_odometer": trip.end_odometer,
                "location_points": [
                    {
                        "latitude": p.latitude,
                        "longitude": p.longitude,
                        "timestamp": _iso(p.created_at)
                    }
                    for p in location_points
                ]
            })
        return jsonify({
            "status": "success",
            "vin": vin,
            "vehicle": {
                "brand": vehicle.brand,
                "model": vehicle.model,
                "year": vehicle.year
            },
            "trips": trips_data,
            "total_trips": len(trips_data)
        }), 200
    except Exception as e:
        print("❌ GET VEHICLE TRIPS ERROR:", e)
        return jsonify({"error": str(e)}), 500

# URL rules
bp.add_url_rule('/api/vehicle/<vin>/trips', endpoint='get_vehicle_trips', view_func=jwt_required()(get_vehicle_trips), methods=['GET'])
