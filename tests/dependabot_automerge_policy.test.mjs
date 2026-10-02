import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { actionFilesAreAllowed, evaluateDependabotUpdate } from '../scripts/dependabot_automerge_policy.mjs';

function fixture({ updateType = 'version-update:semver-minor', oldVersion = '10.0.0', newVersion = '10.1.0',
  oldSpec = `^${oldVersion}`, newSpec = `^${newVersion}`, dependencyName = 'eslint', changedFiles,
  runtimeUsesShared = false, generatedTool, directVersionChange = false, unrelatedLockChange = false, lockfileVersion = 3 } = {}) {
  const baseManifest = {
    name: 'ha-codex-bridge',
    dependencies: { katex: '0.18.9' },
    devDependencies: { [dependencyName]: oldSpec, globals: '^17.0.0', ...(generatedTool ? { [generatedTool]: '1.0.0' } : {}) },
  };
  const headManifest = structuredClone(baseManifest);
  headManifest.devDependencies[dependencyName] = newSpec;

  const baseLock = {
    name: 'ha-codex-bridge',
    lockfileVersion,
    packages: {
      '': { dependencies: { katex: '0.18.9' }, devDependencies: { [dependencyName]: oldSpec, globals: '^17.0.0', ...(generatedTool ? { [generatedTool]: '1.0.0' } : {}) } },
      'node_modules/katex': { version: '0.18.9', ...(runtimeUsesShared ? { dependencies: { 'eslint-shared': '^1.0.0' } } : {}) },
      [`node_modules/${dependencyName}`]: { version: oldVersion, dependencies: { 'eslint-shared': '^1.0.0' } },
      'node_modules/eslint-shared': { version: '1.0.0' },
      'node_modules/globals': { version: '17.0.0' },
      ...(generatedTool ? { [`node_modules/${generatedTool}`]: { version: '1.0.0', dependencies: { 'eslint-shared': '^1.0.0' } } } : {}),
    },
  };
  const headLock = structuredClone(baseLock);
  headLock.packages[''].devDependencies[dependencyName] = newSpec;
  headLock.packages[`node_modules/${dependencyName}`].version = newVersion;
  headLock.packages['node_modules/eslint-shared'].version = '1.1.0';
  if (directVersionChange) headLock.packages['node_modules/globals'].version = '17.1.0';
  if (unrelatedLockChange) headLock.packages['node_modules/unrelated'] = { version: '2.0.0' };

  return {
    metadata: {
      packageEcosystem: 'npm_and_yarn',
      directory: '/',
      dependencyGroup: '',
      dependencyNames: dependencyName,
      maintainerChanges: 'false',
      newVersion,
      updateType,
    },
    changedFiles: changedFiles ?? (newSpec === oldSpec ? ['package-lock.json'] : ['package.json', 'package-lock.json']),
    baseManifest,
    headManifest,
    baseLock,
    headLock,
    reviewData: { reviewDecision: '', reviews: [] },
  };
}

test('allows an allowlisted development patch when only the lockfile changes', () => {
  const input = fixture({ updateType: 'version-update:semver-patch', oldVersion: '10.0.0', newVersion: '10.0.1', oldSpec: '^10.0.0', newSpec: '^10.0.0' });
  const result = evaluateDependabotUpdate(input);
  assert.equal(result.eligible, true, result.reason);
});

test('allows an allowlisted development minor with a scoped transitive lock change', () => {
  const result = evaluateDependabotUpdate(fixture());
  assert.equal(result.eligible, true, result.reason);
});

test('allows a scoped package name in the development allowlist', () => {
  const input = fixture({ dependencyName: '@eslint/js', oldVersion: '10.0.0', newVersion: '10.1.0' });
  const result = evaluateDependabotUpdate(input);
  assert.equal(result.eligible, true, result.reason);
});

test('accepts a scoped patch using the repository lockfile graph', async () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const baseManifest = JSON.parse(await readFile(resolve(root, 'package.json'), 'utf8'));
  const baseLock = JSON.parse(await readFile(resolve(root, 'package-lock.json'), 'utf8'));
  const dependencyName = '@axe-core/playwright';
  const path = `node_modules/${dependencyName}`;
  const oldVersion = baseLock.packages[path]?.version;
  assert.match(oldVersion, /^\d+\.\d+\.\d+$/);
  const [major, minor, patch] = oldVersion.split('.').map(Number);
  const newVersion = `${major}.${minor}.${patch + 1}`;
  const headManifest = structuredClone(baseManifest);
  const headLock = structuredClone(baseLock);
  headManifest.devDependencies[dependencyName] = newVersion;
  headLock.packages[''].devDependencies[dependencyName] = newVersion;
  headLock.packages[path].version = newVersion;

  const result = evaluateDependabotUpdate({
    metadata: {
      packageEcosystem: 'npm_and_yarn',
      directory: '/',
      dependencyGroup: '',
      dependencyNames: dependencyName,
      maintainerChanges: 'false',
      newVersion,
      updateType: 'version-update:semver-patch',
    },
    changedFiles: ['package.json', 'package-lock.json'],
    baseManifest,
    headManifest,
    baseLock,
    headLock,
    reviewData: { reviewDecision: '', reviews: [] },
  });
  assert.equal(result.eligible, true, result.reason);
});

test('keeps major updates manual', () => {
  const input = fixture({ updateType: 'version-update:semver-major', oldVersion: '10.0.0', newVersion: '11.0.0', oldSpec: '^10.0.0', newSpec: '^11.0.0' });
  assert.equal(evaluateDependabotUpdate(input).eligible, false);
});

