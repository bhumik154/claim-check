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

gotestsum-*.txt and make-gotestsum-*.txt: gotestsum v1.12.0 with
`--rerun-fails`, go 1.23.12, captured the same way on 2026-11-14. A flaky
test fails on the first run and passes on the rerun; in the -fail files a
second test always fails.
- The default format prints no `--- FAIL` or `FAIL\t` lines at all, only
  its own "DONE 2 runs, 3 tests, 1 failure" tally, even when the rerun
  passed and gotestsum exited 0.
- standard-verbose prints go's own `FAIL\t<package>` for the first run and
  `ok  \t<package>` for the rerun. The later `ok` is what says it passed.
