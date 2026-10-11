"""MongoDB Atlas connection helper. Database name comes from LONGVIEW_DB (default: longview)."""
import os

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

DB_NAME = os.environ.get("LONGVIEW_DB", "longview")

_client = None


def get_db(name=None):
    """Return the configured database (or `name`), creating one shared client on first use."""
    global _client
    if _client is None:
        uri = os.environ.get("MONGODB_URI")
        if not uri:
            raise RuntimeError("MONGODB_URI is not set (check your .env file)")
        _client = MongoClient(uri, serverSelectionTimeoutMS=10000)
        _client.admin.command("ping")  # fail fast if Atlas is unreachable
    return _client[name or DB_NAME]
