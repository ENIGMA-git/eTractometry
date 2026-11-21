#!/usr/bin/env nextflow

include { DIFFUSION_MODELING     } from './subworkflows/modeling'
include { TRACTOGRAPHY           } from './subworkflows/tractography'
include { TRACTOMETRY            } from './subworkflows/tractometry'
include { BUNDLE_QC              } from './subworkflows/bundle_qc'
include { CHECK_RUN_COMPLETENESS } from './modules/combine'

params {
    dataset:              String
    input_csv:            Path
    metrics:              String
    extra_maps:           String = ''
    methods:              String
    output_dir:           Path
    subjects_list:        Path
    cortical_atlas_mni:   Path
    mni_qa_template_1mm:  Path
    mni_qa_template_2mm:  Path
    bundle_atlas_dir:     Path
    atlas_bundle_pngs:    Path
    dsi_studio:           String = ''
    conda_env:            String = ''
    similarity_percentile: String = '50'
    bundle_selection:     String = 'primary'
}

def selects(token, group, cfg, bundle) {
    return token in ['all', cfg.category] || [group, bundle].any { name -> name == token || name.startsWith(token + '_') }
}

workflow {

    main:

    // --methods : 'qc_tract', 'meta', 'buan', or 'meta,buan'
    if (!params.methods) {
        error "Must specify --methods (meta, buan, 'meta,buan', or none)."
    }
    def methods_input = params.methods.tokenize(',').collect { it.trim().toLowerCase() }
    def valid_methods = ['meta', 'buan', 'none']
    methods_input.each { m ->
        if (!(m in valid_methods)) {
            error "Unknown method '${m}'. Valid options: ${valid_methods.join(', ')}."
        }
    }
    if ('none' in methods_input && methods_input.size() > 1) {
        error "'--methods none' cannot be combined with other methods. Got: ${params.methods}"
    }

    def methods = ('none' in methods_input) ? [] : methods_input
    def extra_map_names = params.extra_maps.tokenize(',')

    input_rows = channel.fromPath(params.input_csv).splitCsv(header: true)

    subject_inputs = input_rows
        .map { row ->
            def dwi      = file(row.dwi).toRealPath()
            def is_sz    = row.dwi.toString().endsWith('.sz')
            def has_mask = row.mask ? true : false
            def has_b_table = row.b_table ? true : false
            tuple(
                row.subject,
                is_sz,
                is_sz || has_b_table ? dwi : file(row.bval).toRealPath(),
                is_sz || has_b_table ? dwi : file(row.bvec).toRealPath(),
                has_b_table ? file(row.b_table).toRealPath() : dwi,
                has_b_table,
                dwi,
                has_mask ? file(row.mask).toRealPath() : dwi,
                has_mask,
                row.resample_to
            )
        }

    extra_maps = input_rows
        .map { row ->
            def names = extra_map_names.findAll { name -> row["map_${name}"] }
            tuple(row.subject, names, names.collect { name -> file(row["map_${name}"]).toRealPath() })
        }
        .filter { _subject, names, _maps -> names }
        
    atlas_bundles = channel
        .fromPath('atlas/full/tt_gz/*.tt.gz')
        .map { tract -> tuple(tract.simpleName, file(tract)) }

    atlas_density_refs = channel
        .fromPath('atlas/mean_density_map/*/HCP1065.*.mask.{1,2}mm.nii.gz')
        .map { f -> tuple(f.parent.name, file(f)) }
        .groupTuple()

    // --bundles : all, a category, or bundle group / bundle names or name prefixes
    def selection = params.bundle_selection.tokenize(',').collect { t -> t.trim() }
    def unknown = selection.findAll { t -> !params.bundles.any { group, cfg -> cfg.bundles.any { b -> selects(t, group, cfg, b) } } }
    if (unknown) {
        error "Unknown --bundles '${unknown.join(', ')}'. Use all, a category, or bundle group or bundle names (or name prefixes) from configs/bundles.config."
    }
    def selected_groups = params.bundles
        .collect { group, cfg -> cfg + [bundles: cfg.bundles.findAll { b -> selection.any { t -> selects(t, group, cfg, b) } }] }
        .findAll { cfg -> cfg.bundles }

    bundle_group_configs = channel.from(selected_groups)

    bundle_configs = channel.from(selected_groups)
        .flatMap { row ->
            def cfg = row.subMap(row.keySet() - 'bundles')
            row.bundles.collect { b -> tuple(cfg, b) }
        }

    // subworkflows
    DIFFUSION_MODELING(
        subject_inputs,
        extra_maps,
        params.metrics,
        extra_map_names,
        params.cortical_atlas_mni,
        params.output_dir,
        params.subjects_list,
        params.dataset
    )

    TRACTOGRAPHY(
        DIFFUSION_MODELING.out.gqi_model,
        DIFFUSION_MODELING.out.mapping_mz,
        bundle_group_configs,
        params.bundle_atlas_dir
    )

    TRACTOMETRY(
        TRACTOGRAPHY.out.tracts,
        DIFFUSION_MODELING.out.metric_maps,
        DIFFUSION_MODELING.out.warp,
        DIFFUSION_MODELING.out.inverse_warp,
        DIFFUSION_MODELING.out.qa_mni,
        atlas_bundles,
        atlas_density_refs,
        bundle_configs,
        methods,
        params.group_mean_cfg,
        params.mni_qa_template_1mm,
        params.mni_qa_template_2mm,
        params.dataset,
        ([params.metrics] + extra_map_names).join(',')
    )

    atlas_pngs_dir = file(params.atlas_bundle_pngs).toAbsolutePath().toString()
    BUNDLE_QC(
        TRACTOGRAPHY.out.tracts,
        DIFFUSION_MODELING.out.warp,
        DIFFUSION_MODELING.out.qa_mni,
        TRACTOMETRY.out.meta_qc_pngs,
        TRACTOMETRY.out.buan_qc_pngs,
        TRACTOMETRY.out.group_mean_pngs,
        TRACTOMETRY.out.similarity,
        bundle_configs,
        params.output_dir,
        params.subjects_list,
        params.dataset,
        atlas_pngs_dir
    )

    CHECK_RUN_COMPLETENESS(
        BUNDLE_QC.out.tract_qc_pngs.collect().ifEmpty([]),
        TRACTOMETRY.out.meta_qc_pngs.collect().ifEmpty([]),
        TRACTOMETRY.out.buan_qc_pngs.collect().ifEmpty([]),
        params.output_dir,
        file(params.input_csv),
        selected_groups.collectMany { cfg -> cfg.bundles },
        params.dataset
    )
}
