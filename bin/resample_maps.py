#!/usr/bin/env python3

import sys
import logging
import argparse
import numpy as np
import nibabel as nib
from nibabel.processing import resample_from_to

logging.basicConfig(stream=sys.stdout, format='%(asctime)s,%(msecs)d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S', encoding='utf-8', level=logging.INFO, force=True)


def resample_maps(maps, reference, outputs):
    """Save each subject-space map on the voxel grid of reference, resampling through world (RAS mm) coordinates only when the grids differ."""
    ref_img = nib.load(reference)
    for map_path, output in zip(maps, outputs):
        img = nib.load(map_path)
        vox2vox = np.linalg.inv(img.affine) @ ref_img.affine
        if img.shape[:3] != ref_img.shape[:3] or not np.allclose(vox2vox, np.eye(4)):
            order = 0 if np.allclose(vox2vox, np.round(vox2vox), atol=1e-3) else 1
            logging.info(f"Resampling {map_path} {img.shape} onto the {reference} grid {ref_img.shape} with order {order}")
            img = resample_from_to(img, ref_img, order=order)
        nib.save(img, output)
        logging.info(f"Saved {output}")


def main():
    parser = argparse.ArgumentParser(description="Resample subject-space metric maps (e.g. NODDI) onto the DSI-Studio GQI voxel grid.")
    parser.add_argument("--maps", nargs="+", required=True, help="Subject-space NIfTI metric maps.")
    parser.add_argument("--reference", required=True, help="DSI-Studio GQI map whose voxel grid defines the output space.")
    parser.add_argument("--outputs", nargs="+", required=True, help="Output NIfTI paths, one per map.")
    args = parser.parse_args()
    if len(args.maps) != len(args.outputs):
        parser.error("--maps and --outputs must have the same length.")
    resample_maps(args.maps, args.reference, args.outputs)


if __name__ == "__main__":
    main()
