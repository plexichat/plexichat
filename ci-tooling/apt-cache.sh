#!/bin/sh
# Reuse apt package lists and archives from GitLab's cache.
#
# Every CI job runs in a fresh container, so apt normally downloads its
# indexes and .deb files again for git/libatomic1/nodejs. The cache paths
# below live under $CI_PROJECT_DIR and are archived by the job's GitLab
# cache; this helper copies them into apt's roots before install and back
# after, and only refreshes the indexes when the restored ones are unusable.
#
# Usage from a before_script:
#   . ci-tooling/apt-cache.sh
#   apt_install git libatomic1
#
# It must run as root, which the Docker executor does by default.

APT_CACHE_DIR="${CI_PROJECT_DIR:-$(pwd)}/.cache/apt"
APT_LISTS_DIR=/var/lib/apt/lists
APT_ARCHIVES_DIR=/var/cache/apt/archives

apt_cache_restore() {
    mkdir -p "$APT_LISTS_DIR" "$APT_ARCHIVES_DIR/partial" \
        "$APT_CACHE_DIR/lists" "$APT_CACHE_DIR/archives"

    if [ -d "$APT_CACHE_DIR/lists" ]; then
        find "$APT_CACHE_DIR/lists" -mindepth 1 -maxdepth 1 \
            ! -name lock ! -name partial ! -name auxfiles \
            -exec cp -a {} "$APT_LISTS_DIR/" \;
    fi
    if [ -d "$APT_CACHE_DIR/archives" ]; then
        find "$APT_CACHE_DIR/archives" -maxdepth 1 -name '*.deb' \
            -exec cp -a {} "$APT_ARCHIVES_DIR/" \;
    fi
}

apt_cache_save() {
    mkdir -p "$APT_CACHE_DIR/lists" "$APT_CACHE_DIR/archives"
    find "$APT_LISTS_DIR" -mindepth 1 -maxdepth 1 \
        ! -name lock ! -name partial ! -name auxfiles \
        -exec cp -a {} "$APT_CACHE_DIR/lists/" \;
    find "$APT_ARCHIVES_DIR" -maxdepth 1 -name '*.deb' \
        -exec cp -a {} "$APT_CACHE_DIR/archives/" \;
}

apt_install() {
    apt_cache_restore

    # The lists are compressed (*_Packages.lz4 on trixie), so match the
    # prefix rather than a bare filename.
    if ! find "$APT_LISTS_DIR" -maxdepth 1 -name '*_Packages*' -print -quit | grep -q .; then
        apt-get update -qq
    fi

    if ! apt-get install -y -qq --no-install-recommends "$@"; then
        # A restored index can be stale or a cached package can be gone;
        # refresh once and retry before failing the job.
        apt-get update -qq
        apt-get install -y -qq --no-install-recommends "$@"
    fi

    apt_cache_save
}
