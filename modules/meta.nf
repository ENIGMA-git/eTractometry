process META_TRACTOMETRY {

    label 'medium'
    label 'python_tools'

    tag "${subject}_${bundle}"
    
    storeDir "${params.output_dir}/subjects/${subject}"

    input:
    tuple val(subject), val(bundle), path(sbundle), path(maps), path(bundle_mask), path(mbundle), path(inverse_warp), path(qa_mni_ref), val(num_segments), val(segmentation_method), val(cam_views)

    output:
    tuple val(subject), val(bundle), path("MeTA/${bundle}/${subject}_${bundle}_*_segments_average_*.csv"),                 emit: meta_profiles
    tuple val(subject), val(bundle), path("MeTA/${bundle}/${subject}_${bundle}*.nii.gz"), optional: true,                  emit: meta_volumes
    tuple val(subject), val(bundle), path("qc_images/meta/${bundle}/${subject}_${bundle}_local_core_*.png"),               emit: meta_qc_pngs
    path "MeTA/${bundle}/${subject}_${bundle}.done",                                                                       emit: done

    script:
    def masks = [
        local_all  : "${subject}_${bundle}_${num_segments}_segments_local_all.nii.gz",
        local_core : "${subject}_${bundle}_${num_segments}_segments_local_core.nii.gz",
        global_all : "${subject}_${bundle}.nii.gz",
        global_core: "${subject}_${bundle}_global_core.nii.gz",
    ]
    // Each map is named by the metric in its file name, e.g. <subject>.odf.gqi.fz.dti_fa.nii.gz -> dti_fa
    def map_args  = maps.collect { m -> "\"${m.name.split(/\./)[4]}=${m}\"" }.join(' ')
    def mask_args = masks.collect { roi, mask -> "\"${roi}=${mask}\"" }.join(' ')
    """
    # parcellate bundle mask
    meta --subject ${subject} \\
        --bundle ${bundle} \\
        --mbundle "${mbundle}" \\
        --sbundle "${sbundle}" \\
        --mask "${subject}_${bundle}.nii.gz" \\
        --warp "${inverse_warp}" \\
        --warp_source "dsi_studio" \\
        --seg_method "${segmentation_method}" \\
        --num_segments ${num_segments} \\
        --output ./ \\
        --percent 0.1

    # Along-tract (local) and whole-bundle (global) profiles of each map
    volumetric_profile --subject ${subject} --bundle ${bundle} --map ${map_args} --mask ${mask_args} --output ./

    # MeTA QC
    dive --background 1 --glass_brain --mode cli --zoom 1.5 \\
        --mask "${subject}_${bundle}_${num_segments}_segments_local_core.nii.gz" \\
        --warp "${inverse_warp}" \\
        --warp_source dsi_studio \\
        --warp_ref "${qa_mni_ref}" \\
        --cam_view ${cam_views.join(' ')} \\
        --output "./${subject}_${bundle}_local_core"

    # Outputs out into storeDir subdirs
    mkdir -p MeTA/${bundle} qc_images/meta/${bundle}
    mv ${subject}_${bundle}_*.{nii.gz,csv} MeTA/${bundle}/ 2>/dev/null || true
    mv ${subject}_${bundle}_local_core_*.png qc_images/meta/${bundle}/ 2>/dev/null || true

    # Completion marker:
    if ls MeTA/${bundle}/${subject}_${bundle}_*_segments_average_*.csv >/dev/null 2>&1; then
        touch MeTA/${bundle}/${subject}_${bundle}.done
    else
        echo "Warning: META_TRACTOMETRY produced no CSVs for ${subject}/${bundle}" >&2
    fi
    """
}