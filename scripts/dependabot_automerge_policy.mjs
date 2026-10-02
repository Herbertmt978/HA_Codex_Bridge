import { appendFile, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const AUTO_MERGE_DEV_TOOLS = new Set([
  '@axe-core/playwright',
  '@eslint/js',
  '@playwright/test',
  'eslint',
  'globals',
  'jsdom',
  'vitest',
]);

const AUTO_MERGE_ACTION_FILES = new Map([
  ['actions/setup-node', new Set(['.github/workflows/ci.yml'])],
  ['astral-sh/setup-uv', new Set([
    '.github/workflows/build-app.yml',
    '.github/workflows/ci.yml',
    '.github/workflows/codex-update.yml',
  ])],
]);

function parseChangedFiles(value) {
  if (!Array.isArray(value) || value.length === 0 || value.some((page) => !Array.isArray(page))) return null;
  const files = value.flat();
  if (files.length === 0 || files.some((file) => !file || typeof file.filename !== 'string' || !file.filename || file.filename.includes('\0'))) return null;
  return files.map((file) => file.filename);
}

export function actionFilesAreAllowed(dependencyName, changedFiles) {
  const allowedFiles = AUTO_MERGE_ACTION_FILES.get(dependencyName);
  return Array.isArray(changedFiles) && changedFiles.length > 0 && allowedFiles && changedFiles.every((path) => allowedFiles.has(path));
}

const stableVersionPattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

function stableVersion(value) {
  if (typeof value !== 'string') return null;
  const match = stableVersionPattern.exec(value);
  if (!match) return null;
  const version = match.slice(1).map(Number);
  return version.every(Number.isSafeInteger) ? version : null;
}

function stableSpec(value) {
  if (typeof value !== 'string') return null;
  const match = /^(\^|~)?((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$/.exec(value);
  const version = match && stableVersion(match[2]);
  return version ? { prefix: match[1] ?? '', text: match[2], version } : null;
}

function compareVersions(left, right) {
  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) return left[index] < right[index] ? -1 : 1;
  }
  return 0;
}

function versionSatisfiesSpec(spec, version) {
  const parsed = stableSpec(spec);
  if (!parsed) return false;
  const compared = compareVersions(version, parsed.version);
  if (!parsed.prefix) return compared === 0;
  if (parsed.prefix === '~') return version[0] === parsed.version[0] && version[1] === parsed.version[1] && compared >= 0;
  if (parsed.version[0] > 0) return version[0] === parsed.version[0] && compared >= 0;
  if (parsed.version[1] > 0) return version[0] === 0 && version[1] === parsed.version[1] && compared >= 0;
  return version[0] === 0 && version[1] === 0 && version[2] === parsed.version[2];
}

function updateMatches(oldVersion, newVersion, updateType) {
  if (compareVersions(newVersion, oldVersion) <= 0) return false;
  if (updateType === 'version-update:semver-patch') {
    return oldVersion[0] === newVersion[0] && oldVersion[1] === newVersion[1];
  }
  if (updateType === 'version-update:semver-minor') {
    return oldVersion[0] === newVersion[0] && oldVersion[1] < newVersion[1];
  }
  return false;
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function same(left, right) {
  return stableJson(left) === stableJson(right);
}

function withoutKey(object, key) {
  const result = { ...object };
  delete result[key];
  return result;
}

function packagePathFor(parentPath, dependencyName) {
  return `${parentPath ? `${parentPath}/` : ''}node_modules/${dependencyName}`;
}

function resolvePackage(packages, parentPath, dependencyName) {
  let cursor = parentPath;
  while (cursor) {
    const candidate = packagePathFor(cursor, dependencyName);
    if (Object.hasOwn(packages, candidate)) return candidate;
    const separator = cursor.lastIndexOf('/');
    cursor = separator < 0 ? '' : cursor.slice(0, separator);
  }
  const rootCandidate = packagePathFor('', dependencyName);
  return Object.hasOwn(packages, rootCandidate) ? rootCandidate : null;
}

function dependencyClosure(lock, roots) {
  const packages = lock.packages;
  const pending = [];
  for (const name of roots) {
    const path = resolvePackage(packages, '', name);
    if (!path) return null;
    pending.push(path);
  }

  const visited = new Set();
  while (pending.length) {
    const path = pending.pop();
    if (visited.has(path)) continue;
    visited.add(path);
    const record = packages[path];
    if (!record || typeof record !== 'object' || record.link === true) continue;
    for (const section of ['dependencies', 'optionalDependencies', 'peerDependencies']) {
      const dependencies = record[section] ?? {};
      if (!dependencies || typeof dependencies !== 'object' || Array.isArray(dependencies)) return null;
      for (const dependencyName of Object.keys(dependencies)) {
        const child = resolvePackage(packages, path, dependencyName);
        if (child) pending.push(child);
      }
    }
  }
  return visited;
}

export function reviewDataIsSafe(reviewData) {
  if (!reviewData || typeof reviewData !== 'object' || !Array.isArray(reviewData.reviews) || reviewData.reviews.length >= 100) {
    return false;
  }
  const allowedDecisions = new Set(['', 'APPROVED', 'CHANGES_REQUESTED', 'REVIEW_REQUIRED']);
  if (!Object.hasOwn(reviewData, 'reviewDecision') ||
      !(reviewData.reviewDecision === null || (typeof reviewData.reviewDecision === 'string' && allowedDecisions.has(reviewData.reviewDecision)))) return false;
  if (reviewData.reviewDecision === 'CHANGES_REQUESTED') return false;

  const decisive = [];
  for (const review of reviewData.reviews) {
    if (!review || typeof review !== 'object' || typeof review.author?.login !== 'string' || !review.author.login) return false;
    if (!['APPROVED', 'CHANGES_REQUESTED', 'COMMENTED', 'DISMISSED', 'PENDING'].includes(review.state)) return false;
    if (review.state === 'PENDING') {
      if (review.submittedAt != null) return false;
      continue;
    }
    if (typeof review.submittedAt !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(review.submittedAt)) return false;
    const parsedAt = new Date(review.submittedAt);
    if (Number.isNaN(parsedAt.valueOf()) || parsedAt.toISOString().replace('.000Z', 'Z') !== review.submittedAt) return false;
    if (['APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'].includes(review.state)) decisive.push(review);
  }

  const sameTimestamp = new Map();
  for (const review of decisive) {
    const key = `${review.author.login}\u0000${review.submittedAt}`;
    const previousState = sameTimestamp.get(key);
    if (previousState && previousState !== review.state) return false;
    sameTimestamp.set(key, review.state);
  }

  const byAuthor = new Map();
  for (const review of decisive) {
    const current = byAuthor.get(review.author.login);
    if (!current || review.submittedAt > current.submittedAt) byAuthor.set(review.author.login, review);
  }
  return [...byAuthor.values()].every((review) => review.state !== 'CHANGES_REQUESTED');
}

function onlyTargetSpecDiffers(baseSection, headSection, dependencyName, targetSpec) {
  const base = baseSection ?? {};
  const head = headSection ?? {};
  if (!base || typeof base !== 'object' || Array.isArray(base) || !head || typeof head !== 'object' || Array.isArray(head)) return false;
  if (base[dependencyName] === undefined || head[dependencyName] !== targetSpec) return false;
  return same(withoutKey(base, dependencyName), withoutKey(head, dependencyName));
}

function validateManifest(baseManifest, headManifest, dependencyName, newVersion) {
  if (!baseManifest || typeof baseManifest !== 'object' || !headManifest || typeof headManifest !== 'object') return false;
  if (!baseManifest.devDependencies || !headManifest.devDependencies) return false;
  const oldSpec = stableSpec(baseManifest.devDependencies[dependencyName]);
  if (!oldSpec) return false;

  const baseWithoutTarget = withoutKey(baseManifest.devDependencies, dependencyName);
  const headWithoutTarget = withoutKey(headManifest.devDependencies, dependencyName);
  if (!same(baseWithoutTarget, headWithoutTarget)) return false;
  if (!same(withoutKey(baseManifest, 'devDependencies'), withoutKey(headManifest, 'devDependencies'))) return false;

  const headSpec = headManifest.devDependencies[dependencyName];
  const headRange = stableSpec(headSpec);
  const targetVersion = stableVersion(newVersion);
  if (!headRange || !targetVersion) return false;
  if (headSpec !== baseManifest.devDependencies[dependencyName]) {
    if (headRange.prefix !== oldSpec.prefix || headRange.text !== newVersion) return false;
  } else if (!versionSatisfiesSpec(headSpec, targetVersion)) {
    return false;
  }
  return { oldSpec: baseManifest.devDependencies[dependencyName], headSpec };
}

function validateLockGraph(baseLock, headLock, dependencyName, oldManifestSpec, headManifestSpec, newVersion, manifestChanged) {
  if (!baseLock || !headLock || baseLock.lockfileVersion !== 3 || headLock.lockfileVersion !== 3) return false;
  if (!baseLock.packages || !headLock.packages || !baseLock.packages[''] || !headLock.packages['']) return false;
  if (!same(withoutKey(baseLock, 'packages'), withoutKey(headLock, 'packages'))) return false;

  const baseRoot = baseLock.packages[''];
  const headRoot = headLock.packages[''];
  const expectedHeadSpec = manifestChanged ? headManifestSpec : oldManifestSpec;
  if (!onlyTargetSpecDiffers(baseRoot.devDependencies, headRoot.devDependencies, dependencyName, expectedHeadSpec)) return false;
  if (!same(baseRoot.dependencies ?? {}, headRoot.dependencies ?? {})) return false;
  if (!same(withoutKey(baseRoot, 'devDependencies'), withoutKey(headRoot, 'devDependencies'))) return false;
  if (baseRoot.devDependencies[dependencyName] !== oldManifestSpec || headRoot.devDependencies[dependencyName] !== expectedHeadSpec) return false;

  const basePath = resolvePackage(baseLock.packages, '', dependencyName);
  const headPath = resolvePackage(headLock.packages, '', dependencyName);
  if (!basePath || !headPath) return false;
  const baseVersion = stableVersion(baseLock.packages[basePath].version);
  const headVersion = stableVersion(headLock.packages[headPath].version);
  const targetVersion = stableVersion(newVersion);
  if (!baseVersion || !headVersion || !targetVersion || compareVersions(headVersion, targetVersion) !== 0) return false;

  const baseTargetClosure = dependencyClosure(baseLock, [dependencyName]);
  const headTargetClosure = dependencyClosure(headLock, [dependencyName]);
  const productionRoots = Object.keys({
    ...(baseRoot.dependencies ?? {}),
    ...(baseRoot.optionalDependencies ?? {}),
    ...(baseRoot.peerDependencies ?? {}),
  });
  const baseProductionClosure = dependencyClosure(baseLock, productionRoots);
  const headProductionClosure = dependencyClosure(headLock, productionRoots);
  const generatedRoots = ['esbuild', 'pdfjs-dist'].filter((name) => Object.hasOwn(baseRoot.devDependencies ?? {}, name));
  const baseGeneratedClosure = dependencyClosure(baseLock, generatedRoots);
  const headGeneratedClosure = dependencyClosure(headLock, generatedRoots);
  if (!baseTargetClosure || !headTargetClosure || !baseProductionClosure || !headProductionClosure ||
      !baseGeneratedClosure || !headGeneratedClosure) return false;

  const directDependencies = new Set([
    ...Object.keys(baseRoot.dependencies ?? {}),
    ...Object.keys(baseRoot.optionalDependencies ?? {}),
    ...Object.keys(baseRoot.peerDependencies ?? {}),
    ...Object.keys(baseRoot.devDependencies ?? {}),
  ]);
  directDependencies.delete(dependencyName);
  for (const name of directDependencies) {
    const basePath = resolvePackage(baseLock.packages, '', name);
    const headPath = resolvePackage(headLock.packages, '', name);
    const baseRecord = basePath && baseLock.packages[basePath];
    const headRecord = headPath && headLock.packages[headPath];
    if (!baseRecord || !headRecord || typeof baseRecord.version !== 'string' || baseRecord.version !== headRecord.version) return false;
  }

  const targetReachable = new Set([...baseTargetClosure, ...headTargetClosure]);
  const protectedReachable = new Set([
    ...baseProductionClosure,
    ...headProductionClosure,
    ...baseGeneratedClosure,
    ...headGeneratedClosure,
  ]);
  const packagePaths = new Set([...Object.keys(baseLock.packages), ...Object.keys(headLock.packages)]);
  let changedTargetRecord = false;
  for (const path of packagePaths) {
    if (path === '') continue;
    if (same(baseLock.packages[path], headLock.packages[path])) continue;
    if (!targetReachable.has(path) || protectedReachable.has(path)) return false;
    if (path === basePath || path === headPath) changedTargetRecord = true;
  }
  return changedTargetRecord && compareVersions(headVersion, baseVersion) > 0;
}

export function evaluateDependabotUpdate({ metadata, changedFiles, baseManifest, headManifest, baseLock, headLock, reviewData }) {
  const reject = (reason) => ({ eligible: false, reason });
  if (!metadata || metadata.packageEcosystem !== 'npm_and_yarn' || metadata.directory !== '/') return reject('Unsupported ecosystem or directory.');
  if (metadata.dependencyGroup !== '') return reject('Grouped dependency updates are not eligible.');
  if (metadata.maintainerChanges !== 'false') return reject('Maintainer changes metadata is not explicitly false.');
  if (!['version-update:semver-patch', 'version-update:semver-minor'].includes(metadata.updateType)) return reject('Only stable patch and minor updates are eligible.');
  const newVersion = stableVersion(metadata.newVersion);
  if (!newVersion) return reject('Target version is not stable three-part SemVer.');
  const names = typeof metadata.dependencyNames === 'string'
    ? metadata.dependencyNames.split(',').map((name) => name.trim()).filter(Boolean)
    : [];
  if (names.length !== 1 || !AUTO_MERGE_DEV_TOOLS.has(names[0])) return reject('Dependency is not a single allowlisted development tool.');
  if (!reviewDataIsSafe(reviewData)) return reject('Review data is missing, malformed, or has an unresolved change request.');

  const dependencyName = names[0];
  const manifestSpecs = validateManifest(baseManifest, headManifest, dependencyName, metadata.newVersion);
  if (!manifestSpecs) return reject('Manifest changes are not limited to the target development dependency.');
  if (!Array.isArray(changedFiles)) return reject('Changed-file data is missing or malformed.');
  const manifestChanged = baseManifest.devDependencies[dependencyName] !== headManifest.devDependencies[dependencyName];
  if (manifestChanged !== changedFiles.includes('package.json')) return reject('Manifest diff does not match the exact changed-file list.');
  const expectedFiles = manifestChanged ? ['package-lock.json', 'package.json'] : ['package-lock.json'];
  if (!Array.isArray(changedFiles) || changedFiles.length !== expectedFiles.length || !expectedFiles.every((path) => changedFiles.includes(path))) {
    return reject('Changed files are not limited to the expected npm manifest and lockfile.');
  }
  if (!validateLockGraph(baseLock, headLock, dependencyName, manifestSpecs.oldSpec, manifestSpecs.headSpec, metadata.newVersion, manifestChanged)) {
    return reject('Lockfile changes do not match the target development dependency graph.');
  }
  const baseLockedVersion = stableVersion(baseLock.packages[resolvePackage(baseLock.packages, '', dependencyName)]?.version);
  if (!baseLockedVersion || !updateMatches(baseLockedVersion, newVersion, metadata.updateType)) return reject('Lockfile version change does not match the declared patch/minor update.');
  return { eligible: true, reason: 'Single allowlisted stable development patch/minor update with a scoped lock graph.' };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args[0] === '--review-only') {
    const reviewData = JSON.parse(await readFile(args[1], 'utf8'));
    if (!reviewDataIsSafe(reviewData)) {
      console.error('Review data is missing, malformed, or has an unresolved change request.');
      process.exitCode = 1;
    } else {
      console.log('No unresolved change request is present.');
    }
  } else if (args[0] === '--actions-files') {
    const changedFiles = parseChangedFiles(JSON.parse(await readFile(args[2], 'utf8')));
    if (!actionFilesAreAllowed(args[1], changedFiles)) {
      console.error('Changed files are not limited to the approved workflow paths for this action.');
      process.exitCode = 1;
    } else {
      console.log('Changed files are limited to the approved workflow paths.');
    }
  } else {
    const [baseManifestPath, headManifestPath, baseLockPath, headLockPath, changedFilesPath, reviewDataPath, outputPath] = args;
    if (![baseManifestPath, headManifestPath, baseLockPath, headLockPath, changedFilesPath, reviewDataPath, outputPath].every(Boolean)) {
      console.error('Expected paths for base/head manifest, base/head lockfile, changed files, review data, and GitHub step output.');
      process.exitCode = 2;
    } else {
      const [baseManifest, headManifest, baseLock, headLock, changedFilesText, reviewText] = await Promise.all([
        readFile(baseManifestPath, 'utf8'), readFile(headManifestPath, 'utf8'),
        readFile(baseLockPath, 'utf8'), readFile(headLockPath, 'utf8'),
        readFile(changedFilesPath, 'utf8'), readFile(reviewDataPath, 'utf8'),
      ]);
      const changedFiles = parseChangedFiles(JSON.parse(changedFilesText));
      const metadata = {
        packageEcosystem: process.env.PACKAGE_ECOSYSTEM,
        directory: process.env.DIRECTORY,
        dependencyGroup: process.env.DEPENDENCY_GROUP,
        dependencyNames: process.env.DEPENDENCY_NAMES,
        maintainerChanges: process.env.MAINTAINER_CHANGES,
        newVersion: process.env.NEW_VERSION,
        updateType: process.env.UPDATE_TYPE,
      };
      const result = evaluateDependabotUpdate({
        metadata,
        changedFiles,
        baseManifest: JSON.parse(baseManifest),
        headManifest: JSON.parse(headManifest),
        baseLock: JSON.parse(baseLock),
        headLock: JSON.parse(headLock),
        reviewData: JSON.parse(reviewText),
      });
      console.log(result.reason);
      await appendFile(outputPath, `eligible=${result.eligible}\n`, 'utf8');
    }
  }
}
