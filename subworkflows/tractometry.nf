include { BUNDLE_METRICS          } from '../modules/group_mean'
include { GROUP_MEAN_BUNDLE       } from '../modules/group_mean'
include { META_TRACTOMETRY        } from '../modules/meta'
include { BUAN_TRACTOMETRY        } from '../modules/buan'
include { AGGREGATE_META_PROFILES } from '../modules/combine'
include { AGGREGATE_SHAPE_METRICS } from '../modules/combine'
include { AGGREGATE_BUAN_PROFILES } from '../modules/combine'
include { AGGREGATE_SIMILARITY    } from '../modules/combine'

workflow TRACTOMETRY {

    take:
    tracts_channel
    metric_maps_channel
    warp_channel
    inverse_warp_channel
    qa_mni_channel
    atlas_bundles
    atlas_density_refs
    bundle_configs
    methods
    group_mean_cfg 
    mni_qa_template_1mm
    mni_qa_template_2mm
    dataset
    metrics

    main:
    def meta_on = 'meta' in methods
    def buan_on = 'buan' in methods

    def metrics_list    = metrics.tokenize(',').collect { it.trim() }.findAll { it }
    def metrics_by_len  = metrics_list.sort(false) { -it.length() }

    bundle_mask_ch        = channel.empty()
    density_map_ch        = channel.empty()
    shape_metrics_ch      = channel.empty()
    similarity_ch         = channel.empty()
    meta_profiles_ch      = channel.empty()
    meta_qc_pngs_ch       = channel.empty()
    group_mean_density_ch = channel.empty()
    group_mean_pngs_ch    = channel.empty()
    buan_profiles_ch      = channel.empty()
    buan_qc_pngs_ch       = channel.empty()


    
    bundle_metrics_in = tracts_channel                            
        .combine(metric_maps_channel,  by: 0)
        .combine(inverse_warp_channel, by: 0)
        .combine(qa_mni_channel,       by: 0)
        .map { subject, bundle, sbundle, maps, inv_warp, qa_mni ->
            tuple(bundle, subject, sbundle, maps, inv_warp, qa_mni) }
        .combine(atlas_density_refs, by: 0)
        .map { bundle, subject, sbundle, maps, inv_warp, qa_mni, refs_dm ->
            tuple(subject, bundle, sbundle, maps, inv_warp, qa_mni, refs_dm) }
    BUNDLE_METRICS(bundle_metrics_in)

    bundle_mask_ch   = BUNDLE_METRICS.out.bundle_mask
    density_map_ch   = BUNDLE_METRICS.out.density_map

    group_mean_in = BUNDLE_METRICS.out.density_map_mni
        .groupTuple(by: 1)
        .map { subjects, bundle, dms ->
            log.info "GROUP_MEAN_BUNDLE: ${bundle} → ${dms.size()} subjects"
            tuple(bundle, dms)
        }
        .combine(bundle_configs.map { cfg, bundle -> tuple(bundle, cfg.cam_views) }, by: 0)
    GROUP_MEAN_BUNDLE(group_mean_in, group_mean_cfg, mni_qa_template_1mm, mni_qa_template_2mm, dataset)

    group_mean_density_ch = GROUP_MEAN_BUNDLE.out.group_mean_density
    group_mean_pngs_ch    = GROUP_MEAN_BUNDLE.out.group_mean_pngs

    // shape-metrics:
    shape_csvs = BUNDLE_METRICS.out.shape_metrics
        .map { subject, bundle, csv -> csv }
        .collect()
    AGGREGATE_SHAPE_METRICS(shape_csvs, dataset)
    shape_metrics_ch = AGGREGATE_SHAPE_METRICS.out.combined

    // atlas-similarity:
    similarity_per_percentile = BUNDLE_METRICS.out.similarity
        .map { subject, bundle, csvs -> csvs }
        .flatten()
        .map { csv ->
            def m = (csv.simpleName =~ /_similarity(\d+)$/)
            m ? tuple((m[0][1] as Integer), csv) : null
        }
        .filter { it != null }
        .groupTuple()
    AGGREGATE_SIMILARITY(similarity_per_percentile, dataset)
    similarity_ch    = AGGREGATE_SIMILARITY.out.combined

    // MeTA
    if (meta_on) {
        meta_in = tracts_channel
            .combine(metric_maps_channel,           by: 0)
            .combine(BUNDLE_METRICS.out.bundle_mask, by: [0, 1])
            .combine(inverse_warp_channel,          by: 0)
            .combine(qa_mni_channel,                by: 0)
            .combine(bundle_configs,                by: 1)
            .combine(atlas_bundles,                 by: 0)
            .map { bundle, subject, sbundle, maps, bundle_mask, inv_warp, qa_mni, cfg, atlas_tt ->
                tuple(subject, bundle, sbundle, maps, bundle_mask, atlas_tt, inv_warp, qa_mni,
                    cfg.num_segments, cfg.segmentation_method, cfg.cam_views)
            }
        META_TRACTOMETRY(meta_in)

        meta_profiles_ch = META_TRACTOMETRY.out.meta_profiles
        meta_qc_pngs_ch  = META_TRACTOMETRY.out.meta_qc_pngs

        meta_in_per_metric_roi = META_TRACTOMETRY.out.meta_profiles
            .map { subject, bundle, csvs -> csvs }
            .flatten()
            .map { csv ->
                def stem = csv.simpleName
                def m    = (stem =~ /^(.+)_segments_average_local_(all|core)$/)
                if (!m) return null
                def stripped = m[0][1]
                def roi      = m[0][2]
                def metric   = metrics_by_len.find { stripped.endsWith('_' + it) }
                metric ? tuple(metric, roi, csv) : null
            }
            .filter { it != null }
            .groupTuple(by: [0, 1])
        AGGREGATE_META_PROFILES(meta_in_per_metric_roi, dataset)
    }

    // BUAN
    if (buan_on) {
        buan_in = tracts_channel
            .combine(warp_channel, by: 0)
            .combine(qa_mni_channel, by: 0)
            .combine(metric_maps_channel, by: 0)
            .combine(bundle_configs, by: 1)
            .combine(atlas_bundles, by: 0)
            .map { bundle, subject, sbundle, warp, qa_mni, metric_maps, cfg, atlas_tt ->
                tuple(subject, bundle, sbundle, warp, qa_mni, cfg.extrapolate, metric_maps, atlas_tt, cfg.cam_views)
            }
        BUAN_TRACTOMETRY(buan_in)
        buan_profiles_ch = BUAN_TRACTOMETRY.out.buan_profiles
        buan_qc_pngs_ch  = BUAN_TRACTOMETRY.out.buan_qc_pngs

        buan_in_per_metric = BUAN_TRACTOMETRY.out.buan_profiles
            .map { subject, bundle, csvs -> csvs }
            .flatten()
            .map { csv ->
                def stem   = csv.simpleName
                def metric = metrics_by_len.find { stem.endsWith('_' + it) }
                metric ? tuple(metric, csv) : null
            }
            .filter { it != null }
            .groupTuple()
        AGGREGATE_BUAN_PROFILES(buan_in_per_metric, dataset)
    }

    emit:
    bundle_mask        = bundle_mask_ch
    density_map        = density_map_ch
    shape_metrics      = shape_metrics_ch
    similarity         = similarity_ch
    meta_profiles      = meta_profiles_ch
    meta_qc_pngs       = meta_qc_pngs_ch
    group_mean_density = group_mean_density_ch
    group_mean_pngs    = group_mean_pngs_ch
    buan_profiles      = buan_profiles_ch
    buan_qc_pngs       = buan_qc_pngs_ch
}
