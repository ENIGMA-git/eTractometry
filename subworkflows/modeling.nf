include { RECONSTRUCT_GQI       } from '../modules/dsi_studio/model'
include { IMPORT_EXTRA_MAPS     } from '../modules/extra_maps'
include { RENDER_MODEL_QC       } from '../modules/qc'
include { BUILD_DIFFUSION_HTML  } from '../modules/qc'

workflow DIFFUSION_MODELING {

    take:
    subject_inputs
    extra_maps
    metrics
    extra_map_names
    cortical_atlas_mni
    output_dir
    subjects_list
    dataset

    main:
    
    RECONSTRUCT_GQI(
        subject_inputs, 
        metrics
    )

    // Subject-space extra maps (e.g. NODDI) on the GQI grid; subjects missing one are profiled without it
    metric_maps = RECONSTRUCT_GQI.out.metric_maps
    if (extra_map_names) {
        IMPORT_EXTRA_MAPS(
            extra_maps.combine(RECONSTRUCT_GQI.out.metric_maps, by: 0)
        )
        metric_maps = metric_maps
            .join(IMPORT_EXTRA_MAPS.out.metric_maps, remainder: true)
            .map { subject, gqi_maps, imported -> tuple(subject, imported ? [gqi_maps, imported].flatten() : gqi_maps) }
    }

    model_qc_in = RECONSTRUCT_GQI.out.qa_mni
        .combine(metric_maps, by: 0)
    RENDER_MODEL_QC(
        model_qc_in, 
        cortical_atlas_mni
    )

    model_qc_pngs_all = RENDER_MODEL_QC.out.model_qc_pngs.collect()
    BUILD_DIFFUSION_HTML(
        model_qc_pngs_all, 
        output_dir, 
        ([metrics] + extra_map_names).join(','), 
        subjects_list, 
        dataset
    )

    emit:
    qc_dwi        = RECONSTRUCT_GQI.out.qc_dwi
    gqi_model     = RECONSTRUCT_GQI.out.gqi_model
    qc_model      = RECONSTRUCT_GQI.out.qc_model
    mapping_mz    = RECONSTRUCT_GQI.out.mapping_mz
    qa_mni        = RECONSTRUCT_GQI.out.qa_mni
    warp          = RECONSTRUCT_GQI.out.warp
    inverse_warp  = RECONSTRUCT_GQI.out.inverse_warp
    metric_maps   = metric_maps
    model_qc_pngs = RENDER_MODEL_QC.out.model_qc_pngs
}
