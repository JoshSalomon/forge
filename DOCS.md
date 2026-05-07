# Documentation Site

The Forge documentation site is built with [Zensical](https://zensical.org) — the next-gen successor to Material for MkDocs by the same team. Source lives in `docs/`, configuration in `zensical.toml`.

## Setup

```bash
uv sync --extra docs
```

That's it. No separate venv, no separate pip install.

## Local Preview

```bash
uv run zensical serve
```

Opens at `http://localhost:8000` with live reload on save.

## Build Static Site

```bash
uv run zensical build
```

Output goes to the `site/` directory (gitignored).

## Deploy to GitHub Pages

```bash
uv run ghp-import -n -p -f site
```

CI does this automatically on every push to `main` that touches `docs/`, `zensical.toml`, `CONTRIBUTING.md`, or `README.md`.

## Structure

```
docs/
├── index.md                  # Home page
├── getting-started.md        # Quick start
├── guide/                    # User-facing workflow guides
│   ├── feature-workflow.md
│   ├── bug-workflow.md
│   ├── labels.md
│   └── pr-commands.md
├── dev/                      # Developer documentation
│   ├── setup.md
│   ├── testing.md
│   └── contributing.md
├── skills/                   # Skills system documentation
│   ├── index.md
│   ├── authoring.md
│   └── defaults.md
├── reference/                # Reference documentation
│   ├── api.md
│   ├── config.md
│   └── proposals.md
├── developer-guide.md        # Existing comprehensive developer guide
└── images/                   # Logo and diagrams
```

## Adding a Page

1. Create `docs/section/page.md`
2. Add it to the `nav` array in `zensical.toml`
3. Run `uv run zensical serve` to preview

## Notes

- `site/` is gitignored — never commit it
- GLightbox is enabled natively via `zensical.extensions.glightbox`
- Mermaid diagrams render automatically in fenced code blocks tagged `mermaid`
- Dark/light mode uses Lucide icons (`lucide/sun`, `lucide/moon`)
