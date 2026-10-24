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
    dsi_studio_bin:       String = ''
    python_env:           String = ''
    similarity_percentile: String = '50'
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
            tuple(
                row.subject,
                is_sz,
                is_sz ? dwi : file(row.bval).toRealPath(),
                is_sz ? dwi : file(row.bvec).toRealPath(),
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
        //                                              → (bundle, [refs_dm files])

    bundle_group_configs = channel.from(params.bundles.values())

    bundle_configs = channel.from(params.bundles.values())
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
        file("${params.bundle_atlas_dir}/human.txt"),
        params.dataset
    )
}
