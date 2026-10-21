#!/usr/bin/env node
/**
 * Delete old release tags from the container registry by tag.
 *
 * ``docker:cleanup_old_images`` calls this on a release tag so the registry
 * holds the newest ``--keep`` builds of an image and not every build ever
 * shipped. It talks to the GitLab container-registry API rather than the docker
 * CLI, because the images are built and pushed by a *different* job: a cleanup
 * job's own daemon never holds them, and ``docker rmi`` on a name it has not
 * pulled cannot delete anything in the registry either way.
 *
 * Why this exists at all: the job it replaces listed ``docker image ls`` of the
 * job's own daemon (always empty) and split tags into alpha/beta/release
 * buckets that the ``a.X.Y-<build>`` scheme does not have, so its retention
 * policy could only ever print "Keeping all release tags" and exit 0. A policy
 * that cannot delete anything is not a policy, it is a claim.
 *
 * Only tags that match the release pattern are candidates, ``latest`` and any
 * other moving tag is never touched, and the newest ``--keep`` are kept, so the
 * blast radius is superseded builds of the named image.
 *
 * Usage:
 *   node scripts/registry-cleanup.cjs --path plexichat/plexichat-client/nginx \
 *       [--keep 20] [--dry-run]
 *
 * In CI the defaults come from the environment: ``CI_PROJECT_ID`` and
 * ``CI_API_V4_URL`` locate the project, and the token is
 * ``REGISTRY_CLEANUP_TOKEN`` when the project defines one (a project access
 * token with the ``api`` scope, sent as ``PRIVATE-TOKEN``) or the job token
 * otherwise (sent as ``JOB-TOKEN``, which carries the scope on only some
 * GitLab versions -- the job says so when the API refuses).
 *
 * Exit codes: 0 when the policy was applied (including "nothing to delete"),
 * 1 when the API refused a delete, 2 when the request itself is wrong (no such
 * repository, missing argument).
 */
'use strict';

const DEFAULT_KEEP = 20;
const DEFAULT_PATTERN = '^a\\.(\\d+)\\.(\\d+)-(\\d+)$';
const PER_PAGE = 100;

/**
 * The release version of a tag, or null when the tag is not a release tag.
 *
 * @param {string} name - A registry tag.
 * @param {string} pattern - A regular expression with three capture groups
 *     (major, minor, build). Anything it does not match is not a release tag
 *     and is never deleted.
 * @returns {{major: number, minor: number, build: number}|null}
 */
function parseReleaseTag(name, pattern = DEFAULT_PATTERN) {
    const match = new RegExp(pattern).exec(name);
    if (!match) return null;
    return {
        major: Number(match[1]),
        minor: Number(match[2]),
        build: Number(match[3]),
    };
}

/**
 * The tags a keep-newest-N policy deletes, oldest first.
 *
 * The comparison is numeric per component: a string sort puts ``a.1.0-117``
 * before ``a.1.0-9``, which would delete the newest build and keep the oldest.
 *
 * @param {string[]} names - Every tag the repository has.
 * @param {{keep?: number, pattern?: string}} [options]
 * @returns {string[]} Tags to delete; empty when the repository is within its
 *     retention, which is the normal case.
 */
function selectStaleTags(names, options = {}) {
    const keep = options.keep === undefined ? DEFAULT_KEEP : options.keep;
    const pattern = options.pattern || DEFAULT_PATTERN;
    const releases = [];
    for (const name of names) {
        const version = parseReleaseTag(name, pattern);
        if (version) releases.push({ name, version });
    }
    releases.sort((a, b) => (b.version.major - a.version.major)
        || (b.version.minor - a.version.minor)
        || (b.version.build - a.version.build));
    if (releases.length <= keep) return [];
    return releases.slice(keep).map((entry) => entry.name).reverse();
}

function parseArgs(argv) {
    const options = {
        path: null,
        keep: DEFAULT_KEEP,
        pattern: DEFAULT_PATTERN,
        dryRun: false,
        project: process.env.CI_PROJECT_ID || null,
        apiBase: process.env.CI_API_V4_URL
            ? `${process.env.CI_API_V4_URL}/projects/${process.env.CI_PROJECT_ID || ''}`
            : null,
        token: process.env.REGISTRY_CLEANUP_TOKEN || process.env.CI_JOB_TOKEN || null,
        tokenHeader: process.env.REGISTRY_CLEANUP_TOKEN ? 'PRIVATE-TOKEN' : 'JOB-TOKEN',
    };
    for (let i = 0; i < argv.length; i += 1) {
        const flag = argv[i];
        const value = argv[i + 1];
        const needsValue = () => {
            if (value === undefined) throw new Error(`${flag} needs a value`);
            i += 1;
            return value;
        };
        if (flag === '--path') options.path = needsValue();
        else if (flag === '--keep') {
            options.keep = Number.parseInt(needsValue(), 10);
            // At least one: ``--keep 0`` would delete every release tag the
            // registry has, which is not a retention policy and not something a
            // mistyped variable should be able to do.
            if (!Number.isInteger(options.keep) || options.keep < 1) {
                throw new Error('--keep must be a positive integer');
            }
        } else if (flag === '--pattern') options.pattern = needsValue();
        else if (flag === '--project') options.project = needsValue();
        else if (flag === '--api-base') options.apiBase = needsValue();
        else if (flag === '--token') {
            options.token = needsValue();
            options.tokenHeader = 'PRIVATE-TOKEN';
        } else if (flag === '--dry-run') options.dryRun = true;
        else throw new Error(`unknown argument: ${flag}`);
    }
    if (!options.path) throw new Error('--path <registry repository path> is required');
    if (!options.apiBase) {
        throw new Error('--api-base is required outside CI (CI_API_V4_URL is not set)');
    }
    if (!options.token) {
        throw new Error('no token: set REGISTRY_CLEANUP_TOKEN or CI_JOB_TOKEN');
    }
    return options;
}

