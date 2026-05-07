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

bp = Blueprint("vin", __name__)

def validate_vin_endpoint():
    """
    Validacia VIN formatu a checksumu
    ---
    tags:
      - VIN
    security:
      - bearerAuth: []
    description: |
      Overi:
      - ci VIN ma spravny format
      - ci ma spravny checksum
      - ci sa vozidlo nachadza v databaze
      Mozne stavy:
      - invalid_format
      - invalid_checksum
      - not_found
      - valid
      **Priklad requestu:**
      ```json
      {
        "vin": "WF0XXXXX12345678"
      }
      ```
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - vin
          properties:
            vin:
              type: string
              example: "1HGCM82633A123456"
    responses:
      200:
        description: Validacia vykonana
      400:
        description: Chybajuci VIN
      500:
        description: Server error
    """
    try:
        payload = request.get_json()
        vin = (payload.get("vin") or "").strip().upper()
        if not vin:
            return jsonify({"error": "Missing 'vin' parameter"}), 400
        validation = validate_vin_value(vin)
        if not validation["valid"]:
            return jsonify({
                "status": "invalid",
                "vin": vin,
                **validation
            }), 200
        vehicle = Vehicle.query.filter_by(vin=vin).first()
        if not vehicle:
            return jsonify({
                "status": "not_found",
                "vin": vin,
                "valid": True,
                "reason": "not_found",
                "message": "Vozidlo nie je v našej databáze."
            }), 200
        return jsonify({
            "status": "valid",
            "vin": vin,
            "valid": True,
            "reason": None,
            "message": "VIN is valid",
            "vehicle_exists": True
        }), 200
    except Exception as e:
        print("❌ VIN VALIDATION ERROR:", e)
        return jsonify({"error": str(e)}), 500

# URL rules
bp.add_url_rule('/api/validate-vin', endpoint='validate_vin_endpoint', view_func=jwt_required()(validate_vin_endpoint), methods=['POST'])
