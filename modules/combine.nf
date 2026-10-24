// Combine per-(subject, bundle) tractometry outputs into group-level
// spreadsheets: MeTA profiles, BUAN profiles, bundle shape metrics, and
// atlas-similarity scores.

process AGGREGATE_META_PROFILES {

    label 'medium'
    label 'python_tools'

    tag "${dataset}_${metric}_${roi}"

    publishDir "${params.output_dir}/spreadsheets/MeTA", mode: 'link', overwrite: true

    input:
    tuple val(metric), val(roi), path(csvs)
    val  dataset

    output:
    path "${dataset}_MeTA_${metric}_local_${roi}.csv", emit: combined

    script:
    """
    printf '%s\\n' ${csvs} > _aggregate_inputs.txt
    
    combine_metrics.py \\
        --mode meta_profile \\
        --metrics "${metric}" \\
        --prefix "${dataset}" \\
        --inputs _aggregate_inputs.txt \\
        --output_dir ./
    """
}

// Bundle shape metrics
process AGGREGATE_SHAPE_METRICS {

    label 'medium'
    label 'python_tools'

    tag "${dataset}"

    publishDir "${params.output_dir}/spreadsheets/MeTA", mode: 'link', overwrite: true

    input:
    path csvs
    val  dataset

    output:
    path "${dataset}_shape_metrics.csv", emit: combined

    script:
    """
    printf '%s\\n' ${csvs} > _aggregate_inputs.txt

    combine_metrics.py \\
        --mode shape \\
        --prefix "${dataset}" \\
        --inputs _aggregate_inputs.txt \\
        --output_dir ./
    """
}

process AGGREGATE_BUAN_PROFILES {

    label 'medium'
    label 'python_tools'

    tag "${dataset}_${metric}"

    publishDir "${params.output_dir}/spreadsheets/BUAN", mode: 'link', overwrite: true

    input:
    tuple val(metric), path(csvs)
    val  dataset

    output:
    path "${dataset}_BUAN_${metric}.csv", emit: combined

    script:
    """
    printf '%s\\n' ${csvs} > _aggregate_inputs.txt

    combine_metrics.py \\
        --mode buan_profile \\
        --metrics "${metric}" \\
        --prefix "${dataset}" \\
        --inputs _aggregate_inputs.txt \\
        --output_dir ./
    """
}


process CHECK_RUN_COMPLETENESS {

    label 'minimal'
    label 'python_tools'

    tag "${dataset}"

    publishDir "${params.output_dir}/spreadsheets", mode: 'link', overwrite: true

    input:
    val  wait_for_segmentation
    val  wait_for_meta
    val  wait_for_buan
    val  output_dir
    path input_csv
    path bundle_list
    val  dataset

    output:
    path "${dataset}_run_completeness.csv", emit: report
    path "${dataset}_run_completeness.txt", emit: summary

    script:
    """
    check_run_completeness.py \\
        --output_dir "${output_dir}" \\
        --subjects   "${input_csv}" \\
        --bundles    "${bundle_list}" \\
        --dataset    "${dataset}" \\
        --output_csv "./${dataset}_run_completeness.csv" \\
        --output_txt "./${dataset}_run_completeness.txt"
    """
}

// Atlas-similarity scores
process AGGREGATE_SIMILARITY {

    label 'medium'
    label 'python_tools'

    tag "${dataset}"

    publishDir "${params.output_dir}/spreadsheets/similarity", mode: 'link', overwrite: true

    input:
    tuple val(percentile), path(csvs)
    val  dataset

    output:
    path "${dataset}_similarity${percentile}.csv", emit: combined

    script:
    """
    printf '%s\\n' ${csvs} > _aggregate_inputs.txt

    combine_metrics.py \\
        --mode similarity \\
        --prefix "${dataset}" \\
        --inputs _aggregate_inputs.txt \\
        --output_dir ./
    """
}
