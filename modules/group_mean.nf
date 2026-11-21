process GROUP_MEAN_BUNDLE {

    label 'light'
    label 'python_tools'

    tag "${bundle}"
    
    storeDir "${params.output_dir}/group_mean"

    input:
    tuple val(bundle), path(density_maps), val(cam_views)
    val cfg
    path qa_mni_template_1mm
    path qa_mni_template_2mm
    val dataset

    output:
    tuple val(bundle), path("mean_density/${dataset}_${bundle}_group_mean.nii.gz"), emit: group_mean_density
    tuple val(bundle), path("qc_imgs/${bundle}/${dataset}_${bundle}_*.png"),        emit: group_mean_pngs

    script:
    """
    # calculate mean dm
    calculate_mean_bundle.py \\
        --density_maps ${density_maps} \\
        --group "${dataset}" \\
        --reference "${qa_mni_template_1mm}" "${qa_mni_template_2mm}" \\
        --bundle "${bundle}"

    # create mesh volumes
    calculate_mean_bundle.py \\
        --mean_dm "${dataset}_${bundle}_group_mean.nii.gz" \\
        --group "${dataset}" \\
        --reference "${qa_mni_template_1mm}" "${qa_mni_template_2mm}" \\
        --bundle "${bundle}" \\
        --percentiles ${cfg.percentiles}

    # Render figures
    mesh=()
    for percentile in ${cfg.percentiles}; do
        mesh+=("${dataset}_${bundle}_iso_surface_\${percentile}th.vtk")
    done
    dive --mesh \${mesh[@]} \\
        --output "${dataset}_${bundle}" \\
        --glass_brain \\
        --mesh_colors ${cfg.colors} \\
        --mesh_opacity ${cfg.alphas} \\
        --cam_view ${cam_views.join(' ')} \\
        --background 1 --mode cli --zoom 1.5

    # Outputs out into storeDir subdirs
    mkdir -p mean_density qc_imgs/${bundle}
    mv ${dataset}_${bundle}_group_mean.nii.gz mean_density/
    mv ${dataset}_${bundle}_*.png qc_imgs/${bundle}/

    """
}

process BUNDLE_METRICS {

    label 'medium'
    label 'python_tools'

    tag "${subject}_${bundle}"

    storeDir "${params.output_dir}/subjects/${subject}"

    input:
    tuple val(subject), val(bundle), path(sbundle), path(maps), path(inverse_warp), path(qa_mni_ref), path(refs_dm)

    output:
    tuple val(subject), val(bundle), path("density_maps/${bundle}/${subject}_${bundle}.nii.gz"),                emit: bundle_mask
    tuple val(subject), val(bundle), path("density_maps/${bundle}/${subject}_${bundle}_density.nii.gz"),        emit: density_map
    tuple val(subject), val(bundle), path("density_maps/${bundle}/mni_${subject}_${bundle}_density.nii.gz"),    emit: density_map_mni
    tuple val(subject), val(bundle), path("density_maps/${bundle}/${subject}_${bundle}_shape_metrics.csv"),     emit: shape_metrics
    tuple val(subject), val(bundle), path("density_maps/${bundle}/${subject}_${bundle}_similarity*.csv"),       emit: similarity
    path "density_maps/${bundle}/${subject}_${bundle}.done",                                                    emit: done

    script:
    def refs_dm_string = refs_dm.join(" ")
    def percentiles = params.similarity_percentile
        .tokenize(',')
        .collect { it.trim() }
        .findAll { it }
    def percentiles_bash = percentiles.join(' ')

    """
    # density map of subject tractogram (subject space).
    density_map \\
        --tractogram "${sbundle}" \\
        --reference "${maps[0]}" \\
        --output "${subject}_${bundle}.nii.gz"

    # warp subject-space density map into MNI space (for similarity comparison)
    apply_transform_image \\
        --image "${subject}_${bundle}_density.nii.gz" \\
        --warp "${inverse_warp}" \\
        --warp_ref "${qa_mni_ref}" \\
        --order 1 \\
        --output "mni_${subject}_${bundle}_density.nii.gz"

    # shape metrics of subject tractogram
    shape_metrics \\
        --subject "${subject}" \\
        --bundle "${bundle}" \\
        --mask "${subject}_${bundle}.nii.gz" \\
        --tractogram "${sbundle}" \\
        --output ./

    # Similarity metrics against reference density maps — one CSV per percentile
    for p in ${percentiles_bash}; do
        compute_bundle_similarity \\
            --subject ${subject} \\
            --bundle ${bundle} \\
            --input "mni_${subject}_${bundle}_density.nii.gz" \\
            --references ${refs_dm_string} \\
            --output-csv "${subject}_${bundle}_similarity\${p}.csv" \\
            --percentile \${p}
    done

    # Outputs into storeDir subdir
    mkdir -p density_maps/${bundle}
    mv "${subject}_${bundle}.nii.gz" \\
        "${subject}_${bundle}_density.nii.gz" \\
        "mni_${subject}_${bundle}_density.nii.gz" \\
        "${subject}_${bundle}_shape_metrics.csv" \\
        density_maps/${bundle}/
    mv ${subject}_${bundle}_similarity*.csv density_maps/${bundle}/

    # Completion marker:
    expected=\$(echo ${percentiles_bash} | wc -w)
    actual=\$(ls density_maps/${bundle}/${subject}_${bundle}_similarity*.csv 2>/dev/null | wc -l)
    if [ "\${actual}" -eq "\${expected}" ]; then
        touch density_maps/${bundle}/${subject}_${bundle}.done
    else
        echo "Warning: BUNDLE_METRICS produced \${actual}/\${expected} similarity CSVs for ${subject}/${bundle}" >&2
    fi
    """
}
