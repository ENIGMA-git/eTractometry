#!/usr/bin/env python
"""
Computes a distance-weighted mean scalar profile along a bundle using the BUAN weighted-mean method.
"""

import argparse
import csv
import logging
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from scipy.interpolate import splprep, splev
from scipy.spatial import cKDTree
from scipy.spatial import ConvexHull, QhullError

from dipy.io.image import load_nifti
from dipy.tracking.utils import length
from dipy.tracking.streamline import (
    set_number_of_points,
    Streamlines,
    values_from_volume,
)
from dipy.segment.metric import AveragePointwiseEuclideanMetric
from dipy.segment.clustering import QuickBundles

from nibabel.streamlines.array_sequence import ArraySequence
from meta.io.streamline import read_streamlines
from meta.transforms.tractogram import transform_tractogram


# ---------------------------------------------------------------------------
# Centroid utilities
# ---------------------------------------------------------------------------

def _get_initial_centroid(bundle, n_points: int = 100, thresh: float = 100.0) -> np.ndarray:
    """
    Compute a QuickBundles centroid for the atlas bundle.

    Parameters
    ----------
    bundle : ArraySequence
        Atlas bundle streamlines.
    n_points : int
        Number of points to resample to before clustering.
    thresh : float
        QuickBundles distance threshold (mm).

    Returns
    -------
    centroid : np.ndarray, shape (n_points, 3)
        The longest cluster centroid.
    """
    if isinstance(bundle, np.ndarray):
        bundle = ArraySequence(bundle[:, :, :3])
    resampled = set_number_of_points(bundle, nb_points=n_points)
    qb = QuickBundles(threshold=thresh, metric=AveragePointwiseEuclideanMetric())
    clusters  = qb.cluster(resampled)
    centroids = Streamlines(clusters.centroids)
    lens = np.array(list(length(centroids)))
    return np.array(centroids[np.argmax(lens)])