test('rejects prerelease or malformed target versions', () => {
  const input = fixture({ newVersion: '10.1.0-rc.1', newSpec: '^10.1.0-rc.1' });
  assert.equal(evaluateDependabotUpdate(input).eligible, false);
});

test('fails closed for true, empty, and missing maintainer-change metadata', () => {
  for (const value of ['true', '', undefined]) {
    const input = fixture();
    input.metadata.maintainerChanges = value;
    assert.equal(evaluateDependabotUpdate(input).eligible, false);
  }
});

test('rejects unsupported ecosystems, directories, groups, and multiple dependency names', () => {
  for (const edit of [
    (metadata) => { metadata.packageEcosystem = 'github_actions'; },
    (metadata) => { metadata.directory = '/bridge_service'; },
    (metadata) => { metadata.dependencyGroup = 'development-tools'; },
    (metadata) => { metadata.dependencyNames = 'eslint, globals'; },
  ]) {
    const input = fixture();
    edit(input.metadata);
    assert.equal(evaluateDependabotUpdate(input).eligible, false);
  }
});

test('limits Actions auto-merge to the matching workflow paths and exact filenames', () => {
  assert.equal(actionFilesAreAllowed('actions/setup-node', ['.github/workflows/ci.yml']), true);
  assert.equal(actionFilesAreAllowed('astral-sh/setup-uv', [
    '.github/workflows/build-app.yml',
    '.github/workflows/ci.yml',
  ]), true);
  assert.equal(actionFilesAreAllowed('actions/setup-node', ['.github/workflows/ci.yml', 'README.md']), false);
  assert.equal(actionFilesAreAllowed('actions/setup-node', ['.github/workflows/ci.yml\nREADME.md']), false);
});

test('rejects build tools, runtime dependencies, and other unapproved packages', () => {
  for (const dependencyName of ['esbuild', 'pdfjs-dist', 'katex', '@xterm/xterm', 'left-pad']) {
    assert.equal(evaluateDependabotUpdate(fixture({ dependencyName })).eligible, false, dependencyName);
  }
});

test('rejects extra or missing changed paths and runtime manifest changes', () => {
  const extra = fixture({ changedFiles: ['package.json', 'package-lock.json', 'frontend/src/app.js'] });
  assert.equal(evaluateDependabotUpdate(extra).eligible, false);

  const missing = fixture({ changedFiles: ['package.json'] });
  assert.equal(evaluateDependabotUpdate(missing).eligible, false);

  const runtimeChange = fixture();
  runtimeChange.headManifest.dependencies.katex = '0.19.0';
  assert.equal(evaluateDependabotUpdate(runtimeChange).eligible, false);

  const otherDevelopmentChange = fixture();
  otherDevelopmentChange.headManifest.devDependencies.globals = '^18.0.0';
  assert.equal(evaluateDependabotUpdate(otherDevelopmentChange).eligible, false);
});

test('rejects unrelated or production-reachable lock graph changes', () => {
  assert.equal(evaluateDependabotUpdate(fixture({ unrelatedLockChange: true })).eligible, false);
  assert.equal(evaluateDependabotUpdate(fixture({ runtimeUsesShared: true })).eligible, false);
});

test('rejects changes shared with generated bundle tools and other direct dependency versions', () => {
  for (const generatedTool of ['esbuild', 'pdfjs-dist']) {
    assert.equal(evaluateDependabotUpdate(fixture({ generatedTool })).eligible, false, generatedTool);
  }
  assert.equal(evaluateDependabotUpdate(fixture({ directVersionChange: true })).eligible, false);
});

test('rejects non-v3 lockfiles and target versions that do not match the declared update level', () => {
  assert.equal(evaluateDependabotUpdate(fixture({ lockfileVersion: 2 })).eligible, false);

  const badPatch = fixture({ updateType: 'version-update:semver-patch', oldVersion: '10.0.0', newVersion: '10.1.0', oldSpec: '^10.0.0', newSpec: '^10.1.0' });
  assert.equal(evaluateDependabotUpdate(badPatch).eligible, false);
});

test('holds the queue when a latest review decision requests changes', () => {
  const input = fixture();
  input.reviewData = {
    reviewDecision: 'CHANGES_REQUESTED',
    reviews: [{ author: { login: 'reviewer' }, state: 'CHANGES_REQUESTED', submittedAt: '2026-10-02T00:00:00Z' }],
  };
  assert.equal(evaluateDependabotUpdate(input).eligible, false);
});

test('latest reviewer state wins, while GitHub keeps pending merge requirements authoritative', () => {
  const superseded = fixture();
  superseded.reviewData = {
    reviewDecision: 'REVIEW_REQUIRED',
    reviews: [
      { author: { login: 'reviewer' }, state: 'CHANGES_REQUESTED', submittedAt: '2026-10-02T00:00:00Z' },
      { author: { login: 'reviewer' }, state: 'APPROVED', submittedAt: '2026-10-02T00:01:00Z' },
    ],
  };
  assert.equal(evaluateDependabotUpdate(superseded).eligible, true);

  const pending = fixture();
  pending.reviewData = {
    reviewDecision: 'REVIEW_REQUIRED',
    reviews: [{ author: { login: 'reviewer' }, state: 'PENDING', submittedAt: null }],
  };
  assert.equal(evaluateDependabotUpdate(pending).eligible, true);
});
