import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { Graph } from "../workbench/graph";
import { nodeTitle } from "../workbench/text";
import "../styles/proof-ledger.css";

const LEMMA_NODE = "local-lemma:";

/**
 * #356: a claim's verified ledger. Checked lemmas stay available to later rounds, and tried routes
 * say which directions are already closed and why. The next proof round reads the same records.
 */
export function ProofLedger({ graph, onSelect }: { graph: Graph; onSelect: (id: string) => void }) {
  const { t } = useTranslation();
  const heading = useId();
  const ledger = graph.ledger;
  if (!ledger || (!ledger.counts.lemmas && !ledger.counts.tried_routes)) return null;
  const select = (id: string) => (graph.nodes.has(LEMMA_NODE + id) ? () => onSelect(LEMMA_NODE + id) : undefined);
  const routeNumbers = new Map(
    [...graph.nodes.values()]
      .filter((node) => node.kind === "reduction" && typeof node.reference?.lemma_id === "string")
      .map((node) => [String(node.reference!.lemma_id), nodeTitle(t, node)] as const),
  );
  const hidden = {
    lemmas: ledger.counts.lemmas - ledger.lemmas.length,
    routes: ledger.counts.tried_routes - ledger.tried_routes.length,
  };
  return (
    <section className="proof-ledger" aria-labelledby={heading}>
      <h3 id={heading}>{t("ledger.title")}</h3>
      {ledger.lemmas.length > 0 && (
        <>
          <h4>{t("ledger.lemmas", { count: ledger.counts.lemmas })}</h4>
          <ul className="proof-ledger-list">
            {ledger.lemmas.map((lemma) => (
              <li key={lemma.lemma_id}>
                <LedgerEntry onSelect={select(lemma.lemma_id)}>
                  <code>{lemma.proposition}</code>
                  <small>
                    {lemma.used_by.length
                      ? t("ledger.usedBy", {
                          routes: lemma.used_by
                            .map((id) => routeNumbers.get(id) ?? "")
                            .filter(Boolean)
                            .join(", "),
                          count: lemma.used_by.length,
                        })
                      : t("ledger.unused")}
                  </small>
                </LedgerEntry>
              </li>
            ))}
          </ul>
          {hidden.lemmas > 0 && <p className="proof-ledger-more">{t("ledger.more", { count: hidden.lemmas })}</p>}
        </>
      )}
      {ledger.tried_routes.length > 0 && (
        <>
          <h4>{t("ledger.routes", { count: ledger.counts.tried_routes })}</h4>
          <ul className="proof-ledger-list">
            {ledger.tried_routes.map((route) => (
              <li key={route.implication_id}>
                <LedgerEntry onSelect={select(route.implication_id)}>
                  <span className="proof-ledger-status">
                    {routeNumbers.get(route.implication_id) && <b>{routeNumbers.get(route.implication_id)}</b>}
                    {t(`ledger.status.${route.status}`, { defaultValue: route.status })}
                  </span>
                  {route.open_obligation && (
                    <small>
                      {t("ledger.stuckAt")} <code>{route.open_obligation}</code>
                    </small>
                  )}
                  {route.reason && <small className="proof-ledger-reason">{route.reason}</small>}
                </LedgerEntry>
              </li>
            ))}
          </ul>
          {hidden.routes > 0 && <p className="proof-ledger-more">{t("ledger.more", { count: hidden.routes })}</p>}
        </>
      )}
    </section>
  );
}

function LedgerEntry({ onSelect, children }: { onSelect?: () => void; children: React.ReactNode }) {
  return onSelect ? (
    <button type="button" className="proof-ledger-entry" onClick={onSelect}>
      {children}
    </button>
  ) : (
    <div className="proof-ledger-entry">{children}</div>
  );
}
