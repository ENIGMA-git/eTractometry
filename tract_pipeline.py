#!/usr/bin/env python3

import os
import re
import sys
import shlex
import select
import shutil
import logging
import argparse
import subprocess
from datetime import datetime

logging.basicConfig(stream=sys.stdout, format='%(asctime)s,%(msecs)d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S', encoding='utf-8', level=logging.INFO, force=True)


def parse_args():
    parser = argparse.ArgumentParser(description="The ENIGMA-Tractometry Toolbox: an end-to-end pipeline for white matter bundle segmentation, along-tract profiling (MeTA, BUAN), and visual QC reports.")
    parser.add_argument("--output_dir", required=True, help="Output directory for all derivatives: diffusion models, white matter bundles, tractometry profiles, spreadsheets, and QC reports.")
    parser.add_argument("--dataset", help="Dataset (cohort or site) name.")
    parser.add_argument("--subjects", help="Text file of subject IDs.")
    parser.add_argument("--dwi", help="Preprocessed diffusion-weighted image (DWI) path pattern, NIfTI (.nii.gz) or DSI-Studio source (.sz).")
    parser.add_argument("--mask", help="Brain mask path. Required for NIfTI DWI; optional for .sz, where DSI-Studio creates one.")
    parser.add_argument("--bval", help="b-value file (FSL format) path. Required for NIfTI DWI.")
    parser.add_argument("--bvec", help="b-vector file (FSL format gradient directions) path. Required for NIfTI DWI.")

    parser.add_argument("--profile", help="Comma-separated Nextflow profiles: one executor (local, sge, slurm, pbs, pbspro, lsf), and one engine that provides DSI-Studio and the Python tools (conda, apptainer, hybrid) e.g. local,conda.")
    parser.add_argument("--queue", help="Cluster queue. Required for cluster executors.")
    parser.add_argument("--n_parallel", type=int, default=600, help="Maximum number of queued or running jobs")
    parser.add_argument("--dsi_studio_bin", help="Path to the DSI-Studio executable. Required with the conda engine.")
    parser.add_argument("--python_env", help="Path to the conda environment with the Python tools (meta, dive, ...). Required with the conda and hybrid engines.")

    parser.add_argument("--methods", default="meta", help="Comma-separated tractometry methods: meta (Medial Tractography Analysis), buan (Bundle Analytics), or none to stop after diffusion modeling and bundle segmentation.")
    parser.add_argument("--metrics", default="dti_fa,md,ad,rd,qa,gfa,iso,rdi,qir,rd1,rd2,nrdi02L,nrdi04L", help="Comma-separated diffusion metric maps exported from the DSI-Studio GQI model.")
    parser.add_argument("--extra_maps", nargs="+", help="Extra subject-space metric maps (e.g. NODDI) sampled along each bundle with the --metrics maps, as name=path pattern pairs, e.g. icvf=/data/{subject}_icvf.nii.gz odi=/data/{subject}_odi.nii.gz.")
    parser.add_argument("--similarity_percentile", default="50", help="Comma-separated percentile thresholds in [0, 100] applied to each subject's MNI-space bundle density map before comparing it with the HCP1065 atlas bundle; 50 is always included.")

    parser.add_argument("--restart", action="store_true", help="Re-evaluate every task instead of resuming the last run (storeDir cache hits still skip work).")
    parser.add_argument("--ansi_log", action="store_true", help="Show the dynamic ANSI progress log; leave off for large HPC runs.")
    parser.add_argument("--full_reports", action="store_true", help="Also write the Nextflow HTML report, timeline, and DAG; leave off for 200k+ jobs.")
    parser.add_argument("--print_logs", action="store_true", help="Print the Nextflow run history and exit.")
    parser.add_argument("--clean", action="store_true", help="Remove all Nextflow files of the latest run, including logs and cache, and exit.")
    args = parser.parse_args()

    for name in ("output_dir", "subjects", "dwi", "mask", "bval", "bvec", "dsi_studio_bin", "python_env"):
        if getattr(args, name) is not None:
            setattr(args, name, os.path.abspath(getattr(args, name)))
    if shutil.which("nextflow") is None:
        parser.error("nextflow was not found on PATH.")
    if args.print_logs or args.clean:
        return args

    missing = [f"--{name}" for name in ("dataset", "subjects", "dwi", "profile") if getattr(args, name) is None]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")
    if not args.dwi.endswith((".nii", ".nii.gz", ".sz")):
        parser.error("--dwi must be a NIfTI (.nii / .nii.gz) or DSI-Studio source (.sz) path.")
    if not args.dwi.endswith(".sz") and None in (args.mask, args.bval, args.bvec):
        parser.error("--mask, --bval and --bvec are required for NIfTI input.")

    profiles = args.profile.split(",")
    executors = [p for p in profiles if p in ("local", "sge", "slurm", "pbs", "pbspro", "lsf")]
    engines = [p for p in profiles if p in ("conda", "apptainer", "hybrid")]
    if len(executors) != 1 or len(engines) != 1:
        parser.error("--profile must include exactly one executor (local, sge, slurm, pbs, pbspro, lsf) and one engine (conda, apptainer, hybrid).")
    executor, engine = executors[0], engines[0]
    if executor != "local" and args.queue is None:
        parser.error(f"--queue is required with the {executor} executor.")
    if engine in ("conda", "hybrid") and args.python_env is None:
        parser.error(f"--python_env is required with the {engine} engine.")
    if engine == "conda" and args.dsi_studio_bin is None:
        parser.error("--dsi_studio_bin is required with the conda engine.")
    if args.python_env is not None and not os.path.isdir(args.python_env):
        parser.error(f"--python_env directory not found: {args.python_env}")
    if args.dsi_studio_bin is not None and shutil.which(args.dsi_studio_bin) is None:
        parser.error(f"--dsi_studio_bin is not an executable file: {args.dsi_studio_bin}")

    if args.n_parallel < 1:
        parser.error("--n_parallel must be a positive integer.")
    if args.n_parallel > 1000:
        logging.warning(f"--n_parallel {args.n_parallel} is high for a 10 GB Nextflow head node. Consider 300-500 for 200k+ jobs.")

    percentiles = args.similarity_percentile.replace(" ", "")
    if not re.fullmatch(r"\d+(,\d+)*", percentiles):
        parser.error("--similarity_percentile must be a comma-separated list of integers, e.g. 70,80,90.")
    percentiles = [int(p) for p in percentiles.split(",")]
    if max(percentiles) > 100 or len(set(percentiles)) < len(percentiles):
        parser.error("--similarity_percentile values must be unique integers in [0, 100].")
    if 50 not in percentiles:
        percentiles.append(50)
    args.similarity_percentile = ",".join(str(p) for p in percentiles)

    if args.extra_maps is not None:
        if not all(re.fullmatch(r"\w+=.+\.nii(\.gz)?", m) for m in args.extra_maps):
            parser.error("--extra_maps must be name=pattern pairs of NIfTI maps, e.g. icvf=/data/{subject}_icvf.nii.gz.")
        extra_maps = {name: os.path.abspath(pattern) for name, pattern in (m.split("=", 1) for m in args.extra_maps)}
        if len(extra_maps) < len(args.extra_maps) or set(extra_maps) & set(args.metrics.replace(" ", "").split(",")):
            parser.error("--extra_maps names must be unique and differ from the --metrics names.")
        args.extra_maps = extra_maps

    return args


