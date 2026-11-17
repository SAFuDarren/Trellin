# Lean proof and source boundaries

The installed `lean search`, `lean replay-formalpedia`, and `advance worker` commands coordinate these boundaries.

| Boundary | Input | Result |
| --- | --- | --- |
| Load the local proof scope | Confirmed Lean statement ID and the chosen search mode | Frozen target, current basis versions, and eligible proof references for local checking |
| Load a public source | Local snapshot, explicit environment and theorem or proof selector | Source bytes, declared proposition and proof type, attribution and artifact identities |
| Authorize a model request | Confirmed packet and explicit public or local model mode | PostgreSQL permits the scoped request or rejects it before a provider call |
| Read a model proposal | One candidate action and the exact reference IDs sent with the request | A structurally valid candidate for checking, or a rejected response |
| Verify and record | Frozen scope and candidate proof | Exact Lean result, actual dependencies, and a versioned lemma or evidence receipt |

A source loader reports what the artifact contains. The replay workflow compares
the source types and proposed local type in the destination Lean environment.
The shared verification interface checks the local derivation and records its
dependencies. Only the receipt path records a theorem with its downloaded
provenance. Save errors in the responsible layer so a parse or port failure stays
distinct from a missing condition of a verified route.

The workflow tries local derivation before model dispatch. A public claim can have a reviewed basis that is ineligible for public model use. A local proof can close that claim with zero model calls. Before public dispatch PostgreSQL checks the originating packets and basis of each reusable lemma, including the transitive source graph. The request contains permitted proof text and structured route metadata. Raw diagnostics and earlier model prose stay in the local trace. The workflow tests the exact stored request against the current disclosure view before any repeat send.

Verified working lemmas are available to later proof obligations without researcher approval. Each reference binds its checked version. When a dependency is withdrawn, downstream lemmas and receipts need rechecking. A complete proof or a proof of the exact frozen negation enters researcher review. A change to the target or approved premises requires a new reviewed scope.

The regression suite exercises local closure and denied model dispatch on different inputs. The latter uses a model adapter fixture and verifies that neither a provider dispatch nor a call reservation occurs until the exact restricted basis version is explicitly released. Installation tests should use the same Lean toolchain and PostgreSQL revision when comparing builds.

The workflow validates the search mode and budget for command-line and background callers. Only the explicit local search command can record a private model authorization within that validated operation. A worker uses its existing signed route. A recorded temporary database failure known to precede dispatch can resume under the original authorized budget. The attempt remains in the record, and any attempt with an uncertain dispatch outcome is handled without automatically sending it again.
