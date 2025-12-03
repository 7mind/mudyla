# Nix Integration

Mudyla integrates deeply with Nix to provide reproducible execution environments.

## How it Works

By default, Mudyla executes Bash actions using `nix develop`.

1.  **Flake Detection**: It looks for `flake.nix` in the project root.
2.  **Command construction**:
    ```bash
    nix develop --ignore-environment \
      --keep PASSTHROUGH_VAR \
      --command bash rendered_script.sh
    ```
3.  **Isolation**: `--ignore-environment` ensures the action runs in a clean environment, inheriting only what is explicitly allowed via `passthrough` or `vars`.

## Running Without Nix

You can disable Nix integration using `--without-nix`. This is useful for:
*   Windows (default).
*   Docker containers.
*   Quick local testing.

When disabled, Mudyla uses the system `bash` (or Git Bash on Windows) and inherits the full parent environment.
