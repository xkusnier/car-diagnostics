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

bp = Blueprint("vehicles", __name__)

# Odstranenie vozidla z uctu maze iba vazbu pouzivatela na vozidlo, nie samotne vozidlo.
def delete_user_vehicle(vin):
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
    """
    Vymazanie vztahu medzi pouzivatelom a vozidlom
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    description: |
        Vymaze zaznam z tabulky user_vehicles pre prihlaseneho pouzivatela a dane VIN.
        Tym sa vozidlo odstrani zo zoznamu vozidiel pouzivatela.
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        description: VIN cislo vozidla
    responses:
      200:
        description: Vozidlo uspesne odstranene
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            message:
              type: string
      404:
        description: Vozidlo neexistuje alebo nie je priradene pouzivatelovi
      500:
        description: Server error
    """
    try:
        # Vztah k vozidlu sa maze v kontexte aktualne prihlaseneho pouzivatela.
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        # VIN z URL sa normalizuje na velke pismena pred hladanim.
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
        # Admin moze odstranit vazbu na vozidlo vseobecne, bez kontroly konkretneho vlastnika.
        # Admin moze odstranit lubovolnu vazbu na vozidlo, bez ohladu na vlastnika.
        if user.role == "admin":
            user_vehicle = UserVehicle.query.filter_by(vehicle_id=vehicle.id).first()
        else:
            user_vehicle = UserVehicle.query.filter_by(
                user_id=user_id,
                vehicle_id=vehicle.id
            ).first()
        if not user_vehicle:
            return jsonify({"error": "Vehicle not associated with this user"}), 404
        # Maze sa iba zaznam v UserVehicle, nie samotne auto ani jeho historia.
        db.session.delete(user_vehicle)
        db.session.commit()
        return jsonify({
            "status": "success",
            "message": f"Vehicle {vin} removed from your list"
        }), 200
    except Exception as e:
        db.session.rollback()
        print("❌ DELETE USER VEHICLE ERROR:", e)
        return jsonify({"error": str(e)}), 500

