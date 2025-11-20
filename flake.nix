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

        python = pkgs.python311;

        # Build package using Python build tools
        mudyla = pkgs.python311Packages.buildPythonApplication {
          pname = "mudyla";
          version = "0.1.0";
          src = ./.;
          format = "pyproject";

          nativeBuildInputs = with pkgs.python311Packages; [
            hatchling
          ];

          propagatedBuildInputs = with pkgs.python311Packages; [
            mistune
            pyparsing
          ];

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
              echo "Creating uv virtual environment..."
              uv venv
            fi

            # Activate virtual environment
            source .venv/bin/activate

            # Install package in development mode
            if [ ! -f .venv/.mudyla-installed ]; then
              echo "Installing mudyla with uv..."
              uv pip install -e ".[dev]"
              touch .venv/.mudyla-installed
            fi

            echo "Mudyla development environment (with uv)"
            echo "Python version: $(python3 --version)"
            echo "UV version: $(uv --version)"
            echo ""
            echo "Commands:"
            echo "  uv pip install <package>  - Install a package"
            echo "  uv pip sync               - Sync dependencies"
            echo "  mdl --help                - Run mudyla CLI"
          '';
        };

        apps.default = {
          type = "app";
          program = "${mudyla}/bin/mdl";
        };
      }
    );
}
