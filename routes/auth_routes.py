from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token
from models import db, User
from app.models import User

# ✅ Blueprint definícia
auth_bp = Blueprint("auth", __name__, url_prefix="/api")


@auth_bp.route("/register", methods=["POST"])
def register():
    """
    Registrácia nového používateľa
    """
    try:
        data = request.get_json()
        email = data.get("email")
        password = data.get("password")

        if not email or not password:
            return jsonify({"error": "Missing email or password"}), 400

        # Skontroluj, či už email existuje
        if User.query.filter_by(email=email).first():
            return jsonify({"error": "Email already exists"}), 409

        # Vytvor nového používateľa
        new_user = User(email=email, password=password, role="user")
        db.session.add(new_user)
        db.session.commit()

        return jsonify({"status": "success", "message": "User registered"}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Prihlásenie používateľa, vráti JWT token
    """
    try:
        data = request.get_json()
        email = data.get("email")
        password = data.get("password")

        if not email or not password:
            return jsonify({"error": "Missing email or password"}), 400

        user = User.query.filter_by(email=email).first()
        if not user or user.password != password:
            return jsonify({"error": "Invalid credentials"}), 401

        # ✅ Vytvor JWT token
        token = create_access_token(identity=str(user.id))

        return jsonify({
            "status": "success",
            "access_token": token,
            "role": user.role
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
