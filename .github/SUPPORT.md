# Getting Support

Thanks for using OpenSquad! Here is how to get help, in order of preference.

## 1. Documentation (read first)

- **English** — [`README.md`](../../README.md) ·
  [`doc_en/`](../../tree/main/doc_en) ·
  [`CONTRIBUTING.md`](../../blob/main/CONTRIBUTING.md) ·
  [`BRANCHING.md`](../../blob/main/BRANCHING.md)
- **中文** — [`README_ZH.md`](../../blob/main/README_ZH.md) ·
  [`doc_cn/`](../../tree/main/doc_cn) ·
  [`CONTRIBUTING_ZH.md`](../../blob/main/CONTRIBUTING_ZH.md) ·
  [`BRANCHING_ZH.md`](../../blob/main/BRANCHING_ZH.md)

Most "how do I …?" questions are already answered in the docs. Please
skim the relevant guide first — `doc_en/getting_started.md` /
`doc_cn/getting_started.md` for first-time setup, and
`doc_en/agent_management.md` / `doc_cn/agent_management.md` for
per-agent configuration.

## 2. GitHub Discussions (questions, ideas, show-and-tell)

For "how do I configure X?", "is this pattern supported?", or
"here's what I built", open a thread in
[GitHub Discussions](https://github.com/opensquad-ai/opensquad/discussions).
Discussions are visible to everyone and are searchable — your question
is likely to help the next person.

## 3. GitHub Issues (concrete bugs, actionable feature requests)

For something you can describe as a defect or a concrete change
("this returns 500 when …", "please add a `--dry-run` flag to
`opensquad up`"), open a
[GitHub Issue](https://github.com/opensquad-ai/opensquad/issues).
Use the issue templates (`bug_report.md`, `feature_request.md`) so
the maintainer can reproduce / scope quickly.

Before opening an issue, search existing issues and discussions to
avoid duplicates.

## 4. Security (private disclosure)

**Do not open a public issue for security vulnerabilities.** Follow
[`SECURITY.md`](../../blob/main/SECURITY.md) — there is a private
contact channel for sensitive reports. Public disclosure before a
fix lands makes it harder to protect users.

## What this repo does *not* support

- **Live chat / DM support.** The maintainer does not run a Discord,
  Slack, or Telegram support channel tied to the project. Use
  Discussions or Issues above.
- **Custom agent development on commission.** OpenSquad is open
  source; you build your own agents / plugins / skills. For
  one-off help, open a Discussion; for ongoing collaboration,
  a PR is the right path.
- **Production-grade SLA / 24×7 ops.** OpenSquad is provided
  "as is" under the MIT license. There is no paid support tier.

## Response time

The project is maintained by a single person. Expect responses on the
order of days, not hours. If your question is urgent (production
outage), please consider whether OpenSquad is the right tool for a
hard-SLA workload — for that, commercial support offerings from a
third party may be a better fit.
