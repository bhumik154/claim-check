Real output captured on GitHub Actions (ubuntu-24.04) on 2026-11-13:
go 1.23.12, node 22.23.4, npm 10.9.9, GNU Make 4.3, typescript 5.6.3,
eslint 9.13.0, pnpm 9.12.0. Each failing case is a deliberate error in a
throwaway project. The runner's temp path is shortened to /work/.

Notable facts these files establish:
- npm prints no failure line of its own; only its "> pkg@version script"
  header and the wrapped tool's output.
- eslint prints a "✖ N problems" line on success too; only a nonzero error
  count is a failure.
- go vet prints only file:line messages, so only its exit code tells.
