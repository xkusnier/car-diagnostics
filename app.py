import eventlet
eventlet.monkey_patch()

import os
from datetime import timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from flasgger import Swagger
from extensions import db, jwt, socketio

SWAGGER_CONFIG = {
    'title': 'Inteligentna diagnostika API',
    'uiversion': 3,
    'openapi': '3.0.2',
    'doc_expansion': 'list',
    'description': '''
        API pre bakalarsku pracu - diagnostika vozidiel
        ## Testovanie cez Postman
        Vsetky endpointy je mozne testovat v Postmane podla nasledujucich prikladov:
        ### Autentifikacia
        1. Zaregistruj sa: `POST /api/register`
        2. Prihlas sa: `POST /api/login` -> ziskas JWT token
        3. Pre autorizovane endpointy pridaj header: `Authorization: Bearer <token>`
        ### Zariadenia
        - Pridanie zariadenia: `POST /api/add-device` s JSON body `{"device_id": 12345}`
        - Zoznam mojich zariadeni: `GET /api/my-devices`
        - Diagnostika: `GET /api/device/12345/diagnostics`
        ### Komunikacia s RPi
        - Heartbeat: `POST /api/heartbeat` s JSON `{"device_id": 12345}`
        - Trigger prikazu: `POST /api/trigger` s JSON `{"device_id": 12345, "command": "GET_VIN"}`
        - CAN packet: `POST /api/can` s JSON `{"device_id": 12345, "vin": "1HGCM82633A123456"}`
    ''',
    'version': '1.0.0',
    'termsOfService': '',
    'contact': {
        'name': 'Jozef Kusnier',
        'email': '120957@stuba.sk'
    },
    'license': {
        'name': 'MIT'
    },
    'servers': [
        {
            'url': 'https://car-diagnostics.onrender.com',
            'description': 'Render server'
        },
        {
            'url': 'http://localhost:5000',
            'description': 'Local development'
        }
    ],
    'components': {
        'securitySchemes': {
            'bearerAuth': {
                'type': 'http',
                'scheme': 'bearer',
                'bearerFormat': 'JWT'
            }
        }
    }
}

def create_app():
    app = Flask(__name__)
    CORS(app, origins=[
        "https://car-diagnostics-frontend.onrender.com",
        "https://car-diagnostics.onrender.com",
        "http://localhost:5000",
        "http://localhost:3000"
    ])

    @app.before_request
    def ensure_json_content_type():
        if request.method in ['POST', 'PUT', 'PATCH']:
            if request.method == 'OPTIONS':
                return
            if not request.is_json:
                return jsonify({
                    "error": "Content-Type must be application/json",
                    "detail": "Please set Content-Type header to 'application/json'"
                }), 415

    @app.before_request
    def log_request_info():
        print(f"Request: {request.method} {request.path}")
        print(f"Headers: {dict(request.headers)}")
        if request.is_json:
            print(f"JSON: {request.get_json()}")

    @app.after_request
    def after_request(response):
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
        return response

    app.config['SWAGGER'] = SWAGGER_CONFIG
    app.config['SWAGGER_UI_DOC_EXPANSION'] = 'list'
    app.config['SWAGGER_UI_OPERATION_ID'] = True
    app.config['SWAGGER_UI_REQUEST_DURATION'] = True
    app.config['SWAGGER_UI_TRY_IT_OUT'] = False
    app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "your-secret-key")
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)

    db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    jwt.init_app(app)
    Swagger(app, template=app.config['SWAGGER'])
    socketio.init_app(app, cors_allowed_origins="*", async_mode="eventlet")

    from routes.system import bp as system_bp
    from routes.users import bp as users_bp
    from routes.dashboard import bp as dashboard_bp
    from routes.trips import bp as trips_bp
    from routes.driving_events import bp as driving_events_bp
    from routes.vin import bp as vin_bp
    from routes.dtc import bp as dtc_bp
    from routes.devices import bp as devices_bp
    from routes.communication import bp as communication_bp
    from routes.vehicles import bp as vehicles_bp
    from routes.telemetry import bp as telemetry_bp

    app.register_blueprint(system_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(trips_bp)
    app.register_blueprint(driving_events_bp)
    app.register_blueprint(vin_bp)
    app.register_blueprint(dtc_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(communication_bp)
    app.register_blueprint(vehicles_bp)
    app.register_blueprint(telemetry_bp)

    import websocket_events

    return app

app = create_app()

if __name__ == "__main__":
    from models import *
    with app.app_context():
        db.create_all()
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
