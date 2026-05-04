# Publishing

This repository is generated from the local Cortex runtime.

Local publication root:

```text
<CORTEX_REPO>\.cortex-publishing
```

Publishing module:

```text
<CORTEX_REPO>\scripts\brain\cortex_publishing.py
```

## Commands

Preview generated documentation:

```bash
python scripts/brain/cortex_publishing.py preview
```

Initialize the public repository:

```bash
python scripts/brain/cortex_publishing.py init --confirm
```

Regenerate and push:

```bash
python scripts/brain/cortex_publishing.py update
```

## GitHub Authentication

The system uses GitHub CLI (`gh`) instead of the app plugin. The plugin can fail
or be partially configured while `gh auth status` still works. The current
publication path is therefore:

```text
local files -> git commit -> gh/git push -> GitHub repo
```

## GitHub Pages

The repository is intended to serve documentation from the `docs/` directory.
If Pages is not enabled automatically, enable it manually in GitHub:

```text
Settings -> Pages -> Deploy from branch -> main /docs
```