def compute_centroid(
    bundle,
    ns: Optional[int] = None,
    s_len: float = 5.0,
    robust_method: str = 'linear',
    thresh: float = 100.0,
    extrapolate_prop: float = 0.3,
) -> Tuple[np.ndarray, int, float]:
    """
    Compute the atlas bundle centroid with optional robust endpoint extension.

    When robust_method is 'linear' or 'spline', the QB centroid is extended
    beyond its natural endpoints. This avoids large terminal segments caused 
    by the QB centroid being shorter than the longer streamlines in the bundle.

    Parameters
    ----------
    bundle : ArraySequence
        Atlas/reference bundle.
    ns : int, optional
        Target number of segments. When set, the centroid is always resampled
        to exactly ns points; s_len is derived from the resulting arc length.
        When None, ns is derived from mean bundle arc length / s_len.
    s_len : float
        Target segment length (mm). Used only when ns is None. Default 5.0.
    robust_method : str
        'none'   -- standard QB centroid resampled to ns points.
        'linear' -- extrapolate from terminal tangent vectors (default).
        'spline' -- fit cubic spline and evaluate beyond [0, 1].
    thresh : float
        QuickBundles clustering threshold (mm). Default 100.
    extrapolate_prop : float
        Proportion of QB centroid arc length to extend on each side.
        Default 0.3.

    Returns
    -------
    centroid : np.ndarray, shape (ns_actual, 3)
        Evenly arc-length-spaced centroid coordinates.
    ns_actual : int
        Actual number of segments (equals ns when ns is provided).
    s_len_actual : float
        Derived segment length (arc_length / (ns - 1)).
    """

    def _resample_centroid(initial: np.ndarray, ns: int) -> Tuple[np.ndarray, int, float]:
        """
        Resample a centroid curve to ns evenly arc-length-spaced points. Used as the fallback
        """
        centroid = np.array(set_number_of_points(Streamlines([initial]), nb_points=ns)[0])
        arc = float(np.linalg.norm(np.diff(centroid, axis=0), axis=1).sum())
        return centroid, ns, arc / max(ns - 1, 1)

    initial = _get_initial_centroid(bundle, n_points=100, thresh=thresh)

    if ns is None:
        avg_length = float(np.mean(list(length(bundle))))
        ns = max(int(np.round(avg_length / s_len)), 1)

    # Standard (non-robust) path
    if robust_method == 'none':
        return _resample_centroid(initial, ns)

    # Robust path: build extended curve
    centroid_arc = float(np.linalg.norm(np.diff(initial, axis=0), axis=1).sum())
    ext_mm = extrapolate_prop * centroid_arc
    tck = None   # only populated for spline path

    if robust_method == 'spline':
        if len(initial) < 4:
            logging.warning(
                "Initial centroid has fewer than 4 points; "
                "cannot fit cubic spline. Falling back to 'linear'."
            )
            robust_method = 'linear'
        else:
            tck, _ = splprep(initial.T, s=0, k=3)
            deriv_start = np.linalg.norm(np.array(splev(0.0, tck, der=1)))
            deriv_end = np.linalg.norm(np.array(splev(1.0, tck, der=1)))
            t_ext_start = ext_mm / deriv_start if deriv_start > 0 else 0.1
            t_ext_end = ext_mm / deriv_end   if deriv_end   > 0 else 0.1
            t_dense = np.linspace(0.0 - t_ext_start, 1.0 + t_ext_end, 1000)
            ext_pts = np.array(splev(t_dense, tck)).T   # (1000, 3)

    if robust_method == 'linear':
        tang_start = initial[0] - initial[1]
        tang_end = initial[-1] - initial[-2]
        tang_start /= np.linalg.norm(tang_start)
        tang_end /= np.linalg.norm(tang_end)
        n_ext = max(1, int(np.round(ext_mm / s_len)))
        steps = np.arange(1, n_ext + 1)[:, np.newaxis]
        ext_start = initial[0]  + steps * tang_start * s_len
        ext_end = initial[-1] + steps * tang_end   * s_len
        ext_pts = np.vstack([ext_start[::-1], initial, ext_end])

    # Project streamline endpoints onto the extended curve via KD-tree
    tree = cKDTree(ext_pts)
    i_starts, i_ends = [], []
    for sl in bundle:
        sl = np.array(sl)
        if len(sl) < 2:
            continue
        _, i_s = tree.query(sl[0])
        _, i_e = tree.query(sl[-1])
        i_starts.append(i_s)
        i_ends.append(i_e)

    if not i_starts:
        logging.warning("No valid endpoints found; falling back to standard centroid.")
        return _resample_centroid(initial, ns)

    i_min = np.minimum(np.array(i_starts), np.array(i_ends))
    i_max = np.maximum(np.array(i_starts), np.array(i_ends))
    i_start = int(np.round(np.percentile(i_min, 2.0)))
    i_end = int(np.round(np.percentile(i_max, 98.0)))

    if i_end <= i_start:
        logging.warning(
            f"Degenerate endpoint range (i_start={i_start} >= i_end={i_end}); "
            "falling back to standard centroid."
        )
        return _resample_centroid(initial, ns)

    ext_range = ext_pts[i_start:i_end + 1]
    seg_lens  = np.linalg.norm(np.diff(ext_range, axis=0), axis=1)
    arc_length = float(seg_lens.sum())
    cum_lens = np.concatenate([[0.0], np.cumsum(seg_lens)])
    target_lens = np.linspace(0.0, arc_length, ns)

    if robust_method == 'spline' and tck is not None:
        t_range = t_dense[i_start:i_end + 1]
        t_resampled = np.interp(target_lens, cum_lens, t_range)
        centroid = np.array(splev(t_resampled, tck)).T
    else:
        centroid = np.array([
            np.interp(target_lens, cum_lens, ext_range[:, dim])
            for dim in range(3)
        ]).T

    s_len_actual = arc_length / max(ns - 1, 1)
    logging.info(
        f"compute_centroid ({robust_method}): ns={ns}, "
        f"arc_length={arc_length:.1f}mm, s_len={s_len_actual:.2f}mm"
    )
    return centroid, ns, s_len_actual

# ---------------------------------------------------------------------------
# Other utilities
# ---------------------------------------------------------------------------

def prepare_bundle_pairs(
    org_bundle: ArraySequence,
    rec_bundle: ArraySequence,
) -> Tuple[ArraySequence, ArraySequence]:
    """
    Resample org_bundle and rec_bundle so that each corresponding streamline
    has the same number of evenly-distributed points.

    Both bundles must have the same number of streamlines in matching order.
    Point counts are taken from org_bundle; rec_bundle is resampled to match.

    Parameters
    ----------
    org_bundle : ArraySequence
        Subject bundle in native space.
    rec_bundle : ArraySequence
        Subject bundle in atlas/MNI space.

    Returns
    -------
    org_bundle_rsp : ArraySequence
    rec_bundle_rsp : ArraySequence
    """
    if len(org_bundle) != len(rec_bundle):
        raise ValueError(
            f"org_bundle ({len(org_bundle)} streamlines) and "
            f"rec_bundle ({len(rec_bundle)} streamlines) must have the same count."
        )
    n_pts = [len(sl) for sl in org_bundle]
    org_rsp = ArraySequence([
        set_number_of_points(org_bundle[i], nb_points=n_pts[i])
        for i in range(len(org_bundle))
    ])
    rec_rsp = ArraySequence([
        set_number_of_points(rec_bundle[i], nb_points=n_pts[i])
        for i in range(len(rec_bundle))
    ])
    return org_rsp, rec_rsp

