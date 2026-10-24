#!/usr/bin/env python
import re
import sys
import argparse
import pandas as pd
from pathlib import Path
from collections import defaultdict

DEFAULT_METRICS = ["dti_fa", "md", "ad", "rd", "qa", "gfa", "iso", "rdi", "qir", "rd1", "rd2", "nrdi02L", "nrdi04L"]

def parse_meta_profile(stem, metrics):
    # <subject>_<bundle>_<metric>_segments_average_local_<roi>
    m = re.match(r"^(.+)_segments_average_local_(all|core)$", stem)
    if not m:
        return None
    for metric in sorted(metrics, key=len, reverse=True):
        if m.group(1).endswith("_" + metric):
            return {"metric": metric, "roi": m.group(2)}
    return None


def parse_buan_profile(stem, metrics):
    # <subject>_<bundle>_<metric>
    for metric in sorted(metrics, key=len, reverse=True):
        if stem.endswith("_" + metric):
            return {"metric": metric}
    return None


def parse_shape(stem):
    # Filename: <subject>_<bundle>_shape_metrics
    return {} if stem.endswith("_shape_metrics") else None


def parse_similarity(stem):
    # <subject>_<bundle>_similarity<percentile>
    m = re.search(r"_similarity(\d{1,3})$", stem)
    if not m:
        return None
    pct = int(m.group(1))
    if not 0 <= pct <= 100:
        return None
    return {"percentile": pct}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", required=True, choices=["meta_profile", "shape", "buan_profile", "similarity"])
    ap.add_argument("--inputs", required=True, help="Path to a text file with one input CSV path per line.")
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--metrics", default=",".join(DEFAULT_METRICS), help="Comma-separated metric to recognise in filenames.")
    ap.add_argument("--prefix", default="", help="Optional prefix prepended to every output filename " "(e.g. dataset name).")
    args = ap.parse_args()

    with open(args.inputs) as f:
        all_inputs = [line.strip() for line in f if line.strip()]
    if not all_inputs:
        sys.exit(f"ERROR: no inputs found in {args.inputs}.")

    metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{args.prefix}_" if args.prefix else ""

    # inputs by (metric, roi) / (metric,) / () depending on mode.
    buckets = defaultdict(list)
    for path in all_inputs:
        p = Path(path)
        stem = p.stem
        if args.mode == "meta_profile":
            info = parse_meta_profile(stem, metrics)
            key = (info["metric"], info["roi"]) if info else None
        elif args.mode == "buan_profile":
            info = parse_buan_profile(stem, metrics)
            key = (info["metric"],) if info else None
        elif args.mode == "shape":
            info = parse_shape(stem)
            key = () if info is not None else None
        else:  # similarity
            info = parse_similarity(stem)
            key = (info["percentile"],) if info else None

        if key is None:
            print(f"SKIP unparsable filename: {p.name}", file=sys.stderr)
            continue
        buckets[key].append(p)

    if not buckets:
        print("ERROR: no input files matched the requested mode.", file=sys.stderr)
        sys.exit(1)

    # Concatenate and sort by (subjectID, bundle, segment)
    for key, paths in sorted(buckets.items()):
        combined = pd.concat([pd.read_csv(p) for p in sorted(paths)], ignore_index=True)
        sort_cols = [c for c in ("subjectID", "bundle", "segment") if c in combined.columns]
        if sort_cols:
            combined = combined.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)

        if args.mode == "meta_profile":
            metric, roi = key
            out = out_dir / f"{prefix}MeTA_{metric}_local_{roi}.csv"
        elif args.mode == "buan_profile":
            (metric,) = key
            out = out_dir / f"{prefix}BUAN_{metric}.csv"
        elif args.mode == "shape":
            out = out_dir / f"{prefix}shape_metrics.csv"
        else:  # similarity
            (pct,) = key
            out = out_dir / f"{prefix}similarity{pct}.csv"

        combined.to_csv(out, index=False)
        print(f"WROTE {out} ({len(paths)} input files)")


if __name__ == "__main__":
    main()