def main():
    """Collect the subject inputs into a CSV and launch the Nextflow workflow."""
    args = parse_args()
    pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    reports_dir = f"{args.output_dir}/reports"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    os.makedirs(f"{reports_dir}/logs", exist_ok=True)
    os.environ["NXF_LOG_FILE"] = f"{reports_dir}/logs/nextflow_log_{timestamp}.txt"
    os.environ["NXF_CACHE_DIR"] = f"{args.output_dir}/.nextflow_cache"

    ## main.nf reads atlas/ relative to the launch directory:
    os.chdir(pipeline_dir)

    if args.print_logs:
        os.execvp("nextflow", ["nextflow", "log"])
    if args.clean:
        logging.warning("--clean removes all Nextflow files of the latest run, including logs and cache. Press Enter to continue or Ctrl+C to cancel (continues in 10 seconds).")
        select.select([sys.stdin], [], [], 10)
        os.execvp("nextflow", ["nextflow", "clean", "-f"])

    ## Rebuild the input CSV on every run so added or removed subjects are picked up:
    input_csv = f"{args.output_dir}/spreadsheets/{args.dataset}_input_data.csv"
    os.makedirs(os.path.dirname(input_csv), exist_ok=True)
    python = sys.executable if args.python_env is None else f"{args.python_env}/bin/python"
    csv_cmd = [python, "bin/get_dMRI_data.py", "--subject_IDs", args.subjects, "--dwi_format", args.dwi, "--output", input_csv]
    for name in ("mask", "bval", "bvec"):
        if getattr(args, name) is not None:
            csv_cmd += [f"--{name}_format", getattr(args, name)]
    if args.extra_maps is not None:
        csv_cmd += ["--extra_maps_format"] + [f"{name}={pattern}" for name, pattern in args.extra_maps.items()]
    logging.info(f"Collecting input files for subjects in {args.subjects}")
    if subprocess.run(csv_cmd).returncode != 0:
        sys.exit("Failed to collect the input files.")

    ## Launch Nextflow:
    nf_cmd = ["nextflow", "run", "main.nf", "-profile", args.profile, "-queue-size", str(args.n_parallel), "-ansi-log", str(args.ansi_log).lower(),
        "-work-dir", f"{args.output_dir}/work", "-output-dir", f"{args.output_dir}/subjects", "-with-trace", f"{reports_dir}/tract_trace_{timestamp}.tsv"]
    if not args.restart:
        nf_cmd.append("-resume")
    if args.queue is not None:
        nf_cmd += ["-process.queue", args.queue]
    if args.full_reports:
        nf_cmd += ["-with-report", f"{reports_dir}/tract_report_{timestamp}.html", "-with-timeline", f"{reports_dir}/tract_timeline_{timestamp}.html", "-with-dag", f"{reports_dir}/tract_dag_{timestamp}.html"]

    params = {
        "output_dir": args.output_dir,
        "dataset": args.dataset,
        "methods": args.methods,
        "metrics": args.metrics,
        "extra_maps": None if args.extra_maps is None else ",".join(args.extra_maps),
        "similarity_percentile": args.similarity_percentile,
        "subjects_list": args.subjects,
        "input_csv": input_csv,
        "cortical_atlas_mni": f"{pipeline_dir}/atlas/HCP-MMP.nii.gz",
        "mni_qa_template_1mm": f"{pipeline_dir}/atlas/human_qa.1mm.nii.gz",
        "mni_qa_template_2mm": f"{pipeline_dir}/atlas/human_qa.2mm.nii.gz",
        "atlas_bundle_pngs": f"{pipeline_dir}/atlas/full/pngs",
        "bundle_atlas_dir": f"{pipeline_dir}/atlas/sparse",
        "dsi_studio_bin": args.dsi_studio_bin,
        "python_env": args.python_env
    }
    for name, value in params.items():
        if value is not None:
            nf_cmd += [f"--{name}", value]

    logging.info(f"Nextflow log: {os.environ['NXF_LOG_FILE']}")
    logging.info(f"Running: {shlex.join(nf_cmd)}")
    os.execvp("nextflow", nf_cmd)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
