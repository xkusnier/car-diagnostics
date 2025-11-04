from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flasgger import Swagger
from datetime import datetime
import os
import requests
from flask_cors import CORS
import csv
import requests
from io import StringIO
from flask import jsonify
from datetime import datetime, timedelta
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity  # Overenie importu
from routes.auth_routes import auth_bp
from routes.device_routes import device_bp
from routes.vin_routes import vin_bp
from models import db

app.register_blueprint(auth_bp)
app.register_blueprint(device_bp)
app.register_blueprint(vin_bp)


# Inicializácia globálnych objektov
db = SQLAlchemy()
jwt = JWTManager()


def create_app():
    app = Flask(__name__)
    CORS(app)
    Swagger(app)

    # ------------------- CONFIG -------------------
    app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "your-secret-key")
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)

    db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # ------------------- INIT EXTENSIONS -------------------
    db.init_app(app)
    jwt.init_app(app)

    # ------------------- REGISTER BLUEPRINTS -------------------
    from routes.auth_routes import auth_bp
    from routes.device_routes import device_bp
    from routes.vin_routes import vin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(device_bp)
    app.register_blueprint(vin_bp)

    # ------------------- SIMPLE ROUTE -------------------
    @app.route("/")
    def home():
        return jsonify({"status": "ok", "message": "Flask beží modularne"})

    with app.app_context():
        db.create_all()

    return app


# ✅ Toto je kritické: Render (Gunicorn) potrebuje objekt app
app = create_app()

# ✅ Toto slúži len pre lokálne spustenie
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
