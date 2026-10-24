#!/usr/bin/env python3

import os
import re
import sys
import argparse
import pandas as pd
from glob import glob
import nibabel as nib


def resample_to(dwi_path):
    # Return target voxel size: 2mm if too large, min in-plane if anisotropic, else 0.
    zooms = nib.load(dwi_path).header.get_zooms()[:3]
    zmin, zmax = round(min(zooms), 1), round(max(zooms), 1)
    if zmin > 2.1:
        return 2
    if round(zmax - zmin, 1) > 0.1:
        return zmin
    return 0


def one_match(pattern, subject, label):
    matches = [m for m in glob(pattern.format(subject=subject)) if os.path.exists(m)]
    assert len(matches) <= 1, (
        f"Expected one {label} file for subject {subject}, found: {matches}. "
        f"Please use a more specific --{label.lower()}_format pattern."
    )
    return matches[0] if matches else None


parser = argparse.ArgumentParser(description="Collect dMRI input file paths for a list of subjects.")
parser.add_argument("--subject_IDs", required=True, help="Text file with subject IDs.")
parser.add_argument("--dwi_format", required=True, help="DWI file pattern (use {subject}).")
parser.add_argument("--mask_format", default=None, help="Mask file pattern. Optional for .sz input.")
parser.add_argument("--bvec_format", default=None, help="Bvec file pattern. Required for NIfTI, ignored for .sz.")
parser.add_argument("--bval_format", default=None, help="Bval file pattern. Required for NIfTI, ignored for .sz.")
parser.add_argument("--extra_maps_format", nargs="+", default=[], help="Extra subject-space metric map patterns as name=pattern pairs (use {subject}).")
parser.add_argument("--output", required=True, help="Output CSV path.")
args = parser.parse_args()

with open(args.subject_IDs) as f:
    subjects = [s for s in re.split(r"[, \n\t]+", f.read()) if s]

rows = []
for subject in subjects:
    row = {
        "subject": subject,
        "dwi": one_match(args.dwi_format, subject, "DWI"),
        "mask": None,
        "bvec": None,
        "bval": None,
        "resample_to": None,
    }

    # `.sz` (dsi_studio source) bundles bvec/bval internally and is treated as
    # pre-resampled; mask is also optional (dsi_studio generates one if missing).
    is_sz = row["dwi"] is not None and row["dwi"].endswith(".sz")

    if args.mask_format:
        row["mask"] = one_match(args.mask_format, subject, "MASK")
        if row["mask"] is None and is_sz:
            row["mask"] = ""
    elif is_sz:
        row["mask"] = ""

    if is_sz:
        row.update({"bvec": "", "bval": "", "resample_to": 0})
    else:
        if args.bvec_format:
            row["bvec"] = one_match(args.bvec_format, subject, "BVEC")
        if args.bval_format:
            row["bval"] = one_match(args.bval_format, subject, "BVAL")
        if row["dwi"]:
            row["resample_to"] = resample_to(row["dwi"])

    for name, pattern in (m.split("=", 1) for m in args.extra_maps_format):
        row[f"map_{name}"] = one_match(pattern, subject, "EXTRA_MAPS")

    rows.append(row)

df = pd.DataFrame(rows)
missing_df  = df[df.isna().any(axis=1)]
# Extra maps are optional: subjects missing one are profiled without it.
complete_df = df.dropna(subset=["dwi", "mask", "bvec", "bval", "resample_to"])

missing_output = f"{os.path.splitext(args.output)[0]}_missing.csv"
missing_df.to_csv(missing_output, index=False)
complete_df.to_csv(args.output, index=False)

print(f"Total subjects in {args.subject_IDs}: {len(subjects)}")
print(f"  with all required inputs: {len(complete_df)}  -> {args.output}")
print(f"  with missing inputs:      {len(missing_df)}  -> {missing_output}")
for column, n in df.drop(columns="resample_to").isna().sum().items():
    if n:
        print(f"    missing {column}: {n} ({'profiled without this map' if column.startswith('map_') else 'skipped'})")
if complete_df.empty:
    sys.exit("No subject has all required input files.")
