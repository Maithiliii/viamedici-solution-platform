"""Creates a demo case (data/dataset/demo_cases.yaml) through the Directus API as a demo engineer.

Going through Directus (not SQL) means the 'Analyse new case' Flow fires exactly as in real use.
    python -m sep.cli.demo demo-1 [--as lena.hoffmann@sep.example.com]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import get_settings
from ..db import fetch_one
from ..directus import Directus
from .bootstrap_directus import DEMO_PASSWORD
from .seed import load_dataset


def create(demo_id: str, email: str) -> dict:
    s = get_settings()
    data = load_dataset(Path(s.data_dir))
    demo = next(c for c in data["demo_cases"] if c["id"] == demo_id)
    customer = next(c for c in data["taxonomy"]["customers"] if c["key"] == demo["customer"])
    env = fetch_one(
        """SELECT e.id, r.version FROM environments e JOIN customers c ON c.id = e.customer_id
           JOIN releases r ON r.id = e.release_id WHERE c.name = %s""", (customer["name"],))
    d = Directus.login(s.directus_url, email, DEMO_PASSWORD)
    me = d.get("/users/me", fields="id")
    return d.post("/items/cases", {
        "title": demo["title"], "customer_report": demo["report"].strip(), "environment_id": str(env["id"]),
        "epim_version": env["version"], "severity": demo.get("severity", "S2"), "channel": "portal",
        "status": "new", "assigned_to": me["id"]})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("demo_id")
    parser.add_argument("--as", dest="email", default="lena.hoffmann@sep.example.com")
    args = parser.parse_args()
    case = create(args.demo_id, args.email)
    print(json.dumps({"id": case["id"], "case_number": case.get("case_number")}, indent=2))


if __name__ == "__main__":
    main()
