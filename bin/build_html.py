#!/usr/bin/env python

import os
import re
import json
import argparse
import numpy as np
import pandas as pd
from glob import glob
from airium import Airium


OUTLIER_TAIL = {
    "dice": "lower",
    "overlap": "lower",
    "precision": "lower",
    "overreach_reference": "upper",
    "overreach_predicted": "upper",
    "jaccard": "lower",
    "ncc": "lower",
    "spearman": "lower",
    "nmi": "lower"
}
QC_COLUMNS = ["dataset", "subjectID", "figure_name", "qc", "reason", "comment", "outlier"]
QC_STATUSES = {"PASS": ["✅", "Pass!"], "FAIL": ["❌", "Fail!"], "FLAG": ["🚩", "Flag!"], "MISSING": ["⚠️", "Missing!"], "Unreviewed": ["🔄", "Reset QC!"]}
QC_REASONS = ["Overestimated", "Underestimated", "Misshapen", "Misaligned"]


def format_percentile(percentile):
    """Return a percentile as an ordinal, e.g. 1st, 85th or 2.5th."""
    if not float(percentile).is_integer():
        return f"{percentile:g}th"
    percentile = int(percentile)
    suffix = "th" if 10 <= percentile % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(percentile % 10, "th")
    return f"{percentile}{suffix}"


def format_metric_label(metric):
    """Return the display label of a similarity metric, e.g. overreach_reference -> Overreach Reference."""
    return metric.upper() if metric in ["ncc", "nmi"] else metric.replace("_", " ").title()


def get_href(path, page_dir):
    """Return path relative to page_dir, or unchanged when page_dir is None."""
    return path if page_dir is None else os.path.relpath(path, page_dir)


def load_values(values):
    """Return values sorted, reading them line by line when the first value is a file."""
    if os.path.isfile(values[0]):
        with open(values[0]) as f:
            values = f.read().splitlines()
    return sorted(values)


def load_group_similarity_outliers(paths, overlap_lim=0.8, overreach_lim=0.1):
    """Return {(subject, bundle): comment} for bundles that overreach or miss the within-group percentile bundles."""
    percentiles = []
    for path in paths:
        match = re.search(r"(\d+(?:\.\d+)?)$", os.path.splitext(os.path.basename(path))[0])
        if match is None:
            raise ValueError(f"Could not infer the percentile from {path}. Expected a filename ending with a number, like within_group_similarity85.csv.")
        percentiles.append(float(match.group(1)))
    if len(set(percentiles)) < len(percentiles):
        raise ValueError("group_similarity has duplicate percentile CSVs.")
    missing = sorted({1.0, 30.0, 50.0, 85.0, 99.0}.difference(percentiles))
    if missing:
        raise ValueError(f"group_similarity is missing the {', '.join(format_percentile(value) for value in missing)} percentile CSVs.")

    overlap = {}
    overreach = {}
    for path, percentile in zip(paths, percentiles):
        df = pd.read_csv(path, dtype={"subjectID": str, "bundle": str}).set_index(["subjectID", "bundle"])
        overlap[percentile] = df["overlap"]
        overreach[percentile] = df["overreach_reference"]
    overlap = pd.DataFrame(overlap)
    overreach = pd.DataFrame(overreach) > overreach_lim

    reasons = pd.DataFrame({
        "overreaches or overlaps the 1st percentile": (overlap[1.0] > overlap_lim) | overreach[1.0],
        "overreaches the 30th percentile": overreach[30.0],
        "overreaches the 50th percentile and does not overlap the 85th percentile": overreach[50.0] & (overlap[85.0] <= overlap_lim),
        "does not overlap the 99th percentile": overlap[99.0] <= overlap_lim
    })
    reasons = reasons[reasons.any(axis=1)]
    return {key: "Group similarity outlier: " + "; ".join(row[row].index) for key, row in reasons.iterrows()}


def load_similarity_outliers(path, subjects, figure_names, percentile=5):
    """Return {(subject, bundle): comment} for subjects in the outer percentile tail of any reference similarity metric within their bundle."""
    df = pd.read_csv(path, dtype={"subjectID": str, "bundle": str})
    df = df[df["subjectID"].isin(subjects) & df["bundle"].isin(figure_names)].set_index(["subjectID", "bundle"])
    metrics = [metric for metric in OUTLIER_TAIL if metric in df.columns]
    if not metrics:
        raise ValueError(f"{path} must include at least one of these metrics: {', '.join(OUTLIER_TAIL)}.")

    def get_percentile(values, q):
        """Return the q-th percentile of the non-NaN values, or NaN when there are none."""
        values = values.dropna()
        return np.percentile(values, q) if len(values) else np.nan

    ## Flag values at or below the lower tail, or at or above the upper tail, of each bundle:
    flags = pd.DataFrame(index=df.index)
    for metric in metrics:
        if OUTLIER_TAIL[metric] == "lower":
            flags[metric] = df[metric] <= df.groupby("bundle")[metric].transform(get_percentile, percentile)
        else:
            flags[metric] = df[metric] >= df.groupby("bundle")[metric].transform(get_percentile, 100 - percentile)

    outliers = {}
    for key, row in flags[flags.any(axis=1)].iterrows():
        lower = [format_metric_label(metric) for metric in metrics if row[metric] and OUTLIER_TAIL[metric] == "lower"]
        upper = [format_metric_label(metric) for metric in metrics if row[metric] and OUTLIER_TAIL[metric] == "upper"]
        parts = []
        if lower:
            parts.append(f"At or below the {format_percentile(percentile)} percentile in {', '.join(lower)}")
        if upper:
            parts.append(f"At or above the {format_percentile(100 - percentile)} percentile in {', '.join(upper)}")
        outliers[key] = "Reference similarity outlier: " + "; ".join(parts)
    return outliers


def write_images(a, figures, pattern, page_dir):
    """Write a row of figures, or an error when none exist."""
    if not figures:
        a.div(klass="error").p(_t=f"No figures exist: {pattern}")
        return
    with a.div(klass="image-row"):
        for figure in figures:
            href = get_href(figure, page_dir)
            a.img(decoding="async", alt=href, onclick="openImagePreview(this)", **{"data-src": href})


