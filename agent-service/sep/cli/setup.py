"""One-shot setup: Directus bootstrap -> seed dataset -> embeddings -> search index.

Runs automatically as the `setup` service of docker compose. Idempotent.
    python -m sep.cli.setup [--force-seed] [--skip-index]
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .. import indexer
from ..config import get_settings
from ..embeddings import get_embeddings
from . import bootstrap_directus, seed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-seed", action="store_true")
    parser.add_argument("--skip-index", action="store_true")
    args = parser.parse_args()

    s = get_settings()
    team = seed.load_dataset(Path(s.data_dir))["taxonomy"]["users"]
    bootstrap_directus.run(team)
    result = {"seed": seed.seed(force=args.force_seed)}
    if not args.skip_index:
        emb = get_embeddings()
        logging.getLogger("sep.setup").info("Ensuring embedding model %s is available", emb.model)
        emb.pull_model()
        result["index"] = indexer.rebuild_all()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
