process SEGMENT_BUNDLES {

    label 'medium'
    label 'dsi_studio'

    tag "${subject}_${cfg.bundles.join('+')}"
    
    storeDir "${params.output_dir}/subjects/${subject}/tracts"

    input:
    tuple val(subject), path(model), path(mapping_mz), val(cfg)
    path atlas

    output:
    tuple val(subject), path("${cfg.bundles.size() > 1 ? '{' + cfg.bundles.join(',') + '}' : cfg.bundles[0]}/${subject}.*.tt.gz"),          optional: true, emit: tracts
    tuple val(subject), path("${cfg.bundles.size() > 1 ? '{' + cfg.bundles.join(',') + '}' : cfg.bundles[0]}/${subject}.*.tt.gz.stat.txt"), optional: true, emit: tract_stats
    path "${cfg.bundles.size() > 1 ? '{' + cfg.bundles.join(',') + '}' : cfg.bundles[0]}/${subject}_*.done",                                                emit: done


    script:
    def bundles      = cfg.bundles.join(',')
    def bundles_sh   = cfg.bundles.join(' ')
    """
    export HOME="\${PWD}/.home"
    export XDG_DATA_HOME="\${PWD}/.xdg_data"
    export XDG_CACHE_HOME="\${PWD}/.xdg_cache"
    export XDG_CONFIG_HOME="\${PWD}/.xdg_config"
    mkdir -p "\${HOME}" "\${XDG_DATA_HOME}" "\${XDG_CACHE_HOME}" "\${XDG_CONFIG_HOME}"

    export LANG=C.UTF-8
    export LC_ALL=C.UTF-8

    echo "HOME=\${HOME}"
    echo "XDG_DATA_HOME=\${XDG_DATA_HOME}"
    
    # segment bundles for subject (group key: ${cfg.bundles[0]})
    dsi_studio --action=atk \\
        --thread_count=${task.cpus} \\
        --source="${model}" \\
        --output="./" \\
        --tractography_atlas="${atlas}/human.tt.gz" \\
        --track_id=${bundles} \\
        --template=${cfg.template} \\
        --otsu_threshold=${cfg.otsu_threshold} \\
        --fa_threshold=${cfg.fa_threshold} \\
        --turning_angle=${cfg.turning_angle} \\
        --smoothing=${cfg.smoothing} \\
        --step_size=${cfg.step_size} \\
        --tolerance=${cfg.tolerance} \\
        --track_voxel_ratio=${cfg.track_voxel_ratio} \\
        --tip_iteration=${cfg.tip_iteration} \\
        --check_ending=${cfg.check_ending} \\
        --use_roi=${cfg.use_roi} \\
        --overwrite=0

    # completion markers:
    for b in ${bundles_sh}; do
        mkdir -p "\$b"
        if [ -e "${subject}.\${b}.tt.gz" ]; then
            mv "${subject}.\${b}.tt.gz" "\$b/"
            [ -e "${subject}.\${b}.tt.gz.stat.txt" ] && mv "${subject}.\${b}.tt.gz.stat.txt" "\$b/"
            touch "\$b/${subject}_\${b}.done"
        else
            echo "Warning: dsi_studio did not produce ${subject}.\${b}.tt.gz" >&2
        fi
    done
    """
}
