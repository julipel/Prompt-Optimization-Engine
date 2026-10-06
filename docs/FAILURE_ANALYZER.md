# Failure analyzer: controlled offline loop

`domain.failures` defines immutable `FailureCase`, `FailureCluster`, `FailureDraft`
and `HumanReview`. Nested finite JSON is copied/frozen. Times must be aware;
provenance identities use opaque ASCII tokens, never paths or user identifiers.
FailureCase retains actual prompt name/version and observed failures, not asserted
root causes. Arbitrary traces/metadata are not stored. A draft proposes input,
context and tags, records cluster/source references and review requirements;
it has no expected answer by default. Failed model output is never copied to expected.

`ports.failures` contains `FailureSanitizer`, `FailureAnalyzer`, `FailureStore`,
`ReviewedDatasetWriter`. All dependencies are injected; imports perform no I/O.
`application.failures` provides three separate APIs:

- `ImportFailures(sanitizer, analyzer, store).execute(traces)` sanitizes every trace,
  validates sanitizer/analyzer results and references, then saves failures/drafts.
- `ReviewFailures(store).execute(draft_id, revision=..., reviewer_id=...,
  reviewed_at=..., decision='approve'|'reject', expected=...,
  sanitization_confirmed=True, expected_confirmed=True)` is a trusted local human
  operation. The reviewer supplies a nonempty correct reference, verifies context
  and confirms privacy. Reviewer ID is an audit label, not authentication evidence.
  Host must restrict this callable to a human review boundary.
- `ExportReviewedFailures(store, writer).execute(draft_ids, dataset_id=...,
  dataset_version=..., destination=..., existing=None)` explicitly creates a release.
  Only approved reviews matching the complete current draft are eligible. Existing
  cases may be merged explicitly into a new version of the same dataset. Duplicate
  case IDs reject the entire operation before writing.

Review stores the exact immutable draft snapshot and final EvaluationCase, including
the human supplied expected. Approval does not mutate a draft into a trusted case.
Repeated/conflicting reviews are rejected. `ReviewFailures.revise(...)` increments
the revision, changes proposed content and removes current approval/rejection.
Old review snapshots remain in history; a fresh review is mandatory.

## Sanitization policy

Raw/untrusted objects enter only the injected sanitizer. Only validated FailureCase
objects leave that boundary for analysis/storage. No raw trace is logged or copied
to public errors. Production hosts must inject a sanitizer implementing their own
privacy policy, rejecting uncertain inputs or obtaining separate sanitization
confirmation before returning a FailureCase. There is no production passthrough.
The injected sanitizer is trusted to establish privacy: shape validation alone
cannot certify arbitrary natural-language text.

The bundled `SyntheticMarkerSanitizer(synthetic_confirmed=True)` is exclusively for
synthetic fixtures. It also requires `synthetic: true` on every trace. Its strict
Pydantic `SyntheticTrace` schema requires id, prompt_name, prompt_version, ISO aware
timestamp string, input, context/output objects, nonempty observations, metadata
object and synthetic boolean. Unknown fields/types/nonfinite values fail closed.
Credential-bearing fields (including nested token/password/authorization/api_key)
are removed; artificial `[[PRIVATE:...]]` text markers are replaced recursively,
including keys, observations and nested arrays. Unrecognized metadata remaining
after credential removal is rejected. No arbitrary metadata is silently discarded.
These artificial regex markers do **not** establish PII/PHI removal. A synthetic
attestation must never be used for production traces. No real PII/PHI is in examples.

## Determinism and persistence

`DeterministicFailureAnalyzer` groups by sorted unique observed labels, sorts groups
lexicographically and failure IDs within each group. Cluster ID is `cluster_` plus
24 hex SHA256 characters of JSON encoding the sorted labels (Unicode preserved).
Tags/basis describe heuristic observations, never proven causes. Every failure
has one draft and one cluster. Trace/analyzer text is data, not executable commands.

`InMemoryFailureStore` is isolated per instance, single-process and without concurrent
writers or durable storage. Batch import checks duplicates before any mutation.
Review history preserves audit data separately from JSONL. Restart loses all state;
hosts requiring durability must implement the store port with equivalent atomic
batch and review snapshot semantics. This session adds no database or background job.

`ExclusiveJsonlDatasetWriter(root)` accepts a simple new `*.jsonl` basename inside
a host-provisioned dedicated release directory. Host must keep that root separate
from raw traces, draft storage and registry. Paths/traversal are rejected, existing
files/symlinks are never replaced. Complete JSONL is staged/fsynced in the same
directory; an atomic hard-link creates the destination exclusively. Filesystems
without hard links fail closed. Directory fsync/crash durability is not guaranteed.
Cleanup failure after link creation reports failure although the complete release
may exist: inspect/load that destination and remove orphan staging files before
any manual retry. There are no automatic retries. Export writes only one artifact;
review provenance remains in the store and is not added to the existing JSONL schema.

## Errors and capabilities

`FailureError` has safe stable `code` and message; original exceptions are retained
in `__cause__` for trusted local diagnostics, never public serialization. Codes:
`import_validation`, `sanitization_failure`, `invalid_sanitizer_output`,
`invalid_analyzer_output`, `duplicate_identity`, `unknown_references`,
`invalid_review_transition`, `unreviewed_draft`, `invalid_expected`, `storage_failure`,
`export_failure`. Domain constructors raise safe `DomainValidationError`.
Technical failures do not return successful partial results. Custom stores must
document partial commits/recovery; the bundled memory store has no multi-file commit.

Analyzer/importer cannot review, export, optimize, approve prompts or promote/deploy.
No new HTTP endpoints or agent tools are registered. Existing optimize_prompt still
only optimizes and retrieves reports. Dataset export never invokes optimization or
changes dataset resolver mappings; the host configures splits explicitly.

## Reproduce the complete scenario

```powershell
.\.venv\Scripts\python.exe examples/failures_offline.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

The example source specifies every synthetic trace/marker, sanitizer policy,
observation grouping and fake output. It rejects export before review, supplies
human ground truth explicitly, exports new train/validation files and loads them
with existing JsonlDatasetLoader. The host explicitly maps both split paths in
FileDatasetResolver (same reviewed case in both splits, no hidden random split).
Only then does a separate optimize_prompt call run. build_offline_app composes
FakeOptimizer and FakeLLMClient with literal responses keyed by prompt version/case
ID, never derived from expected. The complete report 1.0 is retrieved; candidate
remains candidate and production stays v001. Temporary files expire on exit.
No network or paid API is used; real-provider gepa_smoke is not enabled.
