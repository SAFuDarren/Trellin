# Find and link literature evidence

## Search Formalpedia locally

Download an Apache-2.0 [Formalpedia snapshot](https://github.com/prove2me/formalpedia)
to a directory you choose outside the software. Keep its manifest and environment
subdirectories in their original layout. You can retain just the index shards
for discovery and add the selected statement and proof files for replay.

```sh
research-multiplier literature formalpedia "your local query" \
  --snapshot /path/to/public/snapshot --environment ENV --proved-only --limit 20
```

The query never leaves your computer. The results give theorem IDs and source
paths. They are public suggestions and do not modify the research basis.

Replay on this version requires PostgreSQL migration `0068` in the selected project. Back up that project and use the migration owner to enable it. Offline snapshot search works without a database.

```sh
python -c 'import os; from research_multiplier.storage.migrate import upgrade; upgrade(os.environ["RM_MIGRATION_DSN"], "0068")'
```

Use the following with an existing
frozen and confirmed Lean statement and a local version 1 plan. Each plan node
has a distinct `name` and an exact `theorem` catalog name or ID. You can select a
specific `solution_id` and provide a local structured `proof` when needed. A
`local_proposition` may express supported upstream notation using the project's
approved functions. Lean must confirm definitional equality with the full
exported proposition in that same environment before any replay writes a
lemma. Nodes
refer to earlier nodes with `{"node_ref":"EarlierNode"}`. The plan can include a
final `goal_proof`, or the program can attempt the frozen goal with `--finish`.

```sh
research-multiplier lean replay-formalpedia STATEMENT_ID /path/to/replay.json \
  --snapshot /path/to/public/snapshot --environment ENV --finish
research-multiplier lean formalpedia-sources STATEMENT_ID --include-goals
research-multiplier research resume --format text
```

The replay checks the source file identities and compares the exported
theorem type, exported proof's declared type, and local proposition in the
destination's pinned Lean environment. It checks the local proof against
the confirmed basis and records the source with the verified receipt.
Exported source that relies on custom definitions or unsupported constructs
remains searchable. Source proof syntax does not run
automatically. The recorded provenance distinguishes local re-derivation from
the upstream proof artifact. Verified working lemmas enter the workspace
automatically. A verified conditional lemma can leave a specific undischarged
premise for the selected application. `research resume` shows it as a condition
of that route. Port, parse, and runtime errors remain diagnostics. Full-goal
evidence follows the normal human review path.

The source command can still inspect older `0067` records. It reports these as
`legacy_unbound`. Replay the source in a `0068` project to obtain a newly bound
record. Formalpedia snapshot versions and the project toolchain are independent.

## Search the research workspace

Use local search across the workspace database first. It finds exact identifiers and literal text in registered sources, claims, admitted basis items and recorded failed attempts.

```sh
research-multiplier literature find "flow-cut"
research-multiplier literature find doi:10.1234/example
```

To locate another frozen claim with identical recorded wording and mathematical scope, run `research-multiplier literature duplicates CLAIM_ID`. The lookup preserves the statement, hypotheses, exact basis and definitions, parameters, formal target, and fixed mathematical inputs. It ignores descriptive metadata such as titles and the IDs of research frames. Matches are candidates for reuse review. Claims with different wording or equivalent formalizations require separate comparison.

For a frozen public claim, you can explicitly send a bibliographic query to the [Crossref works API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/). Only the query and retrieval parameters are sent. Use publication-date bounds when the scope calls for a particular period. The endpoint call is bounded by a response-size limit, a time limit and a maximum number of items. Search results are scoped to that one query and the returned page.

```sh
research-multiplier literature search CLAIM_ID "fixed network flow cut" --since 2025-01-01 --limit 12
research-multiplier literature history CLAIM_ID
research-multiplier literature show SEARCH_ID
```

The search stores the frozen claim version, provider, parameters, recorded time, normalized hits, and the raw response's content hash. A malformed response or provider error is recorded separately from a successful query with no hits.

For searches you carry out elsewhere, save a local search log and import it. This also works with restricted claims and makes no network request. The application archives the log and validates its format and exact identifiers. The log records where and how you searched under your own review.

```json
{
  "searched_in": "Institutional catalogue and arXiv",
  "query": "network flow cut finite directed graph",
  "identifiers": ["doi:10.1234/example", "arXiv:2401.01234v1"]
}
```

```sh
research-multiplier literature import-search CLAIM_ID inputs/search-log.json
```

A retrieved identifier is metadata. To rely on a theorem, first [archive and review the original](research-review.md#archive-a-specific-original-source). For a Crossref DOI, import the local PDF and confirm the theorem location, wording and mathematical conventions. Then link the original to the exact result rank.

```sh
research-multiplier literature link-source SEARCH_ID RANK SOURCE_ID
research-multiplier literature show SEARCH_ID
```

The link requires the same claim and exact source identifier and displays the original's review status. Use the scoped search IDs when writing the human novelty review. A search with no hits leaves the existing novelty state unchanged. Local retrieval is literal and this workflow uses one external metadata provider.

## Search exact versions with externally generated vectors

Export the text for each item you want to include. The command returns the exact `input` field to encode and a `content_sha256` identifying that version and text. Supported document kinds are `source`, `claim`, `basis`, `evidence`, and `failure`. Source originals must be confirmed and evidence must be current, exact and approved. Keep unpublished text with your own approved embedding workflow.

```sh
research-multiplier literature document source SOURCE_ID
```

Supply vectors produced by your own embedding tool. Use a stable model name and an immutable revision for its weights and encoding setup. The example coordinates below illustrate the file format. Encode both the exported input and your query with the same model and revision.

```json
{
  "model": "your-embedding-model",
  "revision": "weights-and-encoding-v1",
  "dimensions": 2,
  "documents": [
    {
      "kind": "source",
      "id": "SOURCE_ID",
      "content_sha256": "EXPORTED_SHA256",
      "vector": [1, 0]
    }
  ]
}
```

```sh
research-multiplier literature import-vectors inputs/vectors.json
research-multiplier literature vector-profiles
```

For a query, save the same model, revision, and dimensions with a single `vector` field in place of `documents`.

```sh
research-multiplier literature similar inputs/query-vector.json --kind source --limit 20
```

The index records the original vector digest alongside the exact document digest. Reimporting the same version is safe. For a change in the encoding use a new model revision. Retrieval filters by model, revision, dimension, and current eligibility before exact cosine ranking in PostgreSQL. Retired records remain in the audit history and disappear from searches. Cosine scores reflect the supplied embedding model and provide candidates for review. [pgvector search and distance definitions](https://github.com/pgvector/pgvector#querying)
