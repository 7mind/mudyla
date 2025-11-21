# Completion Integration Guide

## For Users Installing Mudyla

### Via Nix Profile
```bash
nix profile install github:yourusername/mudyla
```
Completions automatically work - Nix adds completion paths to `$FPATH` and bash completion paths.

### Via NixOS Configuration
```nix
environment.systemPackages = [ pkgs.mudyla ];
```
Completions automatically work system-wide.

## For Developers Using Mudyla in Their Flake

When using mudyla in another project's devShell, add it to `buildInputs`:

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    mudyla.url = "github:yourusername/mudyla";
  };

  outputs = { self, nixpkgs, mudyla }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      devShells.${system}.default = pkgs.mkShell {
        buildInputs = [
          mudyla.packages.${system}.default
        ];
      };
    };
}
```

### Zsh + Direnv

Completions work automatically because:
1. Nix adds `$package/share/zsh/site-functions` to `$FPATH`
2. Zsh automatically loads completions from `$FPATH`

After `direnv allow`, run `compinit` once to load completions:
```bash
compinit
```

Or add to `~/.zshrc` for automatic reload:
```bash
# Auto-reload completions when direnv changes FPATH
eval "$(direnv hook zsh)" && _direnv_hook() {
  eval "$(direnv export zsh)"
  [[ -n "$DIRENV_DIFF" ]] && compinit -i 2>/dev/null
}
```

### Bash + Nix Develop

Completions work automatically when you run:
```bash
nix develop
```

Nix's bash completion integration automatically sources files from `$package/share/bash-completion/completions/`.

## How It Works

Mudyla installs completions to standard Nix paths:
- **Bash:** `$out/share/bash-completion/completions/mdl`
- **Zsh:** `$out/share/zsh/site-functions/_mdl`

Nix automatically:
- Adds these paths to `$FPATH` (zsh) and bash completion paths
- Sources bash completions in interactive shells
- Makes zsh completions available via FPATH

No manual configuration needed!
