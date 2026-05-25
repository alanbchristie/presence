---
description: Create a release with a title
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(git:*), Bash(gh:*), Bash(npm:*), mcp__github__*
---

# Release workflow

Create release $ARGUMENTS

## Instructions

### 1. Check CI for success

The most recent CI action for thge `main` branch must have been successful.
If it is not a release must not be created.

```
Use the GitHub MCP tool to:
- Get the CI action
- Check for success
```

### 2. Create a release

Use the GitHub Releases page to create a new release:

- Use the provided summary as the summary test for the release
- The release number should use semantic versioning with a value based
  on the conventional commit comments for all the changes since the last
  release
- Create a set of resease notes that contain a summary of changes in every commit
  since the prior release

## Example workflow

```
Me: /release "Bug Fix"

Cluade:
1. Checking lastets main CI
   CI is successful
2. Creating release 1.1.0 for "Bug Fix"
   Release created
```
