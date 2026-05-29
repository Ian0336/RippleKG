"""Create the ArangoDB schema (8 collections + indexes). Idempotent."""
from ripplekg.db.client import get_db
from ripplekg.db.schema import init_schema

if __name__ == "__main__":
    db = get_db()
    init_schema(db)
    print("schema initialized:", sorted(c["name"] for c in db.collections() if not c["name"].startswith("_")))