def point_volume(points: np.ndarray) -> float:
    """
    Estimate the spatial volume (mm^3) of a point cloud via convex hull.

    Parameters
    ----------
    points : np.ndarray, shape (n, 3)
        3-D coordinates of the point cloud (e.g. valid streamline points
        within one along-tract segment, in mm).

    Returns
    -------
    vol : float
        Convex hull volume in mm^3, or 0.0 if the hull cannot be computed.
    """
    if len(points) < 4:
        return 0.0
    try:
        return float(ConvexHull(points).volume)
    except QhullError:
        return 0.0

# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def assign_segments(
    bundle: ArraySequence,
    centroid: np.ndarray,
    fixed_ns: bool = False,
) -> Tuple[np.ndarray, np.ndarray, int]:
    """
    Assign each streamline point to the nearest along-tract segment via
    KD-tree query on the atlas centroid.

    When fixed_ns=False (segment-length mode), terminal bins whose point
    count falls below 0.2 x median are merged into their neighbor, preventing
    near-empty edge segments from producing unreliable means. When
    fixed_ns=True (--ns mode), all ns bins are preserved and the output
    always has exactly len(centroid) segments.

    Parameters
    ----------
    bundle : ArraySequence
        Subject bundle in atlas space (rec_bundle after resampling).
    centroid : np.ndarray, shape (ns, 3)
        Atlas centroid from compute_centroid().
    fixed_ns : bool
        If True, skip terminal-bin merging. Default False.

    Returns
    -------
    s_index : np.ndarray, shape (n_points,)
        Segment index for each streamline point.
    s_dist : np.ndarray, shape (n_points,)
        Distance to the nearest centroid point (inverse-distance weight
        denominator in buan_profile).
    ns_actual : int
        Number of segments after any merging.
    """
    points = bundle.get_data()
    ns = len(centroid)
    s_dist, s_index = cKDTree(centroid).query(points, k=1)

    if fixed_ns:
        return s_index, s_dist, ns

    # Terminal-bin merging (s_len mode only)
    point_count = np.bincount(s_index, minlength=ns)
    thresh = np.median(point_count) * 0.2
    merged = False

    if ns > 1 and point_count[0] < thresh:
        s_index[s_index == 0] = 1
        merged = True
    if ns > 1 and point_count[-1] < thresh:
        s_index[s_index == ns - 1] = ns - 2
        merged = True
    if merged:
        _, s_index = np.unique(s_index, return_inverse=True)

    ns_actual = int(s_index.max()) + 1
    if merged:
        logging.info(f"Terminal-bin merge: ns {ns} -> {ns_actual}")

    return s_index, s_dist, ns_actual


