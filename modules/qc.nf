process RENDER_MODEL_QC {

    label 'minimal'
    label 'python_tools'

    tag "${subject}"

    storeDir "${params.output_dir}/subjects/${subject}/qc_images/maps"

    input:
    tuple val(subject), path(qa_mni), path(metric_maps)
    path cortical_atlas_mni

    output:
    tuple val(subject), path("${subject}*.png"), emit: model_qc_pngs
    path "${subject}.done",                      emit: done

    script:
    """
    # render registration images
    slice_figs.py \\
        --image ${qa_mni} \\
        --atlas ${cortical_atlas_mni} \\
        --output_file "./${subject}_QA-MNI_HCP-MMP.png"

    # render diffusion images
    for map in ${metric_maps}; do
        slice_figs.py \\
            --image "\${map}" \\
            --output_file "\$(basename \${map}).png"
    done

    # Completion marker:
    if ls ${subject}*.png >/dev/null 2>&1; then
        touch ${subject}.done
    else
        echo "Warning: RENDER_MODEL_QC produced no PNGs for ${subject}" >&2
    fi
    """
}

process BUILD_DIFFUSION_HTML {

    label 'minimal'
    label 'python_tools'

    tag "${group}"

    publishDir "${params.output_dir}/QC_HTML/maps", mode: "link", overwrite: true

    input:
    val wait_for
    val out_dir
    val metrics
    path subjects
    val group

    output:
    path "*.html", emit: html
    path "*.csv", optional: true, emit: csv

    script:
    """
    maps=${metrics}
    # make HTML pages for diffusion metrics
    build_html.py \\
        --output "./" \\
        --group ${group} \\
        --rel_dir "${out_dir}/QC_HTML/maps" \\
        --figures_path_format "${out_dir}/subjects/{subject}/qc_images/maps/{subject}.odf.gqi.fz.{figure_name}.nii.gz.png" \\
        --figure_names \${maps//,/ } --subjects ${subjects} -n 1 --aspect_ratio 2.9

    # make HTML page for registration
    build_html.py \\
        --output "./" \\
        --group ${group} \\
        --rel_dir "${out_dir}/QC_HTML/maps" \\
        --figures_path_format "${out_dir}/subjects/{subject}/qc_images/maps/{subject}_QA-MNI_HCP-MMP.png" \\
        --figure_names registration --subjects ${subjects} -n 1 --aspect_ratio 2.9

    """
}

process BUILD_META_HTML {

    label 'minimal'
    label 'python_tools'

    tag "${group}"

    publishDir "${params.output_dir}/QC_HTML/meta", mode: "link", overwrite: true

    input:
    val wait_for
    val out_dir
    val bundle_list
    path subjects
    val group

    output:
    path "*.html", emit: html
    path "*.csv", optional: true, emit: csv

    when:
    bundle_list

    script:
    """
    build_html.py \\
        --output "./" \\
        --group ${group} \\
        --rel_dir "${out_dir}/QC_HTML/meta" \\
        --figures_path_format '${out_dir}/subjects/{subject}/qc_images/meta/{figure_name}/{subject}_{figure_name}_local_core_*.png' \\
        --figure_names ${bundle_list.join(' ')} --subjects ${subjects}

    """
}

process BUILD_BUAN_HTML {

    label 'minimal'
    label 'python_tools'

    tag "${group}"

    publishDir "${params.output_dir}/QC_HTML/buan", mode: "link", overwrite: true

    input:
    val wait_for
    val out_dir
    val bundle_list
    path subjects
    val group

    output:
    path "*.html", emit: html
    path "*.csv", optional: true, emit: csv

    when:
    bundle_list

    script:
    """
    build_html.py \\
        --output "./" \\
        --group ${group} \\
        --rel_dir "${out_dir}/QC_HTML/buan" \\
        --figures_path_format '${out_dir}/subjects/{subject}/qc_images/buan/{figure_name}/tract_{subject}_{figure_name}_*.png' \\
        --figure_names ${bundle_list.join(' ')} --subjects ${subjects}

    """
}

process BUILD_TRACT_HTML {

    label 'minimal'
    label 'python_tools'

    tag "${group}"
    
    publishDir "${params.output_dir}/QC_HTML/tracts", mode: "link", overwrite: true

    input:
    val wait_for
    val wait_for_mean
    val out_dir
    val bundle_list
    path subjects
    val group
    val atlas_bundle_pngs
    path similarity

    output:
    path "*.html", emit: html
    path "*.csv", optional: true, emit: csv

    when:
    bundle_list

    script:
    """
    build_html.py \\
        --output "./" \\
        --group ${group} \\
        --rel_dir "${out_dir}/QC_HTML/tracts" \\
        --mean_path_format '${out_dir}/group_mean/qc_imgs/{figure_name}/${group}_{figure_name}_*.png' \\
        --atlas_path_format '${atlas_bundle_pngs}/{figure_name}/{figure_name}_*.png' \\
        --figures_path_format '${out_dir}/subjects/{subject}/qc_images/tracts/{figure_name}/tract_{subject}_{figure_name}_*.png' \\
        --figure_names ${bundle_list.join(' ')} --subjects ${subjects} \\
        --similarity_metrics ${similarity} --flag_outliers
    """
}
