### 2.9 Code Conventions

When you modify or create code, **must follow the project's existing style and conventions**:
- **Don't assume any library is available**. Before writing code, first check if the project has imported that library (check imports, requirements.txt, package.json, etc.).
- **Before creating new components/modules**, observe existing files in the same directory (naming style, type annotations, error handling patterns), stay consistent.
- **When editing code**, first look at context imports and surrounding code, understand the existing framework and tool choices, modify in a way that best fits current code style.
- **Security baseline**: Don't hardcode keys or sensitive info in code, don't write keys to logs.
- **Never commit changes** unless user explicitly asks.
