### 2.10 OpenSquad Framework Modification Prohibition

**OpenSquad framework core files are prohibited from direct modification.** Protected scope includes but not limited to:

- All files inside `opensquad/` package
- `agents/boot.py`
- `launcher.py`
- `system_config.py`
- All files under `gateway/backend/app/` directory

If you determine there's a framework-level bug, **correct approach is**:
1. Clearly explain to user the problem (file, line number, reason)
2. Wait for user's explicit authorization before modification
3. Prioritize implementing workaround at application layer (agent code, plugins, config) rather than directly patching framework

**Modifications violating this rule, even if seemingly correct, may introduce hard-to-track side effects and cannot be rolled back.**
