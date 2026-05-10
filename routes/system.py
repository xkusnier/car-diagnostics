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

bp = Blueprint("system", __name__)

# Jednoduchy health endpoint pre Render alebo rychle overenie, ze backend bezi.
def health_check():
    """
    Health check endpoint
    ---
    tags:
      - System
    description: Overi, ci server bezi a vrati jednoduchy stav aplikacie.
    responses:
      200:
        description: Server je dostupny
        schema:
          type: object
          properties:
            status:
              type: string
              example: ok
    """
    # Health check vracia jednoduchu odpoved pre hosting alebo rychlu kontrolu servera.
    return jsonify({"status": "ok"}), 200

# Inicializacia DB je ponechana ako pomocny endpoint pre nasadenie a testovanie.
def init_db():
    """
    Inicializacia databazy
    ---
    tags:
      - System
    description: |
      Vytvori vsetky tabulky v databaze podla definovanych modelov.
      **Testovanie cez Postman:**
      - Metoda: `GET`
      - URL: `http://car-diagnostics.onrender.com/init-db`
      - Headers: ziadne
      - Body: ziadne
    responses:
      200:
        description: Databaza uspesne vytvorena
        schema:
          type: object
          properties:
            status:
              type: string
              example: "Database ok"
    """
    # Inicializacia vytvori tabulky podla aktualnych SQLAlchemy modelov.
    db.create_all()
    return jsonify({"status": "Database ok"})

# Domovska odpoved sluzi hlavne ako rozcestnik a kontrola dostupnych endpointov.
def home():
    """
    Health check endpoint
    ---
    tags:
      - System
    description: |
      Overenie, ci server bezi.
      **Testovanie cez Postman:**
      - Metoda: `GET`
      - URL: `http://car-diagnostics.onrender.com/`
      - Headers: ziadne
      - Body: ziadne
      **Ocakavana odpoved:**
      ```json
      {
        "status": "ok",
        "message": "Flask bezi"
      }
      ```
    responses:
      200:
        description: Server bezi
        schema:
          type: object
          properties:
            status:
              type: string
              example: "ok"
            message:
              type: string
              example: "Flask bezi"
    """
    return jsonify({"status": "ok", "message": "Flask bezi"})

# Debug vypis dat z databazy pomaha pri kontrole stavu pocas vyvoja.
def show_all():
    """
    Vypis vsetkych vozidiel a DTC kodov
    ---
    tags:
      - System
    responses:
      200:
        description: Zoznam vozidiel s aktivnymi a historickymi DTC kodmi
      500:
        description: Server error
    """
    try:
        vehicles = Vehicle.query.all()
        data = []
        for v in vehicles:
            data.append({
                "vin": v.vin,
                "dtc_codes_active": [d.dtc_code for d in v.dtcs_active],
                "dtc_codes_history": [d.dtc_code for d in v.dtcs_history]
            })
        return jsonify(data), 200
    except Exception as e:
        print("❌ SHOW ALL ERROR:", e)
        return jsonify({"error": str(e)}), 500

# URL rules
bp.add_url_rule('/', endpoint='home', view_func=home, methods=['GET'])
bp.add_url_rule('/health', endpoint='health', view_func=health_check, methods=['GET'])
bp.add_url_rule('/api/health', endpoint='api_health', view_func=health_check, methods=['GET'])
bp.add_url_rule('/init-db', endpoint='init_db', view_func=init_db, methods=['GET'])
bp.add_url_rule('/api/init-db', endpoint='api_init_db', view_func=init_db, methods=['GET'])
bp.add_url_rule('/show-all', endpoint='show_all', view_func=show_all, methods=['GET'])
bp.add_url_rule('/api/show-all', endpoint='api_show_all', view_func=show_all, methods=['GET'])
bp.add_url_rule('/api/all', endpoint='api_all', view_func=show_all, methods=['GET'])
