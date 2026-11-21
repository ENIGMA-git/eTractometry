include { SEGMENT_BUNDLES } from '../modules/dsi_studio/segment'

workflow TRACTOGRAPHY {

    take:
    gqi_model
    mapping_mz
    bundle_group_configs
    bundle_atlas_dir

    main:
    segment_bundles_in = gqi_model
        .combine(mapping_mz, by: 0)
        .combine(bundle_group_configs)
    SEGMENT_BUNDLES(segment_bundles_in, bundle_atlas_dir)

    tracts_split = SEGMENT_BUNDLES.out.tracts
        .flatMap { subject, tracts ->
            def list = (tracts instanceof List) ? tracts : [tracts]
            list.collect { tract ->
                def bundle = tract.name - "${subject}." - ".tt.gz"
                tuple(subject, bundle, tract)
            }
        }

    emit:
    tracts = tracts_split
}
