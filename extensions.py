from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_socketio import SocketIO

# Rozsirenia sa vytvaraju bez konkretnej aplikacie a pripajaju sa az v create_app().
# SQLAlchemy objekt sa pouziva v modeloch aj routach, preto je definovany centralne.
db = SQLAlchemy()
# JWT manager riesi prihlasovacie tokeny a ochranu endpointov cez @jwt_required.
jwt = JWTManager()
# SocketIO je spolocna instancia pre HTTP aplikaciu aj websocket eventy.
socketio = SocketIO()
