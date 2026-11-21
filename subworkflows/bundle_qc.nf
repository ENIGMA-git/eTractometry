include { RENDER_BUNDLE_QC  } from '../modules/dive'
include { BUILD_TRACT_HTML  } from '../modules/qc'
include { BUILD_META_HTML   } from '../modules/qc'
include { BUILD_BUAN_HTML   } from '../modules/qc'

workflow BUNDLE_QC {

    take:
    tracts_channel
    warp_channel
    qa_mni_channel
    meta_qc_pngs_channel
    buan_qc_pngs_channel
    group_mean_pngs_channel
    similarity
    bundle_configs
    output_dir
    subjects_list
    dataset
    atlas_bundle_pngs

    main:
    render_in = tracts_channel                          
        .combine(warp_channel, by: 0)                   
        .combine(qa_mni_channel, by: 0)
        .combine(bundle_configs, by: 1)
        .map { bundle, subject, tract, warp, qa_mni, cfg ->
            tuple(subject, bundle, tract, warp, qa_mni, cfg.cam_views)
        }
    RENDER_BUNDLE_QC(render_in)

    tract_bundles = RENDER_BUNDLE_QC.out.tract_qc_pngs
        .map { subject, bundle, png -> bundle }
        .unique()
        .collect()

    meta_bundles = meta_qc_pngs_channel
        .map { subject, bundle, png -> bundle }
        .unique()
        .collect()

    buan_bundles = buan_qc_pngs_channel
        .map { subject, bundle, png -> bundle }
        .unique()
        .collect()

    similarity50 = similarity.filter { it -> it.name.contains("50") }

    // tract HTML
    BUILD_TRACT_HTML(
        RENDER_BUNDLE_QC.out.tract_qc_pngs.collect(),
        group_mean_pngs_channel.collect(),
        output_dir,
        tract_bundles,
        subjects_list,
        dataset,
        atlas_bundle_pngs,
        similarity50
    )

    // MeTA label QC HTML
    BUILD_META_HTML(
        meta_qc_pngs_channel.collect(),
        output_dir,
        meta_bundles,
        subjects_list,
        dataset
    )

    // BUAN QC HTML
    BUILD_BUAN_HTML(
        buan_qc_pngs_channel.collect(),
        output_dir,
        buan_bundles,
        subjects_list,
        dataset
    )

    emit:
    tract_qc_pngs = RENDER_BUNDLE_QC.out.tract_qc_pngs
    html          = BUILD_TRACT_HTML.out.html
    meta_html     = BUILD_META_HTML.out.html
    buan_html     = BUILD_BUAN_HTML.out.html
    csv           = BUILD_TRACT_HTML.out.csv
    meta_csv      = BUILD_META_HTML.out.csv
    buan_csv      = BUILD_BUAN_HTML.out.csv
}
