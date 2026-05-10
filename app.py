import os
from datetime import timedelta

# Eventlet je volitelny. Bez tejto ochrany aplikacia spadne hned pri importe,
# ak lokalne alebo na hostingu nie je nainstalovany/kompatibilny eventlet.
try:
    import eventlet
    eventlet.monkey_patch()
    SOCKETIO_ASYNC_MODE = "eventlet"
except Exception:
    SOCKETIO_ASYNC_MODE = "threading"

from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from flasgger import Swagger
from extensions import db, jwt, socketio

# AI: Swagger template aplikacie bol vygenerovany pomocou ChatGPT a nasledne upraveny autorom.
# Swagger nastavenie je drzana ako samostatna konstanta, aby sa nemiesala s inicializaciou Flasku.
SWAGGER_TEMPLATE = {
    "swagger": "2.0",
    "info": {
        "title": "Inteligentna diagnostika API",
        "version": "1.0.0",
        "description": """
API pre bakalarsku pracu - diagnostika vozidiel.

## Testovanie cez Postman
1. Zaregistruj sa: `POST /api/register`
2. Prihlas sa: `POST /api/login` a ziskas JWT token
3. Pre autorizovane endpointy pridaj header: `Authorization: Bearer <token>`

Swagger UI je dostupne na `/apidocs`.
        """,
        "contact": {
            "name": "Jozef Kusnier",
            "email": "120957@stuba.sk"
        },
        "license": {"name": "MIT"}
    },
    "servers": [
        {"url": "https://car-diagnostics.onrender.com", "description": "Render server"},
        {"url": "http://localhost:5000", "description": "Local development"}
    ],
    "components": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT"
            }
        }
    }
}

# AI: Swagger konfiguracia aplikacie bola vygenerovana pomocou ChatGPT a nasledne upravena autorom.
# Konfiguracia urcuje najma cestu k JSON specifikacii a zapnutie Swagger UI.
SWAGGER_CONFIG = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec_1",
            "route": "/apispec_1.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/apidocs/",
    "title": "Inteligentna diagnostika API",
    "uiversion": 3,
}

# Vytvorenie celej Flask aplikacie je oddelene do funkcie, aby sa dala rovnako pouzit lokalne aj na hostingu.
def create_app():
    app = Flask(__name__)
    # CORS povoluje iba frontend a lokalne adresy, z ktorych sa aplikacia realne vola.
    CORS(app, origins=[
        "https://car-diagnostics-frontend.onrender.com",
        "https://car-diagnostics.onrender.com",
        "http://localhost:5000",
        "http://localhost:3000"
    ])

    # Pred kazdym requestom sa zachyti najcastejsia chyba klienta - zly Content-Type pri JSON endpointoch.
    @app.before_request
    # Pred spracovanim requestu kontrolujem JSON, aby backend nemusel riesit necakane formaty vstupu.
    def ensure_json_content_type():
        # Kontrola sa robi len pri metodach, ktore typicky posielaju telo requestu.
        if request.method in ['POST', 'PUT', 'PATCH']:
            # Swagger stranky musia prejst bez tejto validacie, inak by sa UI zbytocne blokovalo.
            if request.path.startswith('/apidocs') or request.path.startswith('/apispec'):
                return
            # Ak klient neposle JSON, endpoint by neskor spadol na get_json alebo vratil necitatelnu chybu.
            if not request.is_json:
                return jsonify({
                    "error": "Content-Type must be application/json",
                    "detail": "Please set Content-Type header to 'application/json'"
                }), 415

    @app.before_request
    # Jednoduche logovanie requestov pomaha hlavne pri ladeni komunikacie s frontend/RPi castou.
    def log_request_info():
        print(f"Request: {request.method} {request.path}")
        print(f"Headers: {dict(request.headers)}")
        # Pri JSON requestoch sa explicitne vracia JSON content type aj v odpovedi.
        if request.is_json:
            print(f"JSON: {request.get_json()}")

    # Po requeste sa doplnaju hlavicky, ktore pomahaju hlavne pri volani z weboveho frontendu.
    @app.after_request
    # CORS hlavicky sa doplnaju aj po spracovani requestu, aby ich mali vsetky odpovede rovnako.
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

    # Render niekedy poskytne starsi tvar postgres URL, preto ho upravujem na driver pouzity v requirements.
    db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
    # Render vie vratit starsi postgres prefix, SQLAlchemy vsak ocakava postgresql.
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    # Databazova URL sa berie z prostredia, aby rovnaky kod fungoval lokalne aj na Renderi.
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Inicializacia rozsireni prebieha az po nastaveni konfiguracie aplikacie.
    # Rozsirenia sa pripajaju az tu, ked uz existuje konkretna Flask instancia.
    db.init_app(app)
    jwt.init_app(app)
    Swagger(app, template=SWAGGER_TEMPLATE, config=SWAGGER_CONFIG)
    socketio.init_app(app, cors_allowed_origins="*", async_mode=SOCKETIO_ASYNC_MODE)

    from routes.system import bp as system_bp
    # Blueprinty rozdeluju vacsi backend na mensie tematicke casti.
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

    # Kazda oblast API je oddelena do vlastneho blueprintu, aby app.py neobsahoval vsetky endpointy naraz.
    app.register_blueprint(system_bp)
    # Vsetky API blueprinty maju spolocny prefix /api, aby boli oddelene od pomocnych stranok.
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

    @app.get('/swagger')
    @app.get('/docs')
    @app.get('/api/docs')
    # Kratsie aliasy iba presmeruju pouzivatela na hlavnu Swagger UI adresu.
    def swagger_alias():
        return redirect('/apidocs/')

    # Pri spusteni cez gunicorn sa blok __main__ nevykona, preto tabulky
    # vytvorime aj pri importe aplikacie. Na produkcii sa to da vypnut
    # premennou AUTO_CREATE_TABLES=false.
    # Automaticke vytvorenie tabuliek je prakticke pri deployi, ale da sa vypnut cez env premennu.
    if os.environ.get('AUTO_CREATE_TABLES', 'true').lower() == 'true':
        with app.app_context():
            db.create_all()

    # Funkcia vracia uz kompletne poskladanu aplikaciu pripravenu pre WSGI/SocketIO server.
    return app

app = create_app()

if __name__ == "__main__":
    from models import *
    with app.app_context():
        db.create_all()
    # Lokalny start pouziva SocketIO runner, aby fungovali aj websocket endpointy.
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# --- Suhrn vyuzitia AI ---
# AI: V tomto subore bol pomocou ChatGPT vygenerovany a nasledne autorom upraveny Swagger template a Swagger konfiguracia aplikacie.
