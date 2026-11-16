import assert from 'node:assert/strict';
import test from 'node:test';
import { deploymentMode } from './build-deployment.mjs';
import { releaseCheckState } from './require-release-check.mjs';

test('Workers branch determines deployment mode and cannot be overridden', () => {
  assert.equal(deploymentMode({ WORKERS_CI_BRANCH: 'main' }), 'production');
  assert.equal(deploymentMode({ WORKERS_CI_BRANCH: 'feature/deployment' }), 'preview');
  assert.equal(deploymentMode({ DEPLOYMENT_MODE: 'preview' }), 'preview');
  assert.throws(() => deploymentMode({}));
  assert.throws(() => deploymentMode({ DEPLOYMENT_MODE: 'ci' }));
  assert.throws(() => deploymentMode({ WORKERS_CI_BRANCH: 'main', DEPLOYMENT_MODE: 'preview' }));
});

test('deployment waits for the latest genuine GitHub check and rejects failures', () => {
  const check = { id: 1, name: 'release-checks', app: { slug: 'github-actions' }, status: 'completed', conclusion: 'success' };
  assert.equal(releaseCheckState([]), 'pending');
  assert.equal(releaseCheckState([check]), 'passed');
  assert.equal(releaseCheckState([{ ...check, app: { slug: 'other' } }]), 'pending');
  assert.equal(releaseCheckState([check, { ...check, id: 2, status: 'in_progress' }]), 'pending');
  for (const conclusion of ['failure', 'cancelled', 'skipped', 'neutral', 'timed_out']) {
    assert.equal(releaseCheckState([check, { ...check, id: 2, conclusion }]), 'failed');
  }
});
