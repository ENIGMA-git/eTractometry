process RENDER_BUNDLE_QC {

    label 'medium'
    label 'python_tools'

    tag "${subject}_${bundle}"
    
    storeDir "${params.output_dir}/subjects/${subject}/qc_images/tracts/${bundle}"

    input:
    tuple val(subject), val(bundle), path(tract), path(warp), path(qa_mni), val(cam_views)

    output:
    tuple val(subject), val(bundle), path("tract_${subject}_${bundle}_*.png"), optional: true, emit: tract_qc_pngs
    path "${subject}_${bundle}.done",                                                          emit: done

    script:
    """
    dive --glass_brain --background 1 --mode cli --zoom 1.5 \\
        --tract_width 3 --tract "${tract}" \\
        --warp "${warp}" \\
        --cam_view ${cam_views.join(' ')} \\
        --warp_source dsi_studio \\
        --warp_ref "${qa_mni}" \\
        --output tract_${subject}_${bundle}

    # completion marker:
    if ls tract_${subject}_${bundle}_*.png >/dev/null 2>&1; then
        touch ${subject}_${bundle}.done
    else
        echo "Warning: RENDER_BUNDLE_QC produced no PNGs for ${subject}/${bundle}" >&2
    fi
    """
}