def build_html(output, figures_path_format, figure_names, group, subjects, atlas_path_format=None, mean_path_format=None, mean_source_path_format=None, rel_dir=None,
               qc_csv_path_format=None, similarity_metrics=None, group_similarity=None, outlier_percentile=5, fig_number=4, aspect_ratio=None, subject_n=200, link_html_help=None):
    """Write a QC HTML sheet and its visual QC CSV for each figure name."""
    if mean_source_path_format is not None and mean_path_format is None:
        raise ValueError("mean_path_format is required when mean_source_path_format is used.")
    if not 0 <= outlier_percentile <= 100:
        raise ValueError("outlier_percentile must be between 0 and 100.")
    if subject_n <= 0:
        raise ValueError("subject_n must be greater than 0.")
    subjects = load_values(subjects)
    figure_names = load_values(figure_names)

    ## Automatic outlier comments:
    comments = {}
    if group_similarity is not None:
        comments = load_group_similarity_outliers(group_similarity)
    if similarity_metrics is not None:
        for key, comment in load_similarity_outliers(similarity_metrics, subjects, figure_names, percentile=outlier_percentile).items():
            comments[key] = f"{comments[key]} | {comment}" if key in comments else comment

    style = f".image-row img {{ display: inline-block; width: {100 / fig_number}%; }}\n.image-row, .error, .blank {{ aspect-ratio: {fig_number if aspect_ratio is None else aspect_ratio}; }}\n"
    if atlas_path_format is None:
        style += ".blank { display: none; }\n"
    html_files = [os.path.join(output.format(figure_name=figure_name), f"{figure_name}_{group}.html") for figure_name in figure_names]

    for i, figure_name in enumerate(figure_names):
        output_dir = output.format(figure_name=figure_name)
        page_dir = None if rel_dir is None else rel_dir.format(figure_name=figure_name)
        if qc_csv_path_format is None:
            csv_file = os.path.join(output_dir, f"{figure_name}_{group}_visual_qc.csv")
        else:
            csv_file = qc_csv_path_format.format(figure_name=figure_name, group=group, dataset=group)
        csv_href = os.path.relpath(csv_file, output_dir)
        os.makedirs(output_dir, exist_ok=True)

        ## Seed the visual QC CSV with missing figures and outlier comments:
        manifest = []
        rows = []
        for subject in subjects:
            pattern = figures_path_format.format(subject=subject, figure_name=figure_name)
            figures = sorted(glob(pattern))
            comment = comments.get((subject, figure_name), "")
            manifest.append([subject, pattern, [get_href(figure, page_dir) for figure in figures], bool(comment)])
            if figures:
                rows.append([group, subject, figure_name, "Unreviewed", "", comment, "OUTLIER" if comment else ""])
            else:
                rows.append([group, subject, figure_name, "MISSING", "", comment, "MISSING"])
        if mean_path_format is not None:
            rows.append([group, "NA", figure_name, "NA", "NA", "", "NA"])
        qc_csv = pd.DataFrame(rows, columns=QC_COLUMNS).to_csv(index=False, lineterminator="\n")
        with open(csv_file, "w", encoding="utf-8") as f:
            f.write(qc_csv)

        data = {"datasetName": group, "defaultBatchSize": subject_n, "csvColumns": QC_COLUMNS, "qcStatuses": QC_STATUSES, "qcReasonOptions": QC_REASONS, "embeddedQCCSV": qc_csv, "subjectManifest": manifest}
        script = "".join(f"const {name} = {json.dumps(value, ensure_ascii=False, separators=(',', ':'))};\n" for name, value in data.items()) + JS

        a = Airium()
        a("<!DOCTYPE html>")
        with a.html(lang="en"):
            with a.head():
                a.meta(charset="utf-8")
                a.title(_t=figure_name)
                a.meta(name="qc-csv-path", content=csv_href)
                a.style(_t=style + CSS)
                a.script(_t=script)
            with a.body():
                with a.div(klass="image-preview", id="image-preview", onclick="closeImagePreview()", **{"aria-hidden": "true"}):
                    a.button(_t="x", type="button", **{"aria-label": "Close image preview"})
                    a.img(id="image-preview-img", onclick="event.stopPropagation()")
                with a.div(klass="csv-load-prompt", id="csv-load-prompt", **{"aria-hidden": "true"}):
                    with a.div(klass="csv-load-dialog"):
                        a.h2(_t="Load QC CSV")
                        a.p(_t="Before you can QC, you must load the designated file:")
                        a.code(_t=csv_href)
                        a.p(id="csv-load-message", klass="csv-load-message")
                        a.button(_t="Choose CSV", type="button", onclick="loadRequiredQCCSV()")

                ## Top bar with batch size, filters and the atlas overlay switch:
                with a.div(klass="top-bar"):
                    with a.div(klass="top-bar-row top-bar-info"):
                        a.h1(_t=figure_name, title=figure_name)
                        a.h2(_t=f"{group} - {i + 1}/{len(figure_names)}")
                        a.h2(id="batch-count")
                    with a.div(klass="top-bar-row top-bar-controls"):
                        with a.label(_t="Batch size:", klass="batch-size-control", title="Number of subjects to render in a page"):
                            a.input(type="number", id="batch-size", min="1", onchange="setBatchSizeFromInput()")
                        with a.details(klass="qc-filter-menu", title="Filter for a QC status ..."):
                            a.summary(_t="Filter By", id="qc-filter-summary")
                            with a.div(klass="qc-filter-options"):
                                with a.div(klass="qc-filter-column"):
                                    a.div(_t="QC Status", klass="qc-filter-heading")
                                    for status in ["Unreviewed", "PASS", "FAIL", "FLAG", "MISSING"]:
                                        with a.label():
                                            a.input(type="checkbox", klass="qc-status-filter", value=status, onchange="applyStatusFilters()")
                                            a(status)
                                if similarity_metrics is not None or group_similarity is not None:
                                    with a.div(klass="qc-filter-column"):
                                        a.div(_t="Automatic Detection", klass="qc-filter-heading")
                                        with a.label():
                                            a.input(type="checkbox", id="qc-outlier-filter", onchange="applyStatusFilters()")
                                            a("Outlier")
                        if atlas_path_format is not None:
                            with a.label(klass="atlas-switch", title="Toggle atlas overlay"):
                                a.input(type="checkbox", id="atlas-mode", checked=True, onchange="toggleAtlas(this)")
                                with a.span(klass="atlas-switch-track"):
                                    a.span(_t="Overlay", klass="atlas-switch-text")
                                    a.span(_t="Off", klass="atlas-switch-text")

                ## Atlas overlay and dataset distribution:
                if atlas_path_format is not None:
                    pattern = atlas_path_format.format(figure_name=figure_name)
                    with a.div(klass="atlas-class", id="atlas"):
                        write_images(a, sorted(glob(pattern)), pattern, page_dir)
                if mean_path_format is not None:
                    pattern = mean_path_format.format(figure_name=figure_name)
                    if mean_source_path_format is None:
                        mean_figures = sorted(glob(pattern))
                    else:
                        mean_figures = [os.path.join(os.path.dirname(pattern), os.path.basename(figure)) for figure in sorted(glob(mean_source_path_format.format(figure_name=figure_name)))]
                    a.div(klass="blank").div(klass="snap")
                    with a.div(klass="snap", id="dataset").div(klass="subject-section"):
                        with a.div(klass="subject-header"):
                            a.h1(_t=group, title=group)
                            a.h2(_t="Dataset Distribution", klass="subject-index")
                        write_images(a, mean_figures, pattern, page_dir)
                        with a.div(klass="qc-input").ul(klass="legend"):
                            a.label(_t="Percentiles:")
                            for color, label in [("#a78eb5", "1st"), ("#84aec2", "50th"), ("#819a58", "85th"), ("#aa8756", "99th")]:
                                with a.li():
                                    a.span(klass="color-dot", style=f"background: {color};")
                                    a(label)
                        with a.div(klass="qc-control-panel").div(klass="qc-reason-row"):
                            a.input(type="text", id="dataset-comment", klass="qc-reason", placeholder="Dataset comment ...", oninput="saveDatasetCommentUI(this)")

                ## Subjects are rendered in batches by the page script:
                a.div(id="subjects-container")
                with a.div(klass="bottom-toolbar"):
                    with a.select(id="fill-empty-values", title="Choose a QC status to populate unreviewed selections when saving ..."):
                        a.option(_t="Leave Unreviewed", value="")
                        for status in ["PASS", "FAIL", "FLAG", "MISSING"]:
                            a.option(_t=f"{status.title()} Unreviewed", value=status)
                    a.button(_t="Save", type="button", klass="save-qc", onclick="saveCSV()", title="Save current QC to CSV sheet")
                    a.button(_t="Clear", type="button", klass="clear-qc", onclick="clearSheet()", title="Clear manual QC and restore starting values")
                    if link_html_help is not None:
                        a.button(_t="❓", type="button", klass="help-qc", onclick=f"window.open({json.dumps(get_href(link_html_help, page_dir))}, '_blank')", title="Open QC help", **{"aria-label": "Open QC help"})
                    a.button(_t="Prev", type="button", id="previous-qc-button", klass="nav-qc previous-qc", onclick="navigate(-1)", **{"data-href": os.path.relpath(html_files[i - 1], output_dir) if i > 0 else ""})
                    a.button(_t="Next", type="button", id="next-qc-button", klass="nav-qc", onclick="navigate(1)", **{"data-href": os.path.relpath(html_files[i + 1], output_dir) if i < len(html_files) - 1 else ""})
        with open(html_files[i], "wb") as f:
            f.write(bytes(a))


