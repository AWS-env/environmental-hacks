# Vendored tree-sitter grammars (WASM)

Taken unmodified from npm `tree-sitter-wasms@0.1.13` (`out/tree-sitter-{python,typescript,tsx,javascript}.wasm`). They only load with `web-tree-sitter@0.20.8`; newer web-tree-sitter releases reject these binaries (dylink metadata error, verified), so that dependency is pinned exactly. Checksums in `SHA256SUMS`. Parsing never executes client code; the grammars only build a syntax tree.
