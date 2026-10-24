#!/usr/bin/env python

from nibabel.processing import resample_from_to
import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
import argparse


def get_slice_indices(size, num_slices):
    """
    Return evenly spaced slice indices centered in the volume.
    """
    if num_slices == 1:
        return [np.round(size / 1.9).astype(int)]

    fractions = np.linspace(0.4, 0.6, num_slices)
    return [int(np.clip(round((size - 1) * frac), 0, size - 1)) for frac in fractions]


def plot_3views(mri, output, atlas=None, alpha=0.5, num_slices=3):
    """
    Show sagittal, coronal, and axial MRI slices with atlas overlay.
    Assumes mri and atlas have shape (X, Y, Z).
    """
    atlas = np.zeros_like(mri) if atlas is None else atlas

    x, y, z = mri.shape
    xs = get_slice_indices(x, num_slices)
    ys = get_slice_indices(y, num_slices)
    zs = get_slice_indices(z, num_slices)

    views = [
        ("Sagittal", [np.rot90(mri[i, :, :]) for i in xs], [np.rot90(atlas[i, :, :]) for i in xs]),
        ("Coronal",  [np.rot90(mri[:, j, :]) for j in ys], [np.rot90(atlas[:, j, :]) for j in ys]),
        ("Axial",    [np.rot90(mri[:, :, k]) for k in zs], [np.rot90(atlas[:, :, k]) for k in zs]),
    ]

    plt.style.use('dark_background')
    fig, axes = plt.subplots(num_slices, 3, figsize=(12, 4 * num_slices), squeeze=False)

    for col, (name, imgs, labs) in enumerate(views):
        for row, (img, lab) in enumerate(zip(imgs, labs)):
            ax = axes[row, col]
            ax.imshow(img, cmap="gray")
            ax.imshow(np.ma.masked_where(lab == 0, lab), cmap="Wistia", alpha=alpha)
            ax.axis("off")
            if name in ("Coronal", "Axial"):
                # assuming RAS
                ax.text(0.05, 0.95, "L", color="white", fontsize=12,
                        transform=ax.transAxes, ha="left", va="top")
                ax.text(0.95, 0.95, "R", color="white", fontsize=12,
                        transform=ax.transAxes, ha="right", va="top")

    # remove ALL spacing between subplots
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0, wspace=0, hspace=0)
    plt.savefig(output, bbox_inches="tight", pad_inches=0)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Registration QC image.")
    parser.add_argument("--image", type=str, help="3D MRI NIFTI file.", required=True)
    parser.add_argument("--atlas", type=str, help="Atlas NIFTI file.", default=None)
    parser.add_argument("--output_file", type=str, help="Output file PNG.", required=True)
    parser.add_argument("--num_slices", type=int, default=1,
                        help="Number of slices to render for each anatomical axis.")
    args = parser.parse_args()
    if args.num_slices < 1:
        parser.error("--num_slices must be at least 1")

    print("Loading MRI ...")
    mri_img = nib.as_closest_canonical(nib.load(args.image)) # load in RAS

    atlas_mask = args.atlas
    if atlas_mask is not None:
        print("Loading atlas ...")
        atlas_img = nib.as_closest_canonical(nib.load(args.atlas)) # load in RAS
        atlas = resample_from_to(atlas_img, mri_img, order=0).get_fdata() # resample atlas affine to mri
        atlas_mask = (atlas > 0).astype(int)
    print("Rendering figure ...")
    plot_3views(mri_img.get_fdata(), args.output_file, atlas=atlas_mask, num_slices=args.num_slices)

    print(f"Image saved: {args.output_file}")

if __name__ == "__main__":
    main()
