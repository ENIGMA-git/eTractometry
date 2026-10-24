#!/usr/bin/env python

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

STAGES: dict[str, str] = {
    "segment":        "tracts/{bundle}/{subject}_{bundle}.done",
    "bundle_metrics": "density_maps/{bundle}/{subject}_{bundle}.done",
    "meta":           "MeTA/{bundle}/{subject}_{bundle}.done",
    "buan":           "BUAN/{bundle}/{subject}_{bundle}.done",
    "render_qc":      "qc_images/tracts/{bundle}/{subject}_{bundle}.done",
}

STAGE_COL_W   = 18
COUNT_COL_W   = 8
PCT_COL_W     = 7
BUNDLE_COL_W  = 60
SUBJECT_COL_W = 30


def read_subjects(path: str) -> list[str]:
    # Read subject ids
    p = Path(path)
    if not p.is_file():
        sys.exit(f"ERROR: --subjects file not found: {p}")
    text = p.read_text()
    header = text.splitlines()[0] if text else ""
    if "," in header and "subject" in header.lower():
        with p.open() as f:
            return [
                row["subject"].strip()
                for row in csv.DictReader(f)
                if row.get("subject", "").strip()
            ]
    return [token for token in re.split(r"[\s,]+", text) if token]


def read_bundles(path: str) -> list[str]:
    p = Path(path)
    if not p.is_file():
        sys.exit(f"ERROR: --bundles file not found: {p}")
    return [line.strip() for line in p.read_text().splitlines() if line.strip()]


def build_table(subjects_dir: Path, subjects: list[str], bundles: list[str]) -> list[dict]:
    rows = []
    for subject in subjects:
        subject_dir = subjects_dir / subject
        for bundle in bundles:
            row = {"subject": subject, "bundle": bundle}
            for stage, fmt in STAGES.items():
                marker = subject_dir / fmt.format(subject=subject, bundle=bundle)
                row[stage] = 1 if marker.is_file() else 0
            rows.append(row)
    return rows


def _format_count(done: int, total: int) -> str:
    pct = 100.0 * done / total if total else 0.0
    return f"{done}/{total} ({pct:.1f}%)"


def _fewest(
    counts: Counter, total_per_item: int, top_n: int
) -> list[tuple[str, int]]:
    below = [(item, n) for item, n in counts.items() if n < total_per_item]
    below.sort(key=lambda kv: (kv[1], kv[0]))
    return below[:top_n]


def format_summary(
    rows: list[dict], subjects: list[str], bundles: list[str], top_n: int = 10
) -> list[str]:
    n_subj, n_bun = len(subjects), len(bundles)
    expected = n_subj * n_bun
    lines: list[str] = []

    lines.append("")
    lines.append(
        f"Cohort:  {n_subj} subjects × {n_bun} bundles "
        f"= {expected} expected (subject, bundle) pairs per stage"
    )
    lines.append("")
    lines.append(
        f"{'Stage':<{STAGE_COL_W}}  {'Done':>{COUNT_COL_W}}  "
        f"{'Missing':>{COUNT_COL_W}}  {'%':>{PCT_COL_W}}"
    )
    lines.append("-" * (STAGE_COL_W + 2 * COUNT_COL_W + PCT_COL_W + 6))
    for stage in STAGES:
        done = sum(row[stage] for row in rows)
        missing = expected - done
        pct = 100.0 * done / expected if expected else 0.0
        lines.append(
            f"{stage:<{STAGE_COL_W}}  {done:>{COUNT_COL_W}}  "
            f"{missing:>{COUNT_COL_W}}  {pct:>{PCT_COL_W - 1}.1f}%"
        )

    lines.append("")
    lines.append(f"Bundles with the fewest completions (top {top_n}, only those below 100%):")
    for stage in STAGES:
        counts = Counter({b: 0 for b in bundles})
        counts.update(row["bundle"] for row in rows if row[stage])
        worst = _fewest(counts, n_subj, top_n)
        if worst:
            lines.append(f"  {stage}:")
            for bundle, done in worst:
                lines.append(f"    {bundle:<{BUNDLE_COL_W}} {_format_count(done, n_subj)}")

    lines.append("")
    lines.append(f"Subjects with the fewest completions (top {top_n}, only those below 100%):")
    for stage in STAGES:
        counts = Counter({s: 0 for s in subjects})
        counts.update(row["subject"] for row in rows if row[stage])
        worst = _fewest(counts, n_bun, top_n)
        if worst:
            lines.append(f"  {stage}:")
            for subject, done in worst:
                lines.append(f"    {subject:<{SUBJECT_COL_W}} {_format_count(done, n_bun)}")

    return lines


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["subject", "bundle", *STAGES])
        writer.writeheader()
        writer.writerows(rows)
    print(f"WROTE {path}  ({len(rows)} rows)")


def write_summary(lines: Iterable[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    print(f"WROTE {path}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--output_dir", required=True,
                    help="Pipeline output_dir (parent of subjects/, spreadsheets/, …).")
    ap.add_argument("--subjects", required=True,
                    help="Path to subjects.txt OR to the pipeline input CSV "
                        "(e.g. spreadsheets/<dataset>_input_data.csv).")
    ap.add_argument("--bundles", required=True,
                    help="Path to bundle list (e.g. atlas/sparse/human.txt).")
    ap.add_argument("--dataset", required=True,
                    help="Dataset name, used to prefix the default output filenames.")
    ap.add_argument("--output_csv", default=None,
                    help="Output CSV path. Defaults to "
                        "<output_dir>/spreadsheets/<dataset>_run_completeness.csv.")
    ap.add_argument("--output_txt", default=None,
                    help="Output TXT summary path. Defaults to the CSV path with `.txt`.")
    ap.add_argument("--top_n", type=int, default=10,
                    help="Number of worst items to list per stage (default: 10).")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    out_dir = Path(args.output_dir).resolve()
    subjects_dir = out_dir / "subjects"
    if not subjects_dir.is_dir():
        sys.exit(f"ERROR: {subjects_dir} not found — has the pipeline run?")

    subjects = read_subjects(args.subjects)
    bundles = read_bundles(args.bundles)
    if not subjects:
        sys.exit("ERROR: subjects list is empty.")
    if not bundles:
        sys.exit("ERROR: bundles list is empty.")

    rows = build_table(subjects_dir, subjects, bundles)

    csv_path = Path(args.output_csv) if args.output_csv \
        else out_dir / "spreadsheets" / f"{args.dataset}_run_completeness.csv"
    write_csv(rows, csv_path)

    summary_lines = format_summary(rows, subjects, bundles, top_n=args.top_n)
    for line in summary_lines:
        print(line)

    txt_path = Path(args.output_txt) if args.output_txt else csv_path.with_suffix(".txt")
    write_summary(summary_lines, txt_path)


if __name__ == "__main__":
    main()
