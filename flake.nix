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

        # Read version from pyproject.toml (single source of truth)
        pyproject = builtins.fromTOML (builtins.readFile ./pyproject.toml);
        version = pyproject.project.version;

        # Build package using Python build tools
        mudyla = pythonPackages.buildPythonApplication {
          pname = "mudyla";
          inherit version;
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
            cp $src/completions/init.sh $out/bin/mudyla-autocomplete
            chmod +x $out/bin/mudyla-autocomplete

            cp $src/mudyla/runtime.sh $out/${python.sitePackages}/mudyla/

            mkdir -p $out/share/bash-completion/completions
            mkdir -p $out/share/zsh/site-functions
            mkdir -p $out/share/mudyla

            cp $src/completions/mdl.bash $out/share/bash-completion/completions/mdl
            cp $src/completions/_mdl $out/share/zsh/site-functions/_mdl
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
            if [ ! -d .venv ]; then
              echo "Creating uv virtual environment..." >&2
              uv venv
            fi

            source .venv/bin/activate

            # Check if dependencies are up to date by comparing pyproject.toml timestamp
            PYPROJECT_HASH=$(sha256sum pyproject.toml | cut -d' ' -f1)
            INSTALLED_HASH=""
            if [ -f .venv/.mudyla-hash ]; then
              INSTALLED_HASH=$(cat .venv/.mudyla-hash)
            fi

            if [ "$PYPROJECT_HASH" != "$INSTALLED_HASH" ]; then
              echo "Dependencies changed, reinstalling mudyla with uv..." >&2
              uv pip install -e ".[dev]"
              echo "$PYPROJECT_HASH" > .venv/.mudyla-hash
            fi

            #export FPATH="''${PWD}/completions"

            # Enable completions in dev shell without system install
            # Use PWD so we get the actual working directory, not the nix store path
            if [ -n "''${BASH_VERSION}" ]; then
              source "''${PWD}/completions/mdl.bash"
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
