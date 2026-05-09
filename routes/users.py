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

bp = Blueprint("users", __name__)

def login():
    """
    Prihlasenie pouzivatela
    ---
    tags:
      - Authentication
    consumes:
      - application/json
    produces:
      - application/json
    description: |
      Prihlasi pouzivatela podla emailu alebo username a vrati JWT token.
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - identifier
            - password
          properties:
            identifier:
              type: string
              description: Email alebo username pouzivatela
              example: "user@example.com"
            password:
              type: string
              example: "heslo123"
    responses:
      200:
        description: Uspesne prihlasenie
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            access_token:
              type: string
            role:
              type: string
            username:
              type: string
            email:
              type: string
      400:
        description: Chybajuce prihlasovacie udaje
      401:
        description: Nespravne prihlasovacie udaje
      415:
        description: Content-Type must be application/json
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        identifier = data.get("identifier")
        password = data.get("password")
        if not identifier or not password:
            return jsonify({"error": "Missing identifier or password"}), 400
        identifier = identifier.strip()
        user = User.query.filter(
            or_(
                func.lower(User.email) == identifier.lower(),
                func.lower(User.username) == identifier.lower()
            )
        ).first()
        if not user or user.password != password:
            return jsonify({"error": "Invalid credentials"}), 401
        access_token = create_access_token(identity=str(user.id))
        return jsonify({
            "status": "success",
            "access_token": access_token,
            "role": user.role,
            "username": user.username,
            "email": user.email
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def register():
    """
    Registracia noveho pouzivatela
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - username
            - email
            - password
          properties:
            username:
              type: string
              example: "jozef"
            email:
              type: string
              example: "user@example.com"
            password:
              type: string
              example: "heslo123"
    responses:
      201:
        description: Pouzivatel bol zaregistrovany
      400:
        description: Chybajuce alebo neplatne udaje
      409:
        description: Email alebo username uz existuje
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        username = data.get("username")
        email = data.get("email")
        password = data.get("password")
        if not username or not email or not password:
            return jsonify({"error": "Missing username, email or password"}), 400
        username = username.strip()
        if len(username) < 3:
            return jsonify({"error": "Username must be at least 3 characters long"}), 400
        if User.query.filter_by(email=email).first():
            return jsonify({"error": "Email already exists"}), 409
        if User.query.filter_by(username=username).first():
            return jsonify({"error": "Username already exists"}), 409
        new_user = User(username=username, email=email, password=password, role="user")
        db.session.add(new_user)
        db.session.commit()
        return jsonify({
            "status": "success",
            "message": "User registered"
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# URL rules
bp.add_url_rule('/api/login', endpoint='login', view_func=login, methods=['POST'])
bp.add_url_rule('/api/register', endpoint='register', view_func=register, methods=['POST'])
