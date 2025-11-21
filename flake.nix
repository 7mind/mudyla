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

            # Only show welcome message in interactive shells
            if [ -t 0 ]; then
              echo "Mudyla development environment (with uv)"
              echo "Python version: $(python3 --version)"
              echo "UV version: $(uv --version)"
              echo ""
              echo "Commands:"
              echo "  uv pip install <package>  - Install a package"
              echo "  uv pip sync               - Sync dependencies"
              echo "  mdl --help                - Run mudyla CLI"
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
