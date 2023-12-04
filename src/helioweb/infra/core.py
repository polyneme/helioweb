from pymongo import MongoClient

from helioweb.infra.config import MONGO_HOST, MONGO_TLS, MONGO_USER, MONGO_PASSWORD


def get_mongodb():
    client = MongoClient(
        host=MONGO_HOST, username=MONGO_USER, password=MONGO_PASSWORD, tls=MONGO_TLS
    )
    mdb = client.helioweb
    return mdb
