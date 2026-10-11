"""MongoDB Atlas connection helper for the longview database."""
import os

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

DB_NAME = "longview"

_client = None


def get_db():
    """Return the longview database, creating one shared client on first use."""
    global _client
    if _client is None:
        uri = os.environ.get("MONGODB_URI")
        if not uri:
            raise RuntimeError("MONGODB_URI is not set (check your .env file)")
        _client = MongoClient(uri, serverSelectionTimeoutMS=10000)
        _client.admin.command("ping")  # fail fast if Atlas is unreachable
    return _client[DB_NAME]
