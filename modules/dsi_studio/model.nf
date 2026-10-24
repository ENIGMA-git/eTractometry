process RECONSTRUCT_GQI {

    label 'medium'
    label 'dsi_studio'

    tag "${subject}"
    
    storeDir "${params.output_dir}/subjects/${subject}"

    input:
    // tuple val(subject), val(is_sz), path(bval), path(bvec), path(dwi), path(mask), val(has_mask), val(resample_to)
    tuple val(subject), val(is_sz), path(bval, stageAs: 'aux.bval'), path(bvec, stageAs: 'aux.bvec'), path(dwi), path(mask, stageAs: 'aux.mask'), val(has_mask), val(resample_to)
    val metrics

    output:
    tuple val(subject), path("model/${subject}.sz.txt"),                                                  emit: qc_dwi
    tuple val(subject), path("model/${subject}.odf.gqi.fz"),                                              emit: gqi_model
    tuple val(subject), path("model/${subject}.odf.gqi.fz.txt"),                                          emit: qc_model
    tuple val(subject), path("model/${subject}.odf.gqi.fz.human.mz"),                                     emit: mapping_mz
    tuple val(subject), path("maps/${subject}.qa.mni.nii.gz"),                                            emit: qa_mni
    tuple val(subject), path("model/${subject}.1Warp.nii.gz"),                                            emit: warp
    tuple val(subject), path("model/${subject}.1InverseWarp.nii.gz"),                                     emit: inverse_warp
    tuple val(subject), path("maps/${subject}.odf.gqi.fz.*.nii.gz"),                                      emit: metric_maps
    path "model/${subject}.done",                                                                         emit: done

    script:
    def sz_name = "${subject}.sz"
    def src_step = is_sz
        ? "[ \"${dwi}\" = \"${sz_name}\" ] || ln -sf \"${dwi}\" \"${sz_name}\""
        : """dsi_studio --action=src \\
        --thread_count=${task.cpus} \\
        --source=\"${dwi}\" \\
        --bval=\"${bval}\" \\
        --bvec=\"${bvec}\" \\
        --output=\"${sz_name}\""""
    
    // Brain mask is optional with .sz
    def mask_arg     = has_mask ? "--mask=\"${mask}\"" : ''
    def make_iso_arg = resample_to.toString() == '0' ? '' : "--make_isotropic=${resample_to}"
    """
    export HOME="\${PWD}/.home"
    export XDG_DATA_HOME="\${PWD}/.xdg_data"
    export XDG_CACHE_HOME="\${PWD}/.xdg_cache"
    export XDG_CONFIG_HOME="\${PWD}/.xdg_config"
    mkdir -p "\${HOME}" "\${XDG_DATA_HOME}" "\${XDG_CACHE_HOME}" "\${XDG_CONFIG_HOME}"
    export LANG=C.UTF-8
    export LC_ALL=C.UTF-8
    echo "HOME=\${HOME}"
    echo "XDG_DATA_HOME=\${XDG_DATA_HOME}"

    # source: build .sz from NIfTI when needed, else reuse the existing .sz
    ${src_step}

    # QC source
    dsi_studio --action=qc \\
        --thread_count=${task.cpus} \\
        --source="${subject}.sz" \\
        --output="${subject}.sz.txt"

    # Make GQI model
    dsi_studio --action=rec \\
        --thread_count=${task.cpus} \\
        --source="${subject}.sz" \\
        ${mask_arg} \\
        --method=4 --param=1.25 --other_output=all \\
        --check_btable=1 \\
        ${make_iso_arg} \\
        --output="${subject}.odf.gqi.fz"

    # QC GQI
    dsi_studio --action=qc \\
        --thread_count=${task.cpus} \\
        --source="${subject}.odf.gqi.fz" \\
        --output="${subject}.odf.gqi.fz.txt"

    # Warp QA to MNI space (for registration QC)
    dsi_studio --action=exp \\
        --thread_count=${task.cpus} \\
        --source="${subject}.odf.gqi.fz" \\
        --export=qa,iso --export_to_mni
    mv "${subject}.odf.gqi.fz.qa.nii.gz" "${subject}.qa.mni.nii.gz"
    mv "${subject}.odf.gqi.fz.iso.nii.gz" "${subject}.iso.mni.nii.gz"

    # Export DSI-Studio's transform as ANTs-style 1Warp / 1InverseWarp NIFTIs.
    dsi_studio --action=reg \\
        --thread_count=${task.cpus} \\
        --to="${subject}.qa.mni.nii.gz,${subject}.iso.mni.nii.gz" \\
        --mapping="${subject}.odf.gqi.fz.human.mz" \\
        --output_mapping="${subject}.transform.nii.gz"
    mv "${subject}.transform.nii.gz"     "${subject}.1Warp.nii.gz"
    mv "${subject}.transform.inv.nii.gz" "${subject}.1InverseWarp.nii.gz"

    IFS=',' read -ra REQ_METRICS <<< "${metrics}"
    for m in "\${REQ_METRICS[@]}"; do
        dsi_studio --action=exp \\
            --thread_count=${task.cpus} \\
            --source="${subject}.odf.gqi.fz" \\
            --export="\${m}" \\
        || echo "Warning: metric '\${m}' unavailable for ${subject}; skipping" >&2
    done

    # Outputs out into storeDir subdirs
    mkdir -p model maps
    mv ${subject}.sz.txt \\
        ${subject}.odf.gqi.fz \\
        ${subject}.odf.gqi.fz.txt \\
        ${subject}.odf.gqi.fz.human.mz \\
        ${subject}.1Warp.nii.gz \\
        ${subject}.1InverseWarp.nii.gz \\
        model/
    mv ${subject}.qa.mni.nii.gz maps/
    mv ${subject}.odf.gqi.fz.*.nii.gz maps/

    # completion marker:
    if [ -e "model/${subject}.odf.gqi.fz" ] && [ -e "maps/${subject}.qa.mni.nii.gz" ]; then
        touch model/${subject}.done
    else
        echo "Warning: RECONSTRUCT_GQI incomplete for ${subject}" >&2
    fi
    """
}
