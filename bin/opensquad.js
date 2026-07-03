#!/usr/bin/env node
// opensquad — npm bootstrap for the Python `opensquad` CLI.
//
// What this script does:
//   1. Detects whether Python 3.11+ is on PATH.
//   2. Ensures the matching `opensquad==X.Y.Z` Python package is installed
//      (installs via pip on first run).
//   3. Forwards every CLI argument to the `opensquad` command.
//
// This is a thin wrapper. The real CLI lives in the Python package and
// gets installed on demand. See https://github.com/opensquad-ai/opensquad
// for the source of truth.

'use strict';

const { execFileSync, spawnSync } = require('child_process');
const path = require('path');

// The version field is replaced at publish time by the release-npm.yml
// workflow so it always tracks the latest tagged release.
const PKG_VERSION = require(path.join(__dirname, '..', 'package.json')).version;
const PYPI_PKG = 'opensquad';

function log(msg) {
  process.stderr.write(`[opensquad] ${msg}\n`);
}

function findPython() {
  for (const cmd of ['python3', 'python']) {
    try {
      const out = execFileSync(cmd, ['--version'], { stdio: 'pipe' }).toString();
      const m = out.match(/Python\s+(\d+)\.(\d+)/);
      if (m && parseInt(m[1], 10) >= 3 && parseInt(m[2], 10) >= 11) {
        return cmd;
      }
    } catch (_) {
      // try next candidate
    }
  }
  return null;
}

function isOpensquadInstalled(py) {
  try {
    execFileSync(py, ['-m', 'pip', 'show', PYPI_PKG], { stdio: 'pipe' });
    return true;
  } catch (_) {
    return false;
  }
}

function installedOpensquadVersion(py) {
  try {
    const out = execFileSync(
      py,
      ['-m', 'pip', 'show', PYPI_PKG],
      { stdio: 'pipe' }
    ).toString();
    const m = out.match(/^Version:\s*(\S+)/m);
    return m ? m[1] : null;
  } catch (_) {
    return null;
  }
}

function installOpensquad(py) {
  log(`installing ${PYPI_PKG}==${PKG_VERSION} via pip (first run only) ...`);
  const res = spawnSync(
    py,
    ['-m', 'pip', 'install', '--user', '--upgrade', `${PYPI_PKG}==${PKG_VERSION}`],
    { stdio: 'inherit' }
  );
  if (res.status !== 0) {
    log('pip install failed. Try running manually:');
    log(`  ${py} -m pip install --user ${PYPI_PKG}==${PKG_VERSION}`);
    process.exit(res.status || 1);
  }
}

function main() {
  const args = process.argv.slice(2);

  // --version / -v: report this bootstrap's version
  if (args.includes('--version') || args.includes('-v')) {
    process.stdout.write(`opensquad ${PKG_VERSION} (npm bootstrap)\n`);
    return;
  }

  // --help / -h
  if (args.length === 0 || args.includes('--help') || args.includes('-h')) {
    process.stdout.write(
      [
        `opensquad ${PKG_VERSION} (npm bootstrap)`,
        '',
        'Usage: opensquad [options] [command]',
        '',
        'This is the npm bootstrap. It ensures the Python `opensquad`',
        'package is installed and forwards all arguments to it.',
        '',
        'Options:',
        '  --version, -v   show bootstrap version',
        '  --help, -h      show this help',
        '',
        'All other arguments are passed to the `opensquad` Python CLI.',
        'See https://github.com/opensquad-ai/opensquad for full docs.',
        '',
      ].join('\n')
    );
    return;
  }

  const py = findPython();
  if (!py) {
    log('Python 3.11+ is required but was not found on PATH.');
    log('Install Python from https://www.python.org/downloads/');
    log('On macOS: brew install python@3.11');
    log('On Linux: use your package manager or pyenv');
    process.exit(1);
  }

  if (!isOpensquadInstalled(py)) {
    installOpensquad(py);
  } else {
    const installed = installedOpensquadVersion(py);
    if (installed && installed !== PKG_VERSION) {
      log(
        `installed version ${installed} != npm package version ${PKG_VERSION}; ` +
          `upgrading ...`
      );
      installOpensquad(py);
    }
  }

  // Forward everything to the real CLI
  const res = spawnSync('opensquad', args, { stdio: 'inherit' });
  process.exit(res.status === null ? 1 : res.status);
}

main();