def main():
    parser = argparse.ArgumentParser(description="Create QC HTML sheets for bundle figures.")
    parser.add_argument("--output", required=True, help="Path format to save HTML files, supporting {figure_name}.")
    parser.add_argument("--figures_path_format", required=True, help="Path format to figures, supporting {subject} and {figure_name}.")
    parser.add_argument("--figure_names", nargs="+", required=True, help="Names of bundles or other figures, or a file listing them.")
    parser.add_argument("--group", required=True, help="Name of the dataset.")
    parser.add_argument("--subjects", nargs="+", required=True, help="Subject IDs, or a file listing them.")
    parser.add_argument("--atlas_path_format", help="Path format to atlas bundle figures.")
    parser.add_argument("--mean_path_format", help="Path format to mean bundle figures.")
    parser.add_argument("--mean_source_path_format", help="Path format to staged mean figures used for discovery. Image links still use the directory in --mean_path_format, so HTML generation does not need to wait for mean figures to be published.")
    parser.add_argument("--rel_dir", help="Path used as the base for relative figure and help links, supporting {figure_name}.")
    parser.add_argument("--qc_csv_path_format", help="Path format to the visual QC CSV, supporting {figure_name}, {group} and {dataset}. Default is {figure_name}_{group}_visual_qc.csv next to the HTML.")
    parser.add_argument("--similarity_metrics", help="Reference similarity metrics CSV. Outliers in any OUTLIER_TAIL metric get an initial comment and appear in the Outlier filter.")
    parser.add_argument("--group_similarity", nargs="+", help="1st, 30th, 50th, 85th and 99th within-group similarity CSVs, named with the percentile at the end (within_group_similarity85.csv). Outliers get an initial comment and appear in the Outlier filter.")
    parser.add_argument("--flag_outliers", nargs="?", const=5.0, type=float, metavar="PERCENTILE", help="Tail percentile for reference similarity outliers. Default is 5. Upper-tail metrics use 100 minus this value.")
    parser.add_argument("--fig_number", "-n", type=int, default=4, help="Number of expected figures.")
    parser.add_argument("--aspect_ratio", type=float, help="Aspect ratio of the figure row. Default is the number of figures.")
    parser.add_argument("--subject_n", type=int, default=200, help="Default number of subjects to load per HTML batch.")
    parser.add_argument("--link_html_help", help="HTML help file to link from a help button, relative to --rel_dir when set.")
    args = parser.parse_args()
    if args.flag_outliers is not None and args.similarity_metrics is None:
        parser.error("--similarity_metrics is required when --flag_outliers is used.")

    build_html(output=args.output, figures_path_format=args.figures_path_format, figure_names=args.figure_names, group=args.group, subjects=args.subjects,
            atlas_path_format=args.atlas_path_format, mean_path_format=args.mean_path_format, mean_source_path_format=args.mean_source_path_format, rel_dir=args.rel_dir,
            qc_csv_path_format=args.qc_csv_path_format, similarity_metrics=args.similarity_metrics, group_similarity=args.group_similarity,
            outlier_percentile=5 if args.flag_outliers is None else args.flag_outliers, fig_number=args.fig_number, aspect_ratio=args.aspect_ratio, subject_n=args.subject_n,
            link_html_help=args.link_html_help)


