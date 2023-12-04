import os

MONGO_HOST = os.environ.get("MONGO_HOST")
MONGO_TLS = bool(os.environ.get("MONGO_TLS"))
MONGO_USER = os.environ.get("MONGO_USER")
MONGO_PASSWORD = os.environ.get("MONGO_PASSWORD")
