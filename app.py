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

    # ------------------- INIT -------------------
    db.init_app(app)
    jwt.init_app(app)

    # ------------------- REGISTER ROUTES -------------------
    from routes.auth_routes import auth_bp
    from routes.device_routes import device_bp
    from routes.vin_routes import vin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(device_bp)
    app.register_blueprint(vin_bp)

    # ------------------- BASIC ROUTE -------------------
    @app.route("/")
    def home():
        return jsonify({"status": "ok", "message": "Flask beží modularne"})

    with app.app_context():
        db.create_all()

    return app


# ------------------- RUN SERVER -------------------
if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
