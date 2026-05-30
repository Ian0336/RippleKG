"""Load the T0 graph from a Re-DocRED file.

Usage: python scripts/ingest_t0.py [path] [limit]
  path  default data/docred/dev_revised.json
  limit default 10 (number of documents -> doc0..doc{limit-1})
"""
import sys

from ripplekg.db.client import get_db
from ripplekg.ingest.loader import ingest_dataset

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/docred/dev_revised.json"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    db = get_db()
    n = ingest_dataset(db, path, limit=limit)
    print(f"ingested {n} documents from {path} (limit={limit})")