## Page style and script shared by every sheet:
CSS = """
html {
    background: rgb(50, 50, 50);
    scroll-snap-type: y mandatory;
    scroll-padding-top: 110px;
}
body {
    font-family: Arial, sans-serif;
    font-size: 14pt;
    max-width: 800px;
    min-width: 400px;
    background: white;
    outline: rgb(220, 220, 220) solid 5px;
    margin: auto;
}
p {
    line-height: normal;
    font-size: 70%;
    word-wrap: break-word;
}
.error {
    align-items: center;
    box-sizing: border-box;
    display: flex;
    justify-content: center;
    padding: 20px;
    text-align: center;
}
.error p {
    background: rgb(255, 245, 245);
    border: 1px solid rgb(240, 170, 170);
    border-radius: 8px;
    color: rgb(130, 0, 0);
    font-size: 70%;
    line-height: 1.35;
    margin: 0;
    max-width: 70ch;
    overflow-wrap: anywhere;
    padding: 12px 14px;
    word-break: break-word;
}
.blank {
    padding-bottom: 55px;
}
.snap {
    scroll-snap-align: start;
}

/* QC status colors, set on subject sections and status buttons through data-status */
.subject-section, .qc-status-button {
    --qc-bg: rgba(245, 245, 245, 0.90);
    --qc-border: rgb(120, 120, 120);
}
[data-status="PASS"] {
    --qc-bg: rgba(97, 189, 0, 0.90);
    --qc-border: rgb(0, 170, 0);
}
[data-status="FAIL"] {
    --qc-bg: rgba(235, 64, 52, 0.90);
    --qc-border: rgb(240, 0, 0);
}
[data-status="FLAG"], .outlier-indicator {
    --qc-bg: rgba(255, 151, 23, 0.90);
    --qc-border: rgb(240, 100, 10);
}
[data-status="MISSING"] {
    --qc-bg: rgba(255, 247, 0, 0.90);
    --qc-border: rgb(200, 170, 0);
}

/* Top bar */
.top-bar {
    position: sticky;
    top: 0;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    border-radius: 0 0 5px 5px;
    gap: 10px;
    background: darkslategray;
    outline: 1px dotted black;
    padding: 10px;
    align-items: stretch;
    white-space: nowrap;
    line-height: normal;
    z-index: 1000;
}
.top-bar-row {
    align-items: center;
    display: flex;
    flex-wrap: nowrap;
    gap: 10px;
    min-width: 0;
    overflow: auto;
    padding: 0 10px;
}
.top-bar-info h1 {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    color: white;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.top-bar-info h2 {
    flex: 0 0 auto;
}
.top-bar-controls {
    flex: 0 0 auto;
    background: #3e6868;
    padding: 10px;
    border-radius: 20px;
}
.top-bar .atlas-switch {
    margin-left: auto;
}
.top-bar h1 {
    font-size: 100%;
    margin: 0;
    padding: 0;
}
.top-bar h2 {
    font-size: 70%;
    background: lightblue;
    border-radius: 20px;
    margin: 0;
    padding: 5px;
}
.batch-size-control {
    align-items: center;
    background: lightblue;
    border-radius: 10px;
    font-size: 70%;
    display: inline-flex;
    gap: 6px;
    padding: 3px 5px;
}
.batch-size-control input {
    width: 72px;
}
.qc-filter-menu {
    flex: 0 0 auto;
    position: relative;
}
.qc-filter-menu summary {
    cursor: pointer;
    display: block;
    list-style: none;
    min-width: 110px;
}
.qc-filter-menu summary::-webkit-details-marker {
    display: none;
}
.qc-filter-menu summary:hover {
    box-shadow: 0 0 10px 2px rgba(120, 120, 120, 0.25);
}
.qc-filter-options {
    background: white;
    border: 1px solid rgb(120, 120, 120);
    border-radius: 6px;
    top: 85px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
    color: black;
    display: grid;
    font-size: 70%;
    gap: 14px;
    grid-auto-columns: max-content;
    grid-auto-flow: column;
    max-width: calc(100vw - 20px);
    min-width: 150px;
    overflow: auto;
    padding: 10px;
    position: fixed;
    z-index: 2100;
}
.qc-filter-options input {
    margin: 0;
}
.qc-filter-column {
    align-content: start;
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.qc-filter-column label {
    align-items: center;
    display: flex;
    gap: 6px;
    white-space: nowrap;
}
.qc-filter-heading {
    border-bottom: 1px solid rgb(210, 210, 210);
    font-weight: bold;
    padding-bottom: 4px;
}
.atlas-switch {
    cursor: pointer;
}
.atlas-switch input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
}
.atlas-switch-track {
    align-items: center;
    background: rgb(245, 245, 245);
    border-radius: 999px;
    box-sizing: border-box;
    display: inline-flex;
    height: 30px;
    padding: 3px;
    position: relative;
    width: 132px;
}
.atlas-switch-track::before {
    background: lightblue;
    border-radius: 999px;
    content: "";
    height: 24px;
    left: 3px;
    position: absolute;
    top: 3px;
    transition: transform 140ms ease;
    width: 63px;
}
.atlas-switch-text {
    color: black;
    font-size: 70%;
    line-height: 1;
    position: relative;
    text-align: center;
    width: 50%;
    z-index: 1;
}
.atlas-switch input:not(:checked) + .atlas-switch-track::before {
    transform: translateX(63px);
    background: rgb(255, 180, 100);
}

/* Subject sections */
.subject-section {
    border-radius: 8px;
    box-sizing: border-box;
    background: var(--qc-bg);
    outline: 1px dotted var(--qc-border);
    margin: 10px 10px 0 10px;
    overflow-x: auto;
}
.subject-section[data-status]:not([data-status="Unreviewed"]) {
    outline: none;
    box-shadow: 0 0 18px 3px var(--qc-bg);
}
.subject-header {
    display: flex;
    align-items: center;
    text-wrap: nowrap;
    height: 25px;
    justify-content: end;
    gap: 10px;
    padding: 10px;
    background: darkslategray;
    outline: 1px solid black;
    border-radius: 5px;
    margin: 5px;
    overflow-x: auto;
    overflow-y: hidden;
}
.subject-header h1 {
    align-items: center;
    display: flex;
    gap: 6px;
    font-size: 100%;
    margin: 0 auto 0 0;
    color: white;
    padding: 0;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.subject-name-text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.qc-flag, .outlier-indicator, .subject-index {
    font-size: 70%;
    background: var(--qc-bg);
    border: 1px solid var(--qc-border);
    border-radius: 20px;
    flex: 0 0 auto;
    margin: 0;
    padding: 5px;
}
.subject-index {
    --qc-bg: lightblue;
    --qc-border: rgb(120, 170, 190);
}
.image-row {
    overflow-x: auto;
    overflow-y: hidden;
    font-size: 0;
    outline: 1px solid black;
    border-radius: 5px;
    margin: 5px;
}
.image-row img {
    cursor: zoom-in;
    min-height: 1px;
    opacity: 0;
    vertical-align: top;
}
.image-row img.loaded {
    opacity: 1;
}
.atlas-class {
    border-radius: 8px;
    overflow-x: auto;
    overflow-y: hidden;
    position: sticky;
    top: 160px;
    outline: 2px dotted navy;
    margin: 10px;
    line-height: 0;
    font-size: 0;
}
.atlas-class img.loaded {
    opacity: 0.5;
}
.legend {
    list-style: none;
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    padding: 8px 10px;
    font-size: 70%;
    background: rgba(255, 255, 255, 0.72);
    border-radius: 5px;
    overflow-x: auto;
    width: fit-content;
    gap: 12px;
}
.legend li, .legend label {
    display: flex;
    align-items: center;
    gap: 6px;
}
.legend label {
    font-weight: bold;
}
.color-dot {
    border-radius: 50%;
    display: inline-block;
    height: 10px;
    width: 10px;
}

/* QC controls */
.qc-control-panel {
    outline: 1px solid black;
    border-radius: 5px;
    margin: 5px;
    background: #3e6868;
    overflow-x: auto;
    padding: 10px 0 0 0;
}
.qc-input, .qc-reason-row {
    display: flex;
    padding: 0 10px 10px 10px;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    flex-wrap: wrap;
}
.qc-status-buttons {
    display: flex;
    gap: 4px;
    flex: 1;
    padding: 5px;
    overflow-x: auto;
    width: 100%;
}
.qc-status-button {
    border: 0;
    background: var(--qc-bg);
    border-radius: 4px;
    cursor: pointer;
    flex: 1;
    font-size: 100%;
    padding: 4px;
    min-width: 0;
    text-shadow:
        -1px -1px 0 black,
         1px -1px 0 black,
        -1px  1px 0 black,
         1px  1px 0 black;
}
.qc-status-button:hover {
    box-shadow: 0 0 5px 2px var(--qc-bg);
}
.qc-status-button.selected {
    box-shadow: 0 0 5px 3px var(--qc-bg);
}
.qc-reason {
    flex: 1;
    min-width: 220px;
}
.qc-reason-select {
    min-width: 190px;
}

/* Bottom toolbar */
.bottom-toolbar {
    position: sticky;
    bottom: 0;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: nowrap;
    overflow-x: auto;
    border-radius: 5px 5px 0 0;
    outline: 1px dotted black;
    padding: 10px;
    background: darkslategray;
    z-index: 1000;
}
.bottom-toolbar select, .bottom-toolbar button, .qc-filter-menu summary, .qc-reason, .qc-reason-select {
    background: rgb(245, 245, 245);
    border: 0;
    border-radius: 4px;
    box-sizing: border-box;
    font-size: 70%;
    padding: 4px 8px;
}
.bottom-toolbar select, .qc-reason, .qc-reason-select {
    appearance: none;
    color: black;
}
.bottom-toolbar button {
    flex: 0 0 auto;
    min-width: 36px;
    cursor: pointer;
}
.bottom-toolbar select:hover, .qc-reason:hover, .qc-reason-select:hover {
    box-shadow: 0 0 10px 2px rgba(120, 120, 120, 0.25);
}
.bottom-toolbar select:focus, .qc-reason:focus, .qc-reason-select:focus {
    box-shadow: none;
    outline: none;
}
.bottom-toolbar .save-qc:focus, .bottom-toolbar .clear-qc:focus, .bottom-toolbar .help-qc:focus, .bottom-toolbar .nav-qc:focus {
    box-shadow: none;
    outline: none;
}
.bottom-toolbar button:hover {
    box-shadow: 0 0 10px 2px rgba(120, 120, 120, 0.25);
}
.bottom-toolbar .save-qc {
    background: lightblue;
}
.bottom-toolbar .save-qc:hover {
    box-shadow: 0 0 10px 2px rgba(120, 170, 190, 0.5);
}
.bottom-toolbar .clear-qc {
    background: rgb(255, 180, 100);
}
.bottom-toolbar .clear-qc:hover {
    box-shadow: 0 0 10px 2px rgba(240, 100, 10, 0.4);
}
.bottom-toolbar .help-qc {
    align-items: center;
    aspect-ratio: 1;
    border-radius: 50%;
    display: inline-flex;
    justify-content: center;
    margin-left: auto;
    background: #e6e641;
    min-width: 25px;
    width: 25px;
}
.bottom-toolbar .help-qc:hover, .bottom-toolbar .nav-qc:hover {
    box-shadow: 0 0 10px 2px rgba(245, 245, 245, 0.55);
}
.bottom-toolbar .previous-qc {
    margin-left: auto;
}
.bottom-toolbar .help-qc + .previous-qc {
    margin-left: 0;
}
.bottom-toolbar .nav-qc:disabled {
    cursor: default;
    opacity: 0.45;
}
.bottom-toolbar .nav-qc:disabled:hover {
    box-shadow: none;
}

/* Overlays */
.image-preview {
    position: fixed;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.82);
    padding: 40px;
    box-sizing: border-box;
    z-index: 2000;
}
.image-preview img {
    max-width: 96vw;
    max-height: 92vh;
    object-fit: contain;
    background: black;
}
.image-preview button {
    position: absolute;
    top: 12px;
    right: 12px;
    border: 1px solid rgb(220, 220, 220);
    background: white;
    border-radius: 4px;
    cursor: pointer;
    font-size: 150%;
    line-height: 1;
    padding: 4px 10px;
}
.csv-load-prompt {
    align-items: center;
    background: rgba(0, 0, 0, 0.72);
    box-sizing: border-box;
    display: none;
    inset: 0;
    justify-content: center;
    padding: 24px;
    position: fixed;
    z-index: 1800;
}
.csv-load-dialog {
    background: white;
    border: 1px solid rgb(80, 80, 80);
    border-radius: 8px;
    box-shadow: 0 12px 36px rgba(0, 0, 0, 0.32);
    box-sizing: border-box;
    color: black;
    padding: 18px;
    width: min(620px, 96vw);
}
.csv-load-dialog h2 {
    font-size: 100%;
    margin: 0 0 10px 0;
}
.csv-load-dialog p {
    font-size: 100%;
    margin: 0 0 12px 0;
}
.csv-load-dialog code {
    background: rgb(245, 245, 245);
    border: 1px solid rgb(190, 190, 190);
    border-radius: 4px;
    box-sizing: border-box;
    display: block;
    font-size: 70%;
    margin: 0 0 12px 0;
    overflow-wrap: anywhere;
    padding: 10px;
    white-space: pre-wrap;
}
.csv-load-message {
    color: rgb(150, 40, 0);
}
.csv-load-dialog button {
    background: lightblue;
    border: 0;
    border-radius: 4px;
    cursor: pointer;
    font-size: 100%;
    padding: 8px 12px;
}
.csv-load-dialog button:hover {
    box-shadow: 0 0 10px 2px rgba(120, 170, 190, 0.5);
}
"""