# Odometer sa cita z live telemetrie, kde moze byt manualny alebo z RPi zdroja.
def get_vehicle_odometer(vin):
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
    """
    Ziskanie odometra vozidla podla VIN
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        description: VIN vozidla
    responses:
      200:
        description: Aktualny odometer vozidla alebo prazdna hodnota
      403:
        description: Vozidlo nepatri pouzivatelovi
      404:
        description: Pouzivatel alebo vozidlo neexistuje
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
        # Pri citani detailov vozidla sa kontroluje, ci bezny pouzivatel auto vlastni.
        if user.role != "admin":
            user_vehicle = UserVehicle.query.filter_by(user_id=user_id, vehicle_id=vehicle.id).first()
            if not user_vehicle:
                return jsonify({"error": "Vehicle not owned by user"}), 403
        # Odometer je ulozeny v live telemetrii, lebo ide o aktualny stav vozidla.
        # Odometer sa cita z live telemetrie, lebo tam je posledna znama hodnota.
        live_row = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle.id).first()
        # Ak vozidlo este nema live telemetry riadok, vytvori sa minimalny zaznam.
        if not live_row:
            return jsonify({
                "status": "success",
                "vin": vehicle.vin,
                "vehicle_id": vehicle.id,
                "odometer": None,
                "odometer_source": "rpi"
            }), 200
        return jsonify({
            "status": "success",
            "vin": vehicle.vin,
            "vehicle_id": vehicle.id,
            "odometer": live_row.odometer,
            "odometer_source": live_row.odometer_source or "rpi"
        }), 200
    except Exception as e:
        print("❌ GET VEHICLE ODOMETER ERROR:", e)
        return jsonify({"error": str(e)}), 500

# Manualna uprava odometra meni zdroj hodnoty a pouziva sa hlavne pri chybajucom RPi odometri.
def update_vehicle_odometer(vin):
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
    """
    Aktualizacia zdroja a hodnoty odometra vozidla
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    parameters:
      - in: path
        name: vin
        required: true
        type: string
        description: VIN vozidla
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - odometer_source
          properties:
            odometer_source:
              type: string
              enum: [manual, rpi]
              example: manual
            odometer:
              type: number
              example: 123456
    responses:
      200:
        description: Odometer bol aktualizovany
      400:
        description: Neplatny zdroj alebo hodnota odometra
      403:
        description: Vozidlo nepatri pouzivatelovi
      404:
        description: Pouzivatel alebo vozidlo neexistuje
      500:
        description: Server error
    """
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        vehicle = Vehicle.query.filter_by(vin=vin.upper()).first()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404
        if user.role != "admin":
            user_vehicle = UserVehicle.query.filter_by(user_id=user_id, vehicle_id=vehicle.id).first()
            if not user_vehicle:
                return jsonify({"error": "Vehicle not owned by user"}), 403
        # Aktualizacia odometra prijima zdroj a hodnotu z tela requestu.
        payload = request.get_json()
        odometer_source = (payload.get("odometer_source") or "").strip().lower()
        odometer_value = payload.get("odometer")
        # Zdroj odometra obmedzujem na dve podporovane moznosti, aby sa s nim dalo dalej pocitat.
        # Zdroj odometra je obmedzeny na hodnoty, ktore pozna aj frontend.
        if odometer_source not in {"manual", "rpi"}:
            return jsonify({"error": "odometer_source must be 'manual' or 'rpi'"}), 400
        live_row = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle.id).first()
        if not live_row:
            live_row = VehicleTelemetryLive(vehicle_id=vehicle.id)
            db.session.add(live_row)
            db.session.flush()
        live_row.odometer_source = odometer_source
        # Pri manualnom zdroji musi prist aj ciselna hodnota odometra.
        if odometer_source == "manual":
            if odometer_value is None:
                return jsonify({"error": "Missing odometer for manual source"}), 400
            try:
                live_row.odometer = int(round(float(odometer_value)))
            except (TypeError, ValueError):
                return jsonify({"error": "odometer must be a number"}), 400
        db.session.commit()
        return jsonify({
            "status": "success",
            "vin": vehicle.vin,
            "vehicle_id": vehicle.id,
            "odometer": live_row.odometer,
            "odometer_source": live_row.odometer_source
        }), 200
    except Exception as e:
        db.session.rollback()
        print("❌ UPDATE VEHICLE ODOMETER ERROR:", e)
        return jsonify({"error": str(e)}), 500

# Porovnanie vozidiel pocita agregacie nad historickou telemetriou pre kazde dostupne auto.
def vehicles_telemetry_comparison():
    # AI: Tento endpoint bol ciastocne generovany pomocou ChatGPT a nasledne upraveny autorom.
    # AI: Swagger dokumentacia pre tento endpoint bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
    """
    Porovnanie telemetrie pre vsetky vozidla pouzivatela
    ---
    tags:
      - Vehicles
    security:
      - bearerAuth: []
    description: |
      Vrati statisticke udaje z historickych telemetrickych dat pre vozidla pouzivatela.
      Bezný pouzivatel vidi iba svoje vozidla. Admin vidi vsetky vozidla.
      V odpovedi sa vracia zoznam vozidiel, stav online/offline, priradene zariadenie
      a vypocitane statistiky ako priemerne otacky, priemerna rychlost, priemerna
      spotreba, maximalne/minimalne otacky, odometer a pocet vzoriek.
    responses:
      200:
        description: Porovnanie telemetrickych udajov vozidiel
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            vehicles:
              type: array
              items:
                type: object
                properties:
                  device_id:
                    type: integer
                    example: 1
                  vin:
                    type: string
                    example: "1HGCM82633A123456"
                  brand:
                    type: string
                    example: "Honda"
                  model:
                    type: string
                    example: "Accord"
                  year:
                    type: string
                    example: "2021"
                  engine:
                    type: string
                    example: "2.0L"
                  online:
                    type: boolean
                    example: true
                  user_id:
                    type: integer
                    example: 1
                  owner_user_ids:
                    type: array
                    items:
                      type: integer
                    example: [1]
                  statistics:
                    type: object
                    properties:
                      avg_rpm:
                        type: integer
                        example: 2200
                      avg_speed:
                        type: integer
                        example: 65
                      avg_consumption:
                        type: number
                        example: 7.2
                      max_rpm:
                        type: integer
                        example: 3000
                      min_rpm:
                        type: integer
                        example: 900
                      total_odometer:
                        type: integer
                        example: 123456
                      odometer_source:
                        type: string
                        example: rpi
                      samples:
                        type: integer
                        example: 25
            summary:
              type: object
              properties:
                total_vehicles:
                  type: integer
                  example: 2
                online_vehicles:
                  type: integer
                  example: 1
                total_samples:
                  type: integer
                  example: 150
      404:
        description: Pouzivatel neexistuje
      500:
        description: Server error
    """
    try:
        # Porovnanie vozidiel najprv aktualizuje online/offline stav zariadeni.
        refresh_stale_device_statuses()
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        vehicles_data = []
        online_count = 0
        # Admin vidi v porovnani vsetky vozidla, bezny pouzivatel iba svoje priradene auta.
        # Admin porovnava vsetky vozidla a zariadenia v systeme.
        if user.role == "admin":
            vehicles = Vehicle.query.all()
            devices = Device.query.all()
        else:
            user_vehicles = UserVehicle.query.filter_by(user_id=user_id).all()
            vehicles = [uv.vehicle for uv in user_vehicles]
            devices = Device.query.filter_by(user_id=user_id).all()
        vehicle_device_map = {}
        # Pri viacerych zariadeniach sa preferuje online zariadenie.
        for d in devices:
            if d.link and len(d.link) > 0 and d.link[0].last_vin_id:
                vehicle_id = d.link[0].last_vin_id
                existing = vehicle_device_map.get(vehicle_id)
                if existing is None:
                    vehicle_device_map[vehicle_id] = d
                else:
                    if not existing.status and d.status:
                        vehicle_device_map[vehicle_id] = d
        # Pre kazde vozidlo sa sklada suhrn vlastnikov, zariadenia a live telemetrie.
        for vehicle in vehicles:
            # Vlastnici sa citaju osobitne, aby sa adminovi zobrazili aj mena pouzivatelov.
            owner_links = UserVehicle.query.filter_by(vehicle_id=vehicle.id).all()
            owner_user_ids = [link.user_id for link in owner_links]
            primary_user_id = owner_user_ids[0] if owner_user_ids else None
            linked_device = vehicle_device_map.get(vehicle.id)
            device_status = linked_device.status if linked_device else False
            device_id = linked_device.id if linked_device else None
            if device_status:
                online_count += 1
            stats = db.session.query(
                func.avg(VehicleTelemetryHistory.engine_rpm).label("avg_rpm"),
                func.avg(VehicleTelemetryHistory.speed).label("avg_speed"),
                func.avg(VehicleTelemetryHistory.consumption_l100km).label("avg_consumption"),
                func.max(VehicleTelemetryHistory.engine_rpm).label("max_rpm"),
                func.min(VehicleTelemetryHistory.engine_rpm).label("min_rpm"),
                func.count(VehicleTelemetryHistory.id).label("samples")
            ).filter(
                VehicleTelemetryHistory.vehicle_id == vehicle.id
            ).first()
            # Live riadok sa pripaja iba ak uz auto poslalo aspon jednu telemetricku vzorku.
            live = VehicleTelemetryLive.query.filter_by(vehicle_id=vehicle.id).first()
            vehicles_data.append({
                "device_id": device_id,
                "vin": vehicle.vin,
                "brand": vehicle.brand,
                "model": vehicle.model,
                "year": vehicle.year,
                "engine": vehicle.engine,
                "online": device_status,
                "user_id": primary_user_id,
                "owner_user_ids": owner_user_ids,
                "statistics": {
                    "avg_rpm": round(stats.avg_rpm) if stats and stats.avg_rpm is not None else None,
                    "avg_speed": round(stats.avg_speed) if stats and stats.avg_speed is not None else None,
                    "avg_consumption": round(stats.avg_consumption, 1) if stats and stats.avg_consumption is not None else None,
                    "max_rpm": stats.max_rpm if stats and stats.max_rpm is not None else None,
                    "min_rpm": stats.min_rpm if stats and stats.min_rpm is not None else None,
                    "total_odometer": live.odometer if live else None,
                    "odometer_source": (live.odometer_source if live and live.odometer_source else "rpi"),
                    "samples": stats.samples if stats and stats.samples else 0
                }
            })
        total_stats = {
            "total_vehicles": len(vehicles_data),
            "online_vehicles": online_count,
            "total_samples": sum(v.get("statistics", {}).get("samples", 0) for v in vehicles_data)
        }
        return jsonify({
            "status": "success",
            "vehicles": vehicles_data,
            "summary": total_stats
        }), 200
    except Exception as e:
        print("❌ VEHICLES TELEMETRY COMPARISON ERROR:", e)
        return jsonify({"error": str(e)}), 500

# URL rules
bp.add_url_rule('/api/vehicle/<vin>', endpoint='delete_user_vehicle', view_func=jwt_required()(delete_user_vehicle), methods=['DELETE'])
bp.add_url_rule('/api/user-vehicle/<vin>', endpoint='delete_user_vehicle_alt', view_func=jwt_required()(delete_user_vehicle), methods=['DELETE'])
bp.add_url_rule('/api/vehicle/<vin>/odometer', endpoint='get_vehicle_odometer', view_func=jwt_required()(get_vehicle_odometer), methods=['GET'])
bp.add_url_rule('/api/vehicle/<vin>/odometer', endpoint='update_vehicle_odometer', view_func=jwt_required()(update_vehicle_odometer), methods=['PUT'])
bp.add_url_rule('/api/vehicles/telemetry-comparison', endpoint='vehicles_telemetry_comparison', view_func=jwt_required()(vehicles_telemetry_comparison), methods=['GET'])

# --- Suhrn vyuzitia AI ---
# AI: V tomto subore bola pomocou ChatGPT vygenerovana a nasledne autorom upravena Swagger dokumentacia oznacenych endpointov.
# AI: Oznacene endpointy boli ciastocne generovane pomocou ChatGPT a nasledne upravene autorom.
