process BUAN_TRACTOMETRY {

    label 'light'
    label 'python_tools'

    tag "${subject}_${bundle}"
    
    storeDir "${params.output_dir}/subjects/${subject}"

    input:
    tuple val(subject), val(bundle), path(org_bundle), path(warp), path(qa_mni), val(extrapolate), path(metric_maps), path(ref_bundle), val(cam_views)

    output:
    tuple val(subject), val(bundle), path("BUAN/${bundle}/${subject}_${bundle}_*.csv"),                 optional: true, emit: buan_profiles
    tuple val(subject), val(bundle), path("qc_images/buan/${bundle}/tract_${subject}_${bundle}_*.png"), optional: true, emit: buan_qc_pngs
    path "BUAN/${bundle}/${subject}_${bundle}.done",                                                                    emit: done

    script:
    """
    output_paths=()

    # get diffusion metric
    for path in ${metric_maps}; do
        metric=\$(basename "\$path" | cut -d"." -f5)
        output_paths+=("${subject}_${bundle}_\${metric}.csv")
    done

    # Along tract profiles
    buan_profile.py \\
        --subject ${subject} \\
        --bundle ${bundle} \\
        --org_bundle "${org_bundle}" \\
        --ref_bundle "${ref_bundle}" \\
        --warp "${warp}" \\
        --warp_source "dsi_studio" \\
        --warp_ref "${qa_mni}" \\
        --scalar_maps ${metric_maps} \\
        --s_len 5.0 \\
        --robust_method "${extrapolate}" \\
        --outputs \${output_paths[@]}

    dive --glass_brain --background 1 --mode cli --zoom 1.5 \\
        --tract_width 3 --tract "${org_bundle}" \\
        --warp "${warp}" \\
        --cam_view ${cam_views.join(' ')} \\
        --warp_source dsi_studio \\
        --warp_ref "${qa_mni}" \\
        --seg_method "${extrapolate}" \\
        --s_len 5.0 \\
        --output tract_${subject}_${bundle}

    # outputs out into storeDir subdirs
    mkdir -p BUAN/${bundle} qc_images/buan/${bundle}
    mv ${subject}_${bundle}_*.csv BUAN/${bundle}/ 2>/dev/null || true
    mv tract_${subject}_${bundle}_*.png qc_images/buan/${bundle}/ 2>/dev/null || true

    # completion marker:
    if ls BUAN/${bundle}/${subject}_${bundle}_*.csv >/dev/null 2>&1; then
        touch BUAN/${bundle}/${subject}_${bundle}.done
    else
        echo "Warning: BUAN_TRACTOMETRY produced no CSVs for ${subject}/${bundle}" >&2
    fi
    """
}
