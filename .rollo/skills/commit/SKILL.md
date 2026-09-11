---
name: commit
description: Create a git commit with a summary of changes
user_invocable: true
context: inline
---

# Commit Skill

1. Run `git diff --staged` and `git status` to see what's changed
2. Write a concise commit message summarizing the changes
3. Run `git commit -m "<message>"`

If no files are staged, tell the user to stage files first.
Arguments passed to this skill are used as additional context for the commit message.
