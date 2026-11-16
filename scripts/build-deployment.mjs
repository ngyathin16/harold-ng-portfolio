import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { validateOrigin, verifyRelease } from './verify-release.mjs';

export function deploymentMode(env) {
  const branchMode = env.WORKERS_CI_BRANCH ? (env.WORKERS_CI_BRANCH === 'main' ? 'production' : 'preview') : undefined;
  const mode = branchMode || env.DEPLOYMENT_MODE;
  assert(['production', 'preview'].includes(mode), 'Set DEPLOYMENT_MODE=production|preview outside Workers Builds.');
  assert(!branchMode || !env.DEPLOYMENT_MODE || env.DEPLOYMENT_MODE === branchMode, 'DEPLOYMENT_MODE conflicts with the Workers build branch.');
  return mode;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const mode = deploymentMode(process.env);
  if (mode === 'production') {
    assert.equal(validateOrigin(process.env.SITE_URL, 'SITE_URL'), validateOrigin(process.env.APPROVED_SITE_URL, 'APPROVED_SITE_URL'), 'SITE_URL must match the approved custom origin.');
  } else {
    assert(!process.env.SITE_URL, 'Preview builds must leave SITE_URL unset.');
  }
  // Use npm's own entry point so this works without shell quoting on Windows.
  const build = spawnSync(process.execPath, [process.env.npm_execpath, 'run', 'build'], { stdio: 'inherit' });
  assert.equal(build.status, 0, 'Astro build failed; nothing was deployed.');
  await verifyRelease({ mode, dist: 'dist', siteUrl: process.env.SITE_URL, approvedSiteUrl: process.env.APPROVED_SITE_URL });
  console.log(`Verified ${mode} deployment artifact.`);
}
