{
  description = "PDF watermarking tool — watermark baked into pixels, non-removable";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        python = pkgs.python3.withPackages (ps: with ps; [
          pillow
          pdf2image
          img2pdf
          colorama  # optional — colored output
          tqdm      # optional — progress bars
        ]);

        # poppler is prepended to PATH so the built package works on its own
        # (nix run / nix profile install), not only inside the devShell.
        filigranez = pkgs.writeShellScriptBin "filigranez" ''
          export PATH="${pkgs.poppler_utils}/bin:$PATH"
          exec ${python}/bin/python3 ${./filigranez.py} "$@"
        '';

      in {
        packages = {
          default  = filigranez;
          filigranez = filigranez;
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [
            filigranez
            python
            pkgs.poppler_utils
          ];

          shellHook = ''
            echo "filigranez — PDF watermarking"
            echo ""
            echo "Usage: filigranez <input.pdf> <text> [output.pdf] [options]"
            echo "       filigranez --help"
            echo ""
          '';
        };
      });
}
