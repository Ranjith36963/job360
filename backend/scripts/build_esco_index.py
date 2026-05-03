#!/usr/bin/env python3
"""Build the ESCO embedding index used by ``services.profile.skill_normalizer``.

---------------------------------------------------------------------
ESCO data attribution (plan §8 risk-table row 1 — CC BY 4.0)

The European Skills, Competences, Qualifications and Occupations
(ESCO) classification is © European Union, 2024, released under the
Creative Commons Attribution 4.0 International licence (CC BY 4.0).
See https://esco.ec.europa.eu/en/about-esco/data-science-and-esco.

The ``labels.json`` + ``embeddings.npy`` artefacts this script
produces contain data DERIVED FROM ESCO v1.2.1. Anyone redistributing
those artefacts (Docker image, release tarball, fork) MUST preserve
this attribution. Job360's LICENSE / README carry the same line.
---------------------------------------------------------------------

Run once (or when the ESCO CSV is refreshed) to produce two artefacts:

    backend/data/esco/labels.json       (list[{uri,label,alt_labels}])
    backend/data/esco/embeddings.npy    (float32 matrix, L2-normalised)

Usage:
    python scripts/build_esco_index.py --esco-csv PATH/TO/skills_en.csv

The ESCO v1.2.1 English skills CSV is downloadable from:
    https://esco.ec.europa.eu/en/use-esco/download

This script is NOT run by CI or tests — it's a developer workflow
step. Runtime only reads the artefacts via ``numpy.load``, which is
fast.

The CSV schema we expect (ESCO v1.2.1 ``skills_en.csv`` columns):

    conceptType       (filter to "KnowledgeSkillCompetence")
    conceptUri        → stored as "uri"
    preferredLabel    → stored as "label"
    altLabels         → stored as "alt_labels" (newline-split)
    status            (filter to "released")

Model: ``sentence-transformers/all-MiniLM-L6-v2`` — 384 dims, 23MB
weights, ~10s to embed 13,900 skills on CPU.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "backend" / "data" / "esco"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--esco-csv", type=Path, required=True, help="Path to skills_en.csv")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--model",
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="Sentence-transformers model id",
    )
    args = parser.parse_args()

    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        print(
            f"Missing dependency: {e}. Install the optional extra:\n"
            f"    pip install '.[esco]'",
            file=sys.stderr,
        )
        return 2

    if not args.esco_csv.exists():
        print(f"ESCO CSV not found at {args.esco_csv}", file=sys.stderr)
        return 2

    print(f"Reading {args.esco_csv} …", flush=True)
    labels: list[dict] = []
    with args.esco_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("conceptType") and "Skill" not in row["conceptType"]:
                continue
            if row.get("status") and row["status"] != "released":
                continue
            uri = row.get("conceptUri") or row.get("uri")
            label = row.get("preferredLabel") or row.get("label")
            if not uri or not label:
                continue
            raw_alts = row.get("altLabels") or row.get("alt_labels") or ""
            alt_labels = [a.strip() for a in raw_alts.replace("\r", "\n").split("\n") if a.strip()]
            labels.append({"uri": uri, "label": label, "alt_labels": alt_labels})

    if not labels:
        print("Parsed 0 skills from the CSV — schema mismatch?", file=sys.stderr)
        return 2
    print(f"Parsed {len(labels)} ESCO skills.", flush=True)

    print(f"Loading encoder {args.model} …", flush=True)
    encoder = SentenceTransformer(args.model)

    # Compose the text to embed: preferredLabel + top-2 alt labels.
    # Multiple labels per skill pull the embedding toward the concept
    # rather than one surface form, which improves cosine match recall
    # on paraphrased user input.
    def _compose(row: dict) -> str:
        parts = [row["label"]]
        for alt in row["alt_labels"][:2]:
            if alt and alt != row["label"]:
                parts.append(alt)
        return " / ".join(parts)

    texts = [_compose(r) for r in labels]
    print("Encoding …", flush=True)
    embeddings = encoder.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    embeddings = np.asarray(embeddings, dtype="float32")
    print(f"Embeddings shape: {embeddings.shape}", flush=True)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    labels_out = args.out_dir / "labels.json"
    emb_out = args.out_dir / "embeddings.npy"
    with labels_out.open("w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=2)
    np.save(emb_out, embeddings)

    print(f"Wrote {labels_out}", flush=True)
    print(f"Wrote {emb_out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
