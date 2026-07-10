---
name: baoyu-format-markdown
description: Formats plain text or markdown files with frontmatter, titles, summaries, headings, bold, lists, and code blocks. Outputs to {filename}-formatted.md.
---

# Markdown Formatter

Transforms plain text or markdown files into well-structured markdown with proper frontmatter, formatting, and typography.

## Script Directory

Scripts in `scripts/` subdirectory. Replace `${SKILL_DIR}` with this SKILL.md's directory path.

| Script | Purpose |
|--------|---------|
| `scripts/main.ts` | Main entry point with CLI options (uses remark-cjk-friendly for CJK emphasis) |
| `scripts/quotes.ts` | Replace ASCII quotes with fullwidth quotes |
| `scripts/autocorrect.ts` | Add CJK/English spacing via autocorrect |

## Workflow

### Step 1: Read Source File
Read the user-specified markdown or plain text file using `read_local_file`.

### Step 2: Analyze & Format Content
If input is plain text (no `---` frontmatter, no `#` headings, no `**bold**`, no `- ` lists, no code blocks):
- Add YAML frontmatter with `title`, `slug`, `summary` (100-150 chars)
- Extract/promote first H1 line to frontmatter `title`, remove H1 from body
- Apply formatting rules:
  - Headings: `#`, `##`, `###` hierarchy
  - Key points: `**bold**`
  - Parallel items: `-` unordered or `1.` ordered lists
  - Code/commands: `` `inline` `` or code blocks
  - Quotes/sayings: `>` blockquote
  - Separators: `---` where appropriate

If input is already markdown — preserve existing structure, add/improve frontmatter only.

### Step 3: Save Formatted File
Use `write_local_file` to save formatted markdown as `{original-filename}-formatted.md`. Overwrite if exists.

### Step 4: Execute Typography Script
Use `execute_bash_command` to run the formatting script:

```bash
npx -y bun /app/data/skills/baoyu-format-markdown/scripts/main.ts {output-file-path} --quotes
```

Script options:
- `--quotes`: Replace ASCII quotes with fullwidth quotes
- `--spacing` (default): Add CJK/English spacing via autocorrect
- `--emphasis` (default): Fix CJK emphasis punctuation issues

### Step 5: Report Results
Output summary:
```
**Formatting complete**
File: {output-path}
Changes: frontmatter, headings, bold markers, lists, code blocks, typography fixes
```

## Notes
- Preserve original writing style and tone
- Specify correct language for code blocks
- Maintain CJK/English spacing standards
- Do not add content not present in original