## serve_visual_qc.py calls the CSV state, load, save and queueImageLoad functions by name:
JS = r"""
const col = Object.fromEntries(csvColumns.map((column, i) => [column, i]));
const csvFileTypes = [{ description: "CSV file", accept: { "text/csv": [".csv"] } }];
const embeddedCSVRows = normalizeCSVRows(parseCSV(embeddedQCCSV));
const imageLoadQueue = [];
const maxActiveImageLoads = 12;
let activeImageLoads = 0;
let activeBatchIndex = 0;
let activeBatchSize = normalizeBatchSize(defaultBatchSize);
let hasUnsavedChanges = false;
let hasLoadedQCCSV = false;
let qcCSVFileHandle = null;
let qcCSVRows = [];
let qcRowsBySubject = {};
let qcCache = {};

function setDirty(isDirty) {
    hasUnsavedChanges = isDirty;
}

function makeElement(tag, attrs={}, text=null) {
    let el = document.createElement(tag);
    for (let [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    if (text !== null) el.textContent = text;
    return el;
}

// QC CSV state, edited in place:

function csvEscape(value) {
    value = String(value);
    return /[",\n\r]/.test(value) ? '"' + value.replace(/"/g, '""') + '"' : value;
}

function parseCSV(text) {
    let rows = [];
    let row = [];
    let value = "";
    let inQuotes = false;

    for (let i = 0; i < text.length; i++) {
        let char = text[i];
        let nextChar = text[i + 1];

        if (char === '"') {
            if (inQuotes && nextChar === '"') {
                value += '"';
                i++;
            } else {
                inQuotes = !inQuotes;
            }
        } else if (char === "," && !inQuotes) {
            row.push(value);
            value = "";
        } else if ((char === "\n" || char === "\r") && !inQuotes) {
            if (char === "\r" && nextChar === "\n") i++;
            row.push(value);
            rows.push(row);
            row = [];
            value = "";
        } else {
            value += char;
        }
    }

    if (value !== "" || row.length > 0) {
        row.push(value);
        rows.push(row);
    }
    return rows;
}

function normalizeCSVRows(rows) {
    let header = rows[0].map(value => value.trim());
    let indexes = csvColumns.map(column => header.indexOf(column));
    let dataRows = rows.slice(1).filter(row => row.some(value => value !== ""));
    return [csvColumns.slice(), ...dataRows.map(row => indexes.map(i => row[i] ?? ""))];
}

function isDatasetRow(row) {
    return [col.subjectID, col.qc, col.reason, col.outlier].every(i => row[i].trim() === "NA");
}

function validateFullCSVRows(rows, csvSource) {
    let header = (rows[0] || []).map(value => value.trim());
    let missingColumns = csvColumns.filter(column => !header.includes(column));
    if (missingColumns.length > 0) return `${csvSource} is missing required columns: ${missingColumns.join(", ")}`;

    let dataRows = normalizeCSVRows(rows).slice(1);
    let embeddedDataRows = embeddedCSVRows.slice(1);
    if (dataRows.length !== embeddedDataRows.length) return `${csvSource} has ${dataRows.length} rows, but this HTML expects ${embeddedDataRows.length} rows for ${document.title}.`;
    if (dataRows.some(row => row[col.figure_name] !== document.title)) return `${csvSource} does not match this page title (${document.title}).`;
    if (dataRows.some(row => row[col.dataset] !== datasetName)) return `${csvSource} does not match this dataset (${datasetName}).`;

    let expectedSubjects = new Set(subjectManifest.map(entry => entry[0]));
    let seenSubjects = new Set();
    for (let row of dataRows.filter(row => !isDatasetRow(row))) {
        let subject = row[col.subjectID];
        if (!expectedSubjects.has(subject)) return `${csvSource} has an unexpected subjectID (${subject}).`;
        if (seenSubjects.has(subject)) return `${csvSource} has a duplicate subjectID (${subject}).`;
        seenSubjects.add(subject);
    }

    let datasetRows = dataRows.filter(isDatasetRow).length;
    let expectedDatasetRows = embeddedDataRows.filter(isDatasetRow).length;
    if (datasetRows !== expectedDatasetRows) return `${csvSource} has ${datasetRows} dataset rows, but this HTML expects ${expectedDatasetRows}.`;
    return "";
}

function setFullCSVRows(rows) {
    qcCSVRows = normalizeCSVRows(rows);
    qcRowsBySubject = {};
    for (let row of qcCSVRows.slice(1)) {
        if (isDatasetRow(row)) continue;
        if (!Object.hasOwn(qcStatuses, row[col.qc])) row[col.qc] = "Unreviewed";
        qcRowsBySubject[row[col.subjectID]] = row;
    }
}

function applyProgressRowsFromCSV(rows, csvSource, markClean=false) {
    for (let row of rows.slice(1)) {
        let cached = qcCache[row[col.subjectID]];
        if (!cached || isDatasetRow(row)) continue;
        cached.reasonSelect.value = row[col.reason];
        cached.commentInput.value = row[col.comment];
        updateHighlight(row[col.subjectID]);
    }
    applyStatusFilters();
    console.info("Loaded QC state from " + csvSource);
    if (markClean) setDirty(false);
}

function applyDatasetCommentFromRows(rows) {
    let input = document.getElementById("dataset-comment");
    let row = rows.slice(1).find(isDatasetRow);
    if (input && row) input.value = row[col.comment];
}

function saveDatasetCommentUI(input) {
    qcCSVRows.slice(1).find(isDatasetRow)[col.comment] = input.value;
    setDirty(true);
}

function updateSubject(subject, column, value) {
    let row = qcRowsBySubject[subject];
    row[col[column]] = value;
    if (column === "reason" && value !== "" && row[col.qc] === "Unreviewed") row[col.qc] = "FLAG";
    updateHighlight(subject);
    applySubjectFilter(subject);
    updateStatusFilterSummary();
    setDirty(true);
}

function clearSheet() {
    if (!confirm("Clear manual QC for this figure and restore original missing values and automatic outlier comments?")) return;
    setFullCSVRows(embeddedCSVRows);
    applyProgressRowsFromCSV(qcCSVRows, "embedded QC CSV");
    applyDatasetCommentFromRows(qcCSVRows);
    document.getElementById("fill-empty-values").value = "";
    setDirty(true);
}

function buildCSVContent() {
    let fillWith = document.getElementById("fill-empty-values").value;
    if (fillWith !== "") {
        for (let row of Object.values(qcRowsBySubject)) {
            if (row[col.qc] === "Unreviewed") row[col.qc] = fillWith;
        }
        for (let subject in qcCache) updateHighlight(subject);
        applyStatusFilters();
    }
    return qcCSVRows.map(row => row.map(csvEscape).join(",")).join("\n");
}

// Load and save the designated CSV:

function getVisualQCCSVPath() {
    return document.querySelector('meta[name="qc-csv-path"]').content;
}

function setQCControlsEnabled(isEnabled) {
    for (let el of document.querySelectorAll(".qc-status-button, .qc-reason-select, .qc-reason, #fill-empty-values, .save-qc, .clear-qc")) el.disabled = !isEnabled;
}

function showRequiredCSVPrompt(message="") {
    let prompt = document.getElementById("csv-load-prompt");
    document.getElementById("csv-load-message").textContent = message;
    prompt.style.display = "flex";
    prompt.setAttribute("aria-hidden", "false");
}

function hideRequiredCSVPrompt() {
    let prompt = document.getElementById("csv-load-prompt");
    prompt.style.display = "none";
    prompt.setAttribute("aria-hidden", "true");
}

async function readCSVFromUser() {
    if (window.showOpenFilePicker) {
        try {
            let [fileHandle] = await window.showOpenFilePicker({ id: "visual-qc-csv", types: csvFileTypes });
            let file = await fileHandle.getFile();
            return { text: await file.text(), name: file.name, fileHandle };
        } catch (err) {
            if (err.name === "AbortError") return null;
            console.warn("File picker load failed; falling back to file input.", err);
        }
    }
    return new Promise(resolve => {
        let input = makeElement("input", { type: "file", accept: ".csv,text/csv" });
        input.addEventListener("change", async () => resolve({ text: await input.files[0].text(), name: input.files[0].name, fileHandle: null }));
        input.addEventListener("cancel", () => resolve(null));
        input.click();
    });
}

async function loadRequiredQCCSV() {
    let loaded = await readCSVFromUser();
    let rows = loaded ? parseCSV(loaded.text) : [];
    let validationError = loaded ? validateFullCSVRows(rows, loaded.name) : "";
    if (validationError) alert(validationError);
    if (!loaded || validationError) {
        showRequiredCSVPrompt("CSV not loaded. Choose the designated file to begin QC.");
        return false;
    }

    qcCSVFileHandle = loaded.fileHandle;
    hasLoadedQCCSV = true;
    setFullCSVRows(rows);
    applyProgressRowsFromCSV(qcCSVRows, loaded.name, true);
    applyDatasetCommentFromRows(qcCSVRows);
    setQCControlsEnabled(true);
    hideRequiredCSVPrompt();
    return true;
}

async function writeCSVFile(fileHandle, csvContent) {
    let writable = await fileHandle.createWritable();
    await writable.write(csvContent);
    await writable.close();
    setDirty(false);
}

async function saveCSV() {
    if (!hasLoadedQCCSV) {
        alert("Load the designated QC CSV before saving.");
        return false;
    }

    setQCControlsEnabled(false);
    try {
        let csvContent = buildCSVContent();
        let fileName = getVisualQCCSVPath().split(/[\\/]/).pop();
        if (qcCSVFileHandle) {
            try {
                await writeCSVFile(qcCSVFileHandle, csvContent);
                alert("QC saved successfully: " + qcCSVFileHandle.name);
                return true;
            } catch (err) {
                console.warn("Could not write to the current CSV file; opening Save dialog instead.", err);
            }
        }
        if (window.showSaveFilePicker) {
            try {
                qcCSVFileHandle = await window.showSaveFilePicker({ id: "visual-qc-csv", suggestedName: fileName, startIn: "downloads", types: csvFileTypes });
                await writeCSVFile(qcCSVFileHandle, csvContent);
                alert("QC CSV saved successfully.");
                return true;
            } catch (err) {
                if (err.name === "AbortError") return false;
                console.warn("File System Access API save failed; falling back to download.", err);
            }
        }

        let link = makeElement("a", { href: URL.createObjectURL(new Blob([csvContent], { type: "text/csv;charset=utf-8;" })), download: fileName });
        link.click();
        URL.revokeObjectURL(link.href);
        setDirty(false);
        alert("QC CSV saved successfully.");
        return true;
    } finally {
        setQCControlsEnabled(true);
    }
}

// Image loading queue:

function pumpImageQueue() {
    while (activeImageLoads < maxActiveImageLoads && imageLoadQueue.length > 0) {
        let img = imageLoadQueue.shift();
        let finish = () => {
            activeImageLoads--;
            pumpImageQueue();
        };
        activeImageLoads++;
        img.addEventListener("load", finish, { once: true });
        img.addEventListener("error", finish, { once: true });
        img.src = img.dataset.src;
        img.removeAttribute("data-src");
        img.classList.add("loaded");
    }
}

function queueImageLoad(img) {
    if (!img.dataset.src || img.dataset.queued) return;
    img.dataset.queued = "true";
    img.fetchPriority = "high";
    imageLoadQueue.push(img);
    pumpImageQueue();
}

function loadVisibleImages() {
    for (let img of document.querySelectorAll("img[data-src]")) {
        if (img.closest(".snap")?.style.display !== "none") queueImageLoad(img);
    }
}

function openImagePreview(img) {
    let overlay = document.getElementById("image-preview");
    let preview = document.getElementById("image-preview-img");
    queueImageLoad(img);
    preview.src = img.currentSrc || img.src || img.dataset.src || "";
    preview.alt = img.alt;
    overlay.style.display = "flex";
    overlay.setAttribute("aria-hidden", "false");
}

function closeImagePreview() {
    let overlay = document.getElementById("image-preview");
    overlay.style.display = "none";
    overlay.setAttribute("aria-hidden", "true");
    document.getElementById("image-preview-img").src = "";
}

// Filters and atlas overlay:

function subjectFilterAllows(subject) {
    let statuses = [...document.querySelectorAll(".qc-status-filter:checked")].map(input => input.value);
    let outlierFilter = document.getElementById("qc-outlier-filter");
    return (statuses.length === 0 || statuses.includes(qcRowsBySubject[subject][col.qc])) && !(outlierFilter?.checked && !qcCache[subject].isOutlier);
}

function applySubjectFilter(subject) {
    let cached = qcCache[subject];
    let isVisible = subjectFilterAllows(subject);
    cached.snap.style.display = isVisible ? "" : "none";
    cached.blank.style.display = isVisible && document.getElementById("atlas-mode")?.checked ? "" : "none";
}

function updateStatusFilterSummary() {
    let summary = document.getElementById("qc-filter-summary");
    if (!document.querySelector(".qc-status-filter:checked, #qc-outlier-filter:checked")) {
        summary.textContent = "Filter By";
        return;
    }
    let count = Object.keys(qcCache).filter(subjectFilterAllows).length;
    summary.textContent = `Filter: ${count} ${count === 1 ? "subject" : "subjects"}`;
}

function applyStatusFilters() {
    for (let subject in qcCache) applySubjectFilter(subject);
    updateStatusFilterSummary();
    loadVisibleImages();
}

function setAtlasMode(showAtlas) {
    document.getElementById("atlas").style.display = showAtlas ? "" : "none";
    for (let blank of document.getElementsByClassName("blank")) blank.style.display = showAtlas ? "" : "none";
    document.body.style.marginBottom = showAtlas ? "100vh" : "0";
    applyStatusFilters();
}

function toggleAtlas(el) {
    let anchor = null;
    for (let section of document.querySelectorAll("#subjects-container .subject-section")) {
        let rect = section.getBoundingClientRect();
        if ((rect.width > 0 || rect.height > 0) && rect.top >= 0 && rect.top < window.innerHeight) {
            anchor = { element: section, offset: rect.top - rect.height };
            break;
        }
    }
    setAtlasMode(el.checked);
    if (anchor) requestAnimationFrame(() => window.scrollBy(0, anchor.element.getBoundingClientRect().top - anchor.offset));
}

// Subject batches and page navigation:

function updateHighlight(subject) {
    let cached = qcCache[subject];
    let status = qcRowsBySubject[subject][col.qc];
    cached.section.dataset.status = status;
    cached.flag.textContent = status;
    for (let button of cached.buttons) {
        button.classList.toggle("selected", button.dataset.status === status);
        button.setAttribute("aria-pressed", button.dataset.status === status);
    }
}

function renderSubject([subject, pattern, figures, isOutlier], index) {
    let row = qcRowsBySubject[subject];
    let blank = makeElement("div", { class: "blank" });
    let snap = makeElement("div", { class: "snap", id: subject });
    let section = makeElement("div", { class: "subject-section" });
    let header = makeElement("div", { class: "subject-header" });
    let title = makeElement("h1", { title: subject });
    let flag = makeElement("h2", { class: "qc-flag" });
    blank.appendChild(makeElement("div", { class: "snap" }));
    title.appendChild(makeElement("span", { class: "subject-name-text" }, subject));
    header.appendChild(title);
    if (isOutlier) header.appendChild(makeElement("h2", { class: "outlier-indicator", title: "Automatically detected outlier" }, "‼️ Outlier ‼️"));
    header.append(flag, makeElement("h2", { class: "subject-index" }, `${index + 1}/${subjectManifest.length}`));
    section.appendChild(header);

    if (figures.length === 0) {
        let error = makeElement("div", { class: "error" });
        error.appendChild(makeElement("p", {}, "No figures exist: " + pattern));
        section.appendChild(error);
    } else {
        let imageRow = makeElement("div", { class: "image-row" });
        for (let figure of figures) {
            let img = makeElement("img", { "data-src": figure, decoding: "async", alt: figure });
            img.addEventListener("click", () => openImagePreview(img));
            imageRow.appendChild(img);
        }
        section.appendChild(imageRow);
    }

    let buttons = makeElement("div", { class: "qc-status-buttons" });
    for (let [status, [icon, tooltip]] of Object.entries(qcStatuses)) {
        let button = makeElement("button", { type: "button", class: "qc-status-button", title: tooltip, "data-status": status }, icon);
        button.addEventListener("click", () => updateSubject(subject, "qc", status));
        buttons.appendChild(button);
    }
    let reasonSelect = makeElement("select", { class: "qc-reason-select", title: "Choose a reason for the QC decision" });
    reasonSelect.appendChild(makeElement("option", { value: "" }, "--QC Reason--"));
    for (let reason of qcReasonOptions) reasonSelect.appendChild(makeElement("option", { value: reason }, reason));
    reasonSelect.value = row[col.reason];
    reasonSelect.addEventListener("change", () => updateSubject(subject, "reason", reasonSelect.value));
    let commentInput = makeElement("input", { type: "text", placeholder: "Comment ...", class: "qc-reason" });
    commentInput.value = row[col.comment];
    commentInput.addEventListener("input", () => updateSubject(subject, "comment", commentInput.value));

    let qcInput = makeElement("div", { class: "qc-input" });
    let reasonRow = makeElement("div", { class: "qc-reason-row" });
    let controlPanel = makeElement("div", { class: "qc-control-panel" });
    qcInput.appendChild(buttons);
    reasonRow.append(reasonSelect, commentInput);
    controlPanel.append(qcInput, reasonRow);
    section.appendChild(controlPanel);
    snap.appendChild(section);

    qcCache[subject] = { isOutlier, blank, snap, section, flag, reasonSelect, commentInput, buttons: [...buttons.children] };
    updateHighlight(subject);
    return [blank, snap];
}

function getBatchCount() {
    return Math.max(1, Math.ceil(subjectManifest.length / activeBatchSize));
}

function normalizeBatchSize(value) {
    let size = parseInt(value, 10);
    return Math.min(Math.max(subjectManifest.length, 1), size > 0 ? size : defaultBatchSize);
}

function updateBatchControls() {
    let batchCount = getBatchCount();
    let batchSize = document.getElementById("batch-size");
    document.getElementById("batch-count").textContent = `Batch - ${activeBatchIndex + 1}/${batchCount}`;
    batchSize.value = activeBatchSize;
    batchSize.max = Math.max(subjectManifest.length, 1);
    for (let [direction, label, arrow, isEdge] of [["previous", "Prev", "<<", activeBatchIndex === 0], ["next", "Next", ">>", activeBatchIndex === batchCount - 1]]) {
        let button = document.getElementById(`${direction}-qc-button`);
        button.textContent = isEdge ? label : arrow;
        button.title = isEdge ? `Change to ${direction} QC figure` : `Load ${direction} subject batch`;
        button.disabled = isEdge && !button.dataset.href;
    }
}

function renderBatch(batchIndex) {
    let container = document.getElementById("subjects-container");
    let atlasMode = document.getElementById("atlas-mode");
    activeBatchIndex = Math.max(0, Math.min(getBatchCount() - 1, batchIndex));
    let start = activeBatchIndex * activeBatchSize;
    imageLoadQueue.length = 0;
    qcCache = {};

    if (subjectManifest.length === 0) {
        let empty = makeElement("div", { class: "subject-section" });
        empty.appendChild(makeElement("div", { class: "subject-header" }, "No subjects"));
        container.replaceChildren(empty);
    } else {
        container.replaceChildren(...subjectManifest.slice(start, start + activeBatchSize).flatMap((entry, i) => renderSubject(entry, start + i)));
    }

    updateBatchControls();
    if (atlasMode) setAtlasMode(atlasMode.checked);
    else applyStatusFilters();
    setQCControlsEnabled(hasLoadedQCCSV);
    window.scrollTo(0, 0);
}

function setBatchSizeFromInput() {
    let firstSubject = activeBatchIndex * activeBatchSize;
    activeBatchSize = normalizeBatchSize(document.getElementById("batch-size").value);
    renderBatch(Math.floor(firstSubject / activeBatchSize));
}

async function navigate(step) {
    let batchIndex = activeBatchIndex + step;
    if (batchIndex >= 0 && batchIndex < getBatchCount()) {
        renderBatch(batchIndex);
        return;
    }

    let href = document.getElementById(step < 0 ? "previous-qc-button" : "next-qc-button").dataset.href;
    if (!href) return;
    if (hasUnsavedChanges) {
        if (confirm("Save changes to CSV before leaving this page?")) await saveCSV();
        else if (confirm("Leave without saving changes?")) setDirty(false);
        if (hasUnsavedChanges) return;
    }
    window.location.href = href;
}

document.addEventListener("keydown", event => {
    if (event.shiftKey && event.code === "F2") hideRequiredCSVPrompt();
    if (event.code === "Escape") closeImagePreview();
});

window.addEventListener("beforeunload", event => {
    if (!hasUnsavedChanges) return;
    event.preventDefault();
    event.returnValue = "";
});

window.onload = () => {
    setFullCSVRows(embeddedCSVRows);
    renderBatch(0);
    showRequiredCSVPrompt();
};
"""


if __name__ == "__main__":
    main()
