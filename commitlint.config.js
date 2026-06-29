// commitlint configuration — Conventional Commits type list (reference).
//
// ⚠️  No tool in this repo actually loads this file. It is kept as a
// human-readable reference for the allowed commit types. The real
// enforcement happens in two places, each with its OWN copy of the
// type list that must be kept in sync by hand:
//   1. .github/workflows/pr-title.yml — `action-semantic-pull-request`
//      checks PR titles (inline `types:` list).
//   2. .pre-commit-config.yaml — `conventional-pre-commit` checks
//      commit messages (uses its default type set).
//
// If you change the allowed types here, update both of those too.
// Rule reference: https://www.conventionalcommits.org/en/v1.0.0/
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    // The PR template already restricts to the types below; this
    // list is the source of truth for both commit messages and
    // (via action-semantic-pull-request) PR titles.
    'type-enum': [
      2,
      'always',
      [
        'feat',
        'fix',
        'docs',
        'style',
        'refactor',
        'perf',
        'test',
        'build',
        'ci',
        'chore',
        'revert',
      ],
    ],
    // Subject must be lowercase, no trailing period, ≤ 72 chars.
    'subject-case': [2, 'always', 'lower-case'],
    'subject-full-stop': [2, 'never', '.'],
    'header-max-length': [2, 'always', 72],
    // Body lines ≤ 100 chars (matches project docs convention).
    'body-max-line-length': [2, 'always', 100, 'soft'],
    // Scope is required for the project's modular feel
    // (e.g. "feat(gateway): …"). Set to 0 if your team prefers
    // global-only commits.
    'scope-empty': [0],
  },
};