def buan_profile(
    bundle_native: ArraySequence,
    s_index: np.ndarray,
    s_dist: np.ndarray,
    ns: int,
    scalar_map: np.ndarray,
    scalar_affine: np.ndarray,
    mask_data: Optional[np.ndarray] = None,
    mask_affine: Optional[np.ndarray] = None,
    epsilon: float = 1e-8,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute a distance-weighted mean along-tract scalar profile.
    Scalar values are trilinearly interpolated at all streamline point
    coordinates via values_from_volume on the unmasked scalar map. If a mask
    is provided, points outside the mask are excluded.
 
    Parameters
    ----------
    bundle_native : ArraySequence
        Subject bundle in native/scalar-map space (org_bundle after resampling).
    s_index : np.ndarray, shape (n_points,)
        Segment index per point (from assign_segments).
    s_dist : np.ndarray, shape (n_points,)
        Distance to nearest centroid point; used as weight denominator.
    ns : int
        Number of segments.
    scalar_map : np.ndarray
        3-D scalar image array in native space.
    scalar_affine : np.ndarray, shape (4, 4)
        Affine of scalar_map.
    mask_data : np.ndarray, optional
        Binary 3-D mask (1=include, 0=exclude). Must share physical space
        with scalar_map; resolution may differ.
    mask_affine : np.ndarray, shape (4, 4), optional
        Affine of mask_data. Required when mask_data is not None.
    epsilon : float
        Added to distances to prevent division by zero. Default 1e-8.
 
    Returns
    -------
    profile : np.ndarray, shape (ns,)
        Distance-weighted mean per segment. NaN where valid_count == 0.
    valid_counts : np.ndarray, shape (ns,), dtype int
        Number of valid (unmasked, non-NaN) points per segment.
    volumes : np.ndarray, shape (ns,), dtype float
        Convex hull volume (mm^3) of valid points per segment. 0.0 for
        degenerate segments (fewer than 4 points or coplanar point cloud).
    """
    from nibabel.affines import apply_affine
    from scipy.ndimage import map_coordinates
 
    raw_values = values_from_volume(scalar_map, bundle_native, scalar_affine)
    values_flat = np.array([v for sl_vals in raw_values for v in sl_vals], dtype=float)
    valid = ~np.isnan(values_flat)
    points_flat = bundle_native.get_data().astype(float)
 
    if mask_data is not None and mask_affine is not None:
        vox = apply_affine(np.linalg.inv(mask_affine), points_flat) 
        # order=0: nearest-neighbour lookup
        # mode='constant', cval=0: points outside the mask volume are excluded.
        in_mask = map_coordinates(
            mask_data, vox.T, order=0, mode='constant', cval=0
        ) > 0
        valid &= in_mask
        logging.info(f"Mask filtered out {(~in_mask).sum()} / {len(valid)} points ({(~in_mask).mean()*100:.1f}%)")
 
    profile = np.full(ns, np.nan)
    valid_counts = np.zeros(ns, dtype=int)
    volumes = np.zeros(ns, dtype=float)
 
    for i in range(ns):
        seg_valid = (s_index == i) & valid
        n_valid = int(seg_valid.sum())
        valid_counts[i] = n_valid
        seg_pts = points_flat[seg_valid]
        volumes[i] = point_volume(seg_pts)
        if n_valid < 1:
            continue
        w  = 1.0 / (s_dist[seg_valid] + epsilon)
        w /= w.sum()
        profile[i] = float(np.sum(w * values_flat[seg_valid]))
 
    return profile, valid_counts, volumes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args) -> None:
    """Main orchestration."""
    org_bundle, _, _, _ = read_streamlines(args.org_bundle)
    ref_bundle, _, _, _ = read_streamlines(args.ref_bundle)

    if args.rec_bundle is not None:
        rec_bundle, _, _, _ = read_streamlines(args.rec_bundle)
    else:
        logging.info(
            f"Deriving rec_bundle from {args.org_bundle} via MeTA "
            f"transform_tractogram (warp_source={args.warp_source})"
        )
        rec_streamlines, _, _, _ = transform_tractogram(
            tractogram=args.org_bundle,
            transform=args.transform,
            warp=args.warp,
            reference=args.warp_ref,
            inverse=args.inverse,
            warp_first=args.warp_first,
            warp_source=args.warp_source,
            trim_endpoints=not args.no_trim,
            output=None,
        )
        rec_bundle = ArraySequence(rec_streamlines)

    if len(rec_bundle) < args.min_lines:
        logging.warning(
            f"Only {len(rec_bundle)} streamlines in rec_bundle "
            f"(min_lines={args.min_lines}). Skipping."
        )
        return

    # Resample org/rec to matching point counts
    org_bundle, rec_bundle = prepare_bundle_pairs(org_bundle, rec_bundle)

    # Compute atlas centroid once — reused for all scalar maps
    fixed_ns = args.ns is not None
    centroid, ns_actual, s_len_actual = compute_centroid(
        ref_bundle,
        ns=args.ns,
        s_len=args.s_len,
        robust_method=args.robust_method,
    )

    s_index, s_dist, ns_final = assign_segments(
        rec_bundle, centroid, fixed_ns=fixed_ns
    )

    mask_data, mask_affine = None, None
    if args.mask:
        logging.info(f"Loading mask: {args.mask}")
        mask_data, mask_affine = load_nifti(args.mask)

    fieldnames = ['subjectID', 'bundle', 'segment', 'mean', 'valid_point_count', 'volume_mm3']
    for map_path, out_path in zip(args.scalar_maps, args.outputs):
        logging.info(f"Processing scalar map: {map_path}")
        scalar_map, scalar_affine = load_nifti(map_path)

        profile, valid_counts, volumes = buan_profile(
            bundle_native=org_bundle,
            s_index=s_index,
            s_dist=s_dist,
            ns=ns_final,
            scalar_map=scalar_map,
            scalar_affine=scalar_affine,
            mask_data=mask_data,
            mask_affine=mask_affine,
        )

        rows = [{
            'subjectID':         args.subject,
            'bundle':            args.bundle_name,
            'segment':           i + 1,
            'mean':              val,
            'valid_point_count': int(cnt),
            'volume_mm3':        vol,
            } for i, (val, cnt, vol) in enumerate(zip(profile, valid_counts, volumes))]

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, 'w', newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        n_nan = int(np.isnan(profile).sum())
        logging.info(
            f"Written {len(rows)} rows to {out_path} "
            f"({n_nan} segment(s) with NaN mean)"
        )

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s:%(module)s.%(funcName)s]: %(message)s",
        datefmt='%H:%M:%S',
    )
    parser = argparse.ArgumentParser(
        description='Along-tract BUAN profile extraction',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument('--rec_bundle', '-rec', default=None, help=('Subject bundle in atlas/MNI space.'
                    'If omitted, --org_bundle is transformed to MNI using --transform and/or --warp.'))
    parser.add_argument('--org_bundle', '-org', required=True, help='Subject bundle in native space')
    parser.add_argument('--ref_bundle', '-ref', required=True, help='Atlas/reference bundle')
    parser.add_argument('--scalar_maps', '-maps', nargs='+', required=True,
                        help='One or more scalar map NIfTIs in native space')
    parser.add_argument('--outputs', '-o', nargs='+', required=True,
                        help='Output CSV paths, one per scalar map (must match in count)')
    parser.add_argument('--mask', '-mask', default=None,
                        help=('Optional binary NIfTI mask applied to all scalar maps.'))

    parser.add_argument('--transform', default=None, help='Affine matrix. subject space → MNI')
    parser.add_argument('--inverse', action='store_true', help='Invert the supplied affine (MNI → subject)')

    parser.add_argument('--warp', default=None, help='Non-linear warp field')
    parser.add_argument('--warp_ref', default=None,
                        help=('Reference image for the warp (required if --warp is given)'))
    parser.add_argument('--warp_source', choices=['ants', 'dsi_studio'], default='dsi_studio',
                        help='Warp convention method')
    parser.add_argument('--warp_first', action='store_true', help='Apply the warp before the affine transform')
    parser.add_argument('--no_trim', action='store_true', help='Keep endpoints outside the warp grid')

    parser.add_argument('--subject', '-subj', required=True, help='Subject ID.')
    parser.add_argument('--bundle_name', '-bndl', required=True, help='Bundle name.')

    parser.add_argument('--s_len', '-s', type=float, default=5.0,
                        help='Target segment length (mm). Ignored when --ns is set.')
    parser.add_argument('--ns', '-ns', type=int, default=None,
                        help=(
                            'Number of segments. Overrides --s_len; output always '
                            'has exactly this many rows with no terminal-bin merging.'
                        ))
    parser.add_argument('--robust_method', '-robust', default='linear',
                        choices=['linear', 'spline', 'none'],
                        help=(
                            'Centroid extension method. '
                            '"linear": extrapolate from terminal tangent vectors (default). '
                            '"spline": cubic spline extrapolation. '
                            '"none": standard QuickBundles centroid.'
                        ))

    parser.add_argument('--min_lines', '-min_lines', type=int, default=20,
                        help='Skip bundle if streamline count is below this threshold.')

    args = parser.parse_args()

    if args.rec_bundle is None:
        if args.transform is None and args.warp is None:
            parser.error("Provide --rec_bundle, or --transform/--warp "
                        "(with --warp_ref) to derive it from --org_bundle.")
        if args.warp_ref is None:
            parser.error("--warp_ref is required when deriving rec_bundle from org_bundle.")

    if args.ns is not None and args.ns < 1:
        parser.error("--ns must be a positive integer")
    if len(args.scalar_maps) != len(args.outputs):
        parser.error(
            f"--scalar_maps ({len(args.scalar_maps)}) and "
            f"--outputs ({len(args.outputs)}) must have the same number of entries"
        )

    run(args)


if __name__ == '__main__':
    main()