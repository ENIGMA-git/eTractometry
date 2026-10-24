process META_TRACTOMETRY {

    label 'medium'
    label 'python_tools'

    tag "${subject}_${bundle}"
    
    storeDir "${params.output_dir}/subjects/${subject}"

    input:
    tuple val(subject), val(bundle), path(sbundle), path(maps), path(bundle_mask), path(mbundle), path(inverse_warp), path(qa_mni_ref), val(num_segments), val(segmentation_method), val(cam_views)

    output:
    tuple val(subject), val(bundle), path("MeTA/${bundle}/${subject}_${bundle}_*_segments_average_local_*.csv"),           emit: meta_profiles
    tuple val(subject), val(bundle), path("MeTA/${bundle}/${subject}_${bundle}*.nii.gz"), optional: true,                  emit: meta_volumes
    tuple val(subject), val(bundle), path("qc_images/meta/${bundle}/${subject}_${bundle}_local_core_*.png"),               emit: meta_qc_pngs
    path "MeTA/${bundle}/${subject}_${bundle}.done",                                                                       emit: done

    script:
    def maps_list = maps.join(" ")
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

    # Along tract profiles
    for map in ${maps_list}; do
        metric=\$(basename "\${map}" | cut -d"." -f5)

        # local_all mask
        volumetric_profile \\
            --subject ${subject} \\
            --bundle ${bundle} \\
            --mask "${subject}_${bundle}_${num_segments}_segments_local_all.nii.gz" \\
            --map \${map} \\
            --output "./"
        mv "${subject}_${bundle}_segments_average.csv" \\
            "${subject}_${bundle}_\${metric}_segments_average_local_all.csv"

        # local_core mask
        volumetric_profile \\
            --subject ${subject} \\
            --bundle ${bundle} \\
            --mask "${subject}_${bundle}_${num_segments}_segments_local_core.nii.gz" \\
            --map \${map} \\
            --output "./"
        mv "${subject}_${bundle}_segments_average.csv" \\
            "${subject}_${bundle}_\${metric}_segments_average_local_core.csv"
    done

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
    if ls MeTA/${bundle}/${subject}_${bundle}_*_segments_average_local_*.csv >/dev/null 2>&1; then
        touch MeTA/${bundle}/${subject}_${bundle}.done
    else
        echo "Warning: META_TRACTOMETRY produced no CSVs for ${subject}/${bundle}" >&2
    fi
    """
}