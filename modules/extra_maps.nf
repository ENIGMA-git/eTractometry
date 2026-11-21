process IMPORT_EXTRA_MAPS {

    label 'minimal'
    label 'python_tools'

    tag "${subject}"

    storeDir "${params.output_dir}/subjects/${subject}"

    input:
    tuple val(subject), val(names), path(maps, stageAs: 'extra?/*'), path(gqi_maps)

    output:
    tuple val(subject), path("extra_maps/${subject}.odf.gqi.fz.${names.size() > 1 ? '{' + names.join(',') + '}' : names[0]}.nii.gz"), emit: metric_maps

    script:
    def outputs = names.collect { name -> "extra_maps/${subject}.odf.gqi.fz.${name}.nii.gz" }
    """
    # Resample onto the GQI grid, named like the DSI-Studio maps for MeTA, BUAN and QC
    mkdir -p extra_maps
    resample_maps.py \\
        --maps ${maps} \\
        --reference "${gqi_maps[0]}" \\
        --outputs ${outputs.join(' ')}
    """
}
