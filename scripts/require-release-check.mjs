import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { setTimeout } from 'node:timers/promises';

const repository = 'ngyathin16/harold-ng-portfolio';
export function releaseCheckState(checks) {
  const check = checks.filter((item) => item.name === 'release-checks' && item.app?.slug === 'github-actions')
    .sort((a, b) => b.id - a.id)[0];
  if (!check || check.status !== 'completed') return 'pending';
  return check.conclusion === 'success' ? 'passed' : 'failed';
}

async function github(path) {
  const response = await fetch(`https://api.github.com/repos/${repository}/${path}`, {
    headers: { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
    signal: AbortSignal.timeout(30_000),
  });
  assert(response.ok, `GitHub release gate: HTTP ${response.status}; no deployment permitted.`);
  return response.json();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const git = (...args) => execFileSync('git', args, { encoding: 'utf8' }).trim();
  const sha = process.env.WORKERS_CI_COMMIT_SHA || git('rev-parse', 'HEAD');
  const branch = process.env.WORKERS_CI_BRANCH || git('branch', '--show-current');
  assert.equal(branch, 'main', 'Production can deploy only main.');
  assert.match(sha, /^[a-f0-9]{40}$/, 'Expected a full Git commit.');
  assert.equal(git('rev-parse', 'HEAD'), sha, 'Build commit must match checkout.');
  assert.equal(git('status', '--porcelain', '--untracked-files=normal'), '', 'Production checkout must be clean.');
  const pulls = await github(`commits/${sha}/pulls`);
  assert(pulls.some((pr) => pr.merged_at && pr.base.ref === 'main' && pr.base.repo.full_name === repository && pr.merge_commit_sha === sha), 'Production requires a merged pull request; direct main pushes cannot deploy.');
  for (let attempt = 0; attempt < 20; attempt++) {
    const state = releaseCheckState((await github(`commits/${sha}/check-runs?per_page=100`)).check_runs);
    assert.notEqual(state, 'failed', 'Required release-checks failed; keeping the current release.');
    if (state === 'passed') {
      assert.equal((await github('commits/main')).sha, sha, 'A newer main commit superseded this build.');
      console.log(`GitHub release-checks passed for ${sha}.`);
      process.exit(0);
    }
    console.log(`Waiting for release-checks on ${sha} (${attempt + 1}/20).`);
    await setTimeout(30_000);
  }
  throw new Error('Timed out waiting for release-checks; retry the build after CI passes.');
}
