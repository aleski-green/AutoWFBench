# Realistic environment WIP scope

This commit packages the existing realistic environment and its API, Docker worker, protected verifier, challenge assets, tests, and demo. It does not change the benchmark lock or existing challenge scorecards.

The full Desktop working copy also modifies cli.py and engine.py for inspection/external scoring and adds inspection tooling. Those are outside the minimal standard-Engine integration and are intentionally not included. Unrelated composer deletions, local n8n bridge files, scorecard proposals, credentials, results, and backups remain untouched.

The full Desktop copy fails the lock check for four files: contracts.py, cli.py, engine.py, environment.py. This minimal commit changes two locked files: contracts.py and environment.py. The original lock remains unchanged, so this is a proposed benchmark extension, not an approved locked baseline. The benchmark owner must review the extension separately.

Validation of this isolated commit: 44 tests passed with AWB_TEST_DOCKER=1, including actual container tests. The first Docker preflight found the pinned image unavailable; the exact unchanged digest was provisioned before the successful rerun. Lock verification is intentionally not passing for the two documented baseline differences.
