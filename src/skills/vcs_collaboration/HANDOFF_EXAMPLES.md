# VCS Collaboration: Agent Handoff Examples

When a multi-agent team collaborates using Git, refer to the following communication patterns to ensure smooth handoffs.

## Scenario 1: Developer completes a feature and requests review
**Agent:** `junior_bot`
**Action:** Completed `git.commit` on branch `junior_bot/feat-auth`.
**Message:**
> @senior_bot [STATUS] I have finished developing the login logic on branch `junior_bot/feat-auth`.
> Local unit tests have passed. Please review and merge into main.

## Scenario 2: Senior developer / reviewer provides feedback
**Agent:** `senior_bot`
**Action:** Reviewed code and found a security issue.
**Message:**
> @junior_bot [BUG] I reviewed your branch. Found that the password hashing at `src/auth.py:42` uses a weak salt.
> Please fix it on the same branch and @ me again.

## 3. Scenario 3: Senior developer merges and cleans up branch
**Agent:** `senior_bot`
**Action:** Merges `junior_bot/feat-auth` into `main`, then deletes the feature branch.
**Message:**
> @junior_bot [STATUS] Review passed, code has been merged into `main`.
> @pm [PHASE] Login module development complete.

## Scenario 4: Project Manager (PM) syncs to GitHub
**Agent:** `pm_bot`
**Action:** Pushes local `main` to the remote repository.
**Message:**
> [VCS] Syncing local progress to GitHub.
> Repository: `qiuhuarui6/vcs-audit-demo`
> Action: Push executed and release PR created. Check details in the **VCS Audit** panel.

---

## Agent Technical Tips:
- Always use `git.status` to confirm your current branch before committing.
- Use `git.log` to verify your commit's Author is correctly identified.
- Don't panic when you encounter merge conflicts — follow the sequence: read the file → fix markers → `git.add` → `git.commit`.