/**
 * The token header for every request. One place, so a request cannot be sent
 * without credentials.
 * @param {{token: string, tokenHeader: string}} options
 * @returns {Record<string, string>}
 */
function authHeaders(options) {
    return {
        [options.tokenHeader]: options.token,
        Accept: 'application/json',
    };
}

/**
 * Follow the API's pagination to collect every item of a list endpoint.
 * @param {string} url - The first page's URL.
 * @param {{token: string, tokenHeader: string}} options
 * @returns {Promise<unknown[]>}
 */
async function listAll(url, options) {
    const items = [];
    let next = `${url}${url.includes('?') ? '&' : '?'}per_page=${PER_PAGE}`;
    while (next) {
        const response = await fetch(next, { headers: authHeaders(options) });
        if (!response.ok) {
            throw new Error(`GET ${next} failed: ${response.status} ${response.statusText}`);
        }
        const page = await response.json();
        if (!Array.isArray(page)) {
            throw new Error(`GET ${next} did not return a list`);
        }
        items.push(...page);
        const nextPage = response.headers.get('x-next-page');
        next = nextPage ? `${url}${url.includes('?') ? '&' : '?'}per_page=${PER_PAGE}&page=${nextPage}` : '';
    }
    return items;
}

/**
 * Find the registry repository the API knows by this path.
 * @param {object} options
 * @returns {Promise<{id: number, path: string}>}
 */
async function findRepository(options) {
    const repositories = await listAll(`${options.apiBase}/registry/repositories`, options);
    const known = repositories.filter((entry) => entry && typeof entry === 'object');
    const match = known.find((entry) => entry.path === options.path);
    if (!match) {
        // Name what the registry does have: a renamed or moved image would
        // otherwise leave this job reporting a clean registry forever.
        const paths = known.map((entry) => entry.path).join(', ') || '(none)';
        throw new Error(`no registry repository at '${options.path}'; the project has: ${paths}`);
    }
    return match;
}

async function main(argv) {
    let options;
    try {
        options = parseArgs(argv);
    } catch (error) {
        console.error(`registry-cleanup: ${error.message}`);
        console.error('Usage: node scripts/registry-cleanup.cjs --path <path> [--keep 20] [--dry-run]');
        return 2;
    }

    let repository;
    let tags;
    try {
        repository = await findRepository(options);
        const listed = await listAll(
            `${options.apiBase}/registry/repositories/${repository.id}/tags`,
            options,
        );
        tags = listed.map((entry) => (entry && entry.name) || '').filter(Boolean);
    } catch (error) {
        console.error(`registry-cleanup: ${error.message}`);
        return 2;
    }

    const stale = selectStaleTags(tags, { keep: options.keep, pattern: options.pattern });
    const releases = tags.filter((name) => parseReleaseTag(name, options.pattern) !== null).length;
    if (stale.length === 0) {
        console.log(
            `registry-cleanup: nothing to delete (${releases} release tag(s) at `
            + `${repository.path}, keeping ${options.keep})`,
        );
        return 0;
    }

    console.log(
        `registry-cleanup: ${repository.path} has ${releases} release tag(s); keeping the `
        + `newest ${options.keep} and deleting ${stale.length}`,
    );

    let failed = 0;
    for (const name of stale) {
        if (options.dryRun) {
            console.log(`  would delete ${name}`);
            continue;
        }
        const url = `${options.apiBase}/registry/repositories/${repository.id}/tags/${encodeURIComponent(name)}`;
        try {
            const response = await fetch(url, { method: 'DELETE', headers: authHeaders(options) });
            // A tag that is already gone is the desired state, not a failure.
            if (response.ok || response.status === 404) {
                console.log(`  deleted ${name}`);
            } else {
                failed += 1;
                console.error(`  FAILED to delete ${name}: ${response.status} ${response.statusText}`);
                if (response.status === 401 || response.status === 403) {
                    console.error(
                        '    the token lacks the api scope: set REGISTRY_CLEANUP_TOKEN to a '
                        + 'project access token with the api scope, masked, in the project settings',
                    );
                }
            }
        } catch (error) {
            failed += 1;
            console.error(`  FAILED to delete ${name}: ${error.message}`);
        }
    }

    if (failed) {
        console.error(`registry-cleanup: ${failed} tag(s) could not be deleted`);
        return 1;
    }
    console.log(
        options.dryRun
            ? `registry-cleanup: dry run, ${stale.length} tag(s) left alone`
            : `registry-cleanup: deleted ${stale.length} tag(s)`,
    );
    return 0;
}

module.exports = {
    DEFAULT_KEEP,
    DEFAULT_PATTERN,
    parseReleaseTag,
    selectStaleTags,
    parseArgs,
};

if (require.main === module) {
    main(process.argv.slice(2)).then(
        (code) => { process.exitCode = code; },
        (error) => {
            console.error(`registry-cleanup: ${error.message}`);
            process.exitCode = 1;
        },
    );
}
