#!/usr/bin/env python

import nibabel as nib
import pyvista as pv
import numpy as np
import argparse
import os
import sys
from nibabel.processing import resample_from_to


def _voxel_volume(img):
    """Voxel volume in mm³ — smaller = higher resolution."""
    return float(np.abs(np.linalg.det(img.affine[:3, :3])))


def pick_canonical_template(reference_paths):
    # Select the highest-resolution reference template (smallest voxel size).
    refs = [(p, nib.load(p)) for p in reference_paths]
    refs.sort(key=lambda kv: _voxel_volume(kv[1]))
    path, ref = refs[0]
    print(f"Canonical reference: {path}  (shape={ref.shape}, voxel volume={_voxel_volume(ref):.3g} mm³)")
    return ref


def calculate_mean_density_map(dm_paths, template, group, bundle, output_dir):

    # sum up all the density maps; resample anything off-grid to the template
    print("Calculating probability map...")
    dm_sum = np.zeros(template.shape, dtype=np.float64)
    resampled = 0
    for subject_path in dm_paths:
        img = nib.load(subject_path)
        if img.shape != template.shape:
            img = resample_from_to(img, template, order=1)
            resampled += 1
        dm_sum = dm_sum + img.get_fdata()
    if resampled:
        print(f"Resampled {resampled}/{len(dm_paths)} subject density maps to the canonical grid.")

    # normalize the density map sum
    dm_prob = (dm_sum-np.min(dm_sum))/(np.max(dm_sum)-np.min(dm_sum))

    # Save the average density map as NIFTI
    nifti_path = os.path.join(output_dir, f"{group}_{bundle}_group_mean.nii.gz")
    nib.save(nib.Nifti1Image(dm_prob.astype("float32"), template.affine), nifti_path)
    print(f"Saved probability map: {nifti_path}")


def calculate_iso_surfaces(mean_dm_path, template, group, bundle, percentiles, output):
    # load mean density map
    dm_prob = nib.load(mean_dm_path).get_fdata()

    # create iso-surfaces of the average density map
    print("Calculating iso-surfaces...")
    # create a pyvista volume from the density map
    volume = pv.ImageData(dimensions=dm_prob.shape)
    volume.point_data["values"] = dm_prob.flatten(order='F')

    # create iso-surface at the specified percentile value (ignoring zero values)
    for percentile in percentiles: 
        threshold = np.percentile(dm_prob[dm_prob>0], percentile)
        mesh = volume.contour(isosurfaces=threshold)
        mesh.transform(template.affine, inplace=True) # move to world coords
        mesh.clear_point_data() # remove color data

        # save the iso-surface as a VTK file
        mesh_path=os.path.join(output, f"{group}_{bundle}_iso_surface_{percentile:g}th.vtk") # save as VTK file
        mesh.save(mesh_path, recompute_normals=False)
        print(f"Saved iso-surface: {mesh_path}")

def main():
    parser = argparse.ArgumentParser(description="Create an average tract between participants.")
    parser.add_argument('--density_maps', nargs='+', type=str, help='Density maps.', required=False)
    parser.add_argument('--mean_dm', type=str, help='Mean density map.', required=False)
    parser.add_argument('--group', type=str, help='Name of the group/dataset/cohort.',required=True)
    parser.add_argument('--output', type=str, help='Output path.', default="./")
    parser.add_argument('--bundle', type=str, help='Bundle name.', required=True)
    parser.add_argument('--reference', nargs='+', type=str, required=True,
                        help='One or more MNI reference templates (e.g. 1 mm and 2 mm). ')
    parser.add_argument('--percentiles', nargs='+', type=float, help='List of percentiles for the isosurface.', default=[])
    args = parser.parse_args()

    assert args.density_maps or (args.mean_dm and args.percentiles), "Either --density_maps or both --mean_dm and --percentile must be provided."

    # Canonical reference grid (highest resolution among supplied references).
    # Subjects on a different grid are resampled to it before summing.
    if args.density_maps:
        template = pick_canonical_template(args.reference)
    else:
        # Iso-surface stage: load the mean_dm written by the previous stage,
        # which already lives on the canonical grid.
        template = nib.load(args.mean_dm)

    # Calculate mean density map across subjects and save as NIFTI
    if args.density_maps:
        calculate_mean_density_map(args.density_maps, template, args.group, args.bundle, args.output)

    # Calculate iso-surfaces of the mean density map and save as VTK files
    if args.percentiles and args.mean_dm:
        calculate_iso_surfaces(args.mean_dm, template, args.group, args.bundle, args.percentiles, args.output)

if __name__ == "__main__":
    main()