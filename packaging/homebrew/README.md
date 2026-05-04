# Homebrew packaging

This directory holds a stub `blemees-tui.rb` formula and instructions
for landing it in the [`blemees/homebrew-tap`](https://github.com/blemees/homebrew-tap)
repository (a separate repo).

## Release flow

1. **Publish to PyPI first.** The formula's `url` and `sha256` come from
   the PyPI source tarball. Run:

   ```sh
   python -m build
   twine upload dist/*
   ```

2. **Generate the resource block.** Homebrew formulae need every Python
   dependency pinned to its sdist tarball + sha256. The
   [`homebrew-pypi-poet`](https://github.com/tdsmith/homebrew-pypi-poet)
   tool does this automatically:

   ```sh
   pipx install homebrew-pypi-poet
   pipx install blemees-tui  # required so poet can introspect the env
   poet -f blemees-tui > resources.txt
   ```

3. **Update the formula** at `packaging/homebrew/blemees-tui.rb`:
   - Replace the placeholder `url` with the real PyPI source URL.
   - Replace `TODO_REPLACE_WITH_PYPI_TARBALL_SHA256` with the sdist's
     SHA256 (look for `sha256` in the PyPI download page).
   - Append the `resource "..."` blocks from `resources.txt`.

4. **Copy into the tap.** Move the updated `blemees-tui.rb` into the
   tap repo's `Formula/` directory. Open a PR there.

5. **Verify.**

   ```sh
   brew tap blemees/tap
   brew install blemees-tui
   blemees --help
   ```

## Why this lives here, not in the tap repo

Keeping the stub formula in the project repo means the source of truth
for "what dependencies blemees-tui ships with" stays next to
`pyproject.toml`. The tap repo's job is just distribution.
