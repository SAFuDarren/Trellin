# Deterministic production domains

The production workflow uses the same PostgreSQL queue, frozen statement packet,
verification and researcher review for every registered checker. The domain
adapter supplies the exact meaning of an instance and the independent certificate
check. The available adapters are fixed directed maximum flow, finite graph
coloring, exact rational linear programming, and bounded rational semidefinite programming.

## Graph coloring

For example, `graph.json` can ask whether a triangle is colorable with two colors.

```json
{
  "vertices": ["a", "b", "c"],
  "edges": [["a", "b"], ["b", "c"], ["a", "c"]],
  "colors": 2
}
```

Use the packet ID, version and hash from the draft. Review the graph, the rules
for a proper coloring and the stated color bound before confirming the packet.

```sh
research-multiplier formalization draft graph.json --domain graph-coloring --title "Triangle with two colors"
research-multiplier formalization show PACKET_ID
research-multiplier formalization confirm PACKET_ID --version VERSION --hash PACKET_HASH \
  --actor "your name" --reason "Checked the graph and the color bound"
research-multiplier formalization prove PACKET_ID
```

A valid assignment gives an exact certificate. A self-loop or a complete finite
search can refute the claim. The verifier checks assignments against the frozen
graph and independently enumerates assignments when a refutation claims
exhaustion. The bounded exhaustive path supports at most 80 vertices and one
million color assignments. The reported run directory contains the certificate,
verification report and record to replay.

Approve an exact result and apply its receipt with the same
[human review commands](v0a.md#review-exact-evidence). A verified
refutation can move the original frozen claim to `refuted` after approval.
The deterministic maximum-flow route can also refute an incorrect target by
showing a feasible flow and a matching cut at a different exact value.

## Register a checker

A production adapter provides contract construction and validation, a structured
review packet, candidate construction and certificate verification. An installed
package can expose a factory in the `research_multiplier.domains` entry-point
group. Use the domain name as the entry-point name and return an instance of
`research_multiplier.production_domains.Domain` with its trusted checker sources.
Enable the package explicitly with `RM_DOMAIN_PACKAGES=your-domain` and allow
its checker name and version through an administrator-reviewed PostgreSQL migration.
The verification
step binds the checker and its code revision to the exact candidate attempt
and the frozen claim. The existing model-assisted proof and Lean routes
continue to support the public maximum-flow scope.

## Rational optimization

Linear programming uses `{"A":[[1]],"b":[2],"c":[1],"target_value":2}` for
maximizing `c·x` with `Ax ≤ b` and `x ≥ 0`.
Save a separate candidate `{"primal":[2],"dual":[1]}` and use the usual
draft, confirmation and review path with `--domain linear-program`.

```sh
research-multiplier formalization prove PACKET_ID --certificate witness.json
```

The semidefinite adapter maximizes `⟨C,X⟩` over real symmetric PSD matrices
with equalities `⟨Aᵢ,X⟩ = bᵢ`. Its instance uses capital `C` and a list of
symmetric matrices under `A`. The certificate supplies the primal matrix
under `primal` and unrestricted dual equality multipliers under `dual`.
Exact symmetric elimination checks PSD without floating-point tolerances.
These adapters verify supplied witnesses using exact arithmetic. They
accept integers, fractions and short decimal strings and can refute a frozen
target when independently feasible primal and dual witnesses agree at another value.
