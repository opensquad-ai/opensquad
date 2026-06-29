---
name: publish-skill
description: Complete guide for publishing a local skill to the public OpenSquad skill library, including lessons learned on BOM handling, path issues, and other gotchas.
version: 1.0.0
author: OpenSquad
---

## Purpose
Publish a local skill directory to the OpenSquad public skill library.

## Prerequisites
- You have a complete local skill directory containing a `SKILL.md` file.
- The `SKILL.md` file must start with YAML frontmatter (delimited by `---`).

## Steps

### Step 1: Create the SKILL.md file
In the skill directory, create `SKILL.md` in this format:
```markdown
---
name: skill-name
description: Short description
version: 1.0.0
author: YourName
---

## Purpose
...
```

### Step 2: ⚠️ Check for a BOM header (the most critical step!)
The `filesystem.write_file` tool on Windows **automatically prepends a UTF-8 BOM** (`\xef\xbb\xbf`) to written files, but the YAML parser used by `publish_skill` does not tolerate the BOM.

❌ Error symptom: `SKILL.md must have YAML frontmatter delimited by ---`
✅ Even when the file's visible content starts with `---`, the actual file may begin with hidden BOM bytes.

**Check for BOM:**
```bash
python -c "data=open('SKILL.md','rb').read(); print('BOM:', data[:3]==b'\xef\xbb\xbf')"
```

**Strip the BOM (if present):**
```bash
python -c "data=open('SKILL.md','rb').read(); open('SKILL.md','wb').write(data[3:] if data[:3]==b'\xef\xbb\xbf' else data)"
```

### Step 3: ⚠️ Use an absolute path (mandatory!)
The `skill_dir` argument of `publish_skill` must be an **absolute path**.

❌ Wrong: `publish_skill(skill_dir="skills\\my-skill")` — the `filesystem` tool performs internal path mapping, so a relative path may be remapped under `workspace/`, where the on-disk directory doesn't actually exist.

✅ Correct: `publish_skill(skill_dir="C:\\path\\to\\project\\skills\\my-skill")`

### Step 4: Run the publish
```
agent_setup.publish_skill(skill_dir="absolute-path", overwrite=True)
```

### Step 5: Activate
Once publish succeeds, call `agent_setup.reload_plugins()` or restart the Agent for the change to take effect.

## Verification
- After a successful publish, the response includes `success: true` along with the source and target paths.
- Use `agent_setup.list_skills()` to confirm the skill appears in the list.

## Common pitfalls
| Symptom | Cause | Fix |
|---|---|---|
| `SKILL.md must have YAML frontmatter` | BOM at the start of the file | Use the Python snippet above to strip the BOM |
| `Directory not found` | A relative path was passed in | Switch to an absolute path |
| Publish succeeded but `list_skills` doesn't show it | The plugins cache wasn't reloaded | Call `reload_plugins()` |
