{
  description = "Mudyla - Multimodal Dynamic Launcher";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;
        pythonPackages = python.pkgs;

        # Build package using Python build tools
        mudyla = pythonPackages.buildPythonApplication {
          pname = "mudyla";
          version = "0.1.0";
          src = ./.;
          format = "pyproject";

          nativeBuildInputs = with pythonPackages; [
            hatchling
          ];

          propagatedBuildInputs = with pythonPackages; [
            mistune
            pyparsing
            rich
          ];

          postInstall = ''
            cp $src/mudyla/runtime.sh $out/${python.sitePackages}/mudyla/
            mkdir -p $out/share/bash-completion/completions
            mkdir -p $out/share/zsh/site-functions
            mkdir -p $out/share/mudyla
            cp $src/completions/init.sh $out/share/mudyla/init.sh
            cp $src/completions/mdl.bash $out/share/bash-completion/completions/mdl
            cp $src/completions/_mdl $out/share/zsh/site-functions/_mdl
            cp $src/completions/init.zsh $out/share/mudyla/init.zsh
          '';

          meta = {
            description = "Multimodal Dynamic Launcher - Shell script orchestrator";
            homepage = "https://github.com/yourusername/mudyla";
            license = pkgs.lib.licenses.mit;
          };
        };

      in
      {
        packages = {
          default = mudyla;
          mudyla = mudyla;
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [
            pkgs.uv
            python
            pkgs.bash
            pkgs.coreutils
          ];

          shellHook = ''
            # Create/activate uv virtual environment
            if [ ! -d .venv ]; then
              echo "Creating uv virtual environment..." >&2
              uv venv
            fi

            # Activate virtual environment
            source .venv/bin/activate

            # Install package in development mode
            if [ ! -f .venv/.mudyla-installed ]; then
              echo "Installing mudyla with uv..." >&2
              uv pip install -e ".[dev]"
              touch .venv/.mudyla-installed
            fi

            # Enable completions in dev shell without system install
            # Use PWD so we get the actual working directory, not the nix store path
            if [ -n "''${BASH_VERSION}" ]; then
              if [ -f "''${PWD}/completions/mdl.bash" ]; then
                source "''${PWD}/completions/mdl.bash"
              fi
            elif [ -n "''${ZSH_VERSION}" ]; then
              # Ensure project completions are on fpath before compinit
              if [ -d "''${PWD}/completions" ]; then
                typeset -U fpath
                fpath=("''${PWD}/completions" ''${fpath})
                export FPATH="''${PWD}/completions''${FPATH:+:''${FPATH}}"
                autoload -Uz compinit
                compinit -i >/dev/null 2>&1 || true
                # Explicitly load the completion function and bind it
                autoload -Uz _mdl 2>/dev/null || true
                compdef _mdl mdl 2>/dev/null || true
              fi
            fi
          '';
        };

        apps.default = {
          type = "app";
          program = "${mudyla}/bin/mdl";
        };
      }
    );
}
