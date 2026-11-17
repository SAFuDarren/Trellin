"""Search public bibliographic metadata and retrieve current workspace records."""

from concurrent.futures import Future
from datetime import date
import json
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener

from research_multiplier.storage import paths
from ..storage.artifacts import Artifacts
from ..models.model_http import NoRedirect
from .research_review import normalize_identifier


CROSSREF = "https://api.crossref.org/works"
MAX_RESPONSE_BYTES = 1_048_576
DOWNLOAD_SECONDS = 20


def _download(url):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Research-Multiplier/1.0"})
    completed = Future()

    def perform():
        try:
            with build_opener(NoRedirect()).open(request, timeout=DOWNLOAD_SECONDS) as response:
                completed.set_result(response.read(MAX_RESPONSE_BYTES + 1))
        except Exception as error:
            completed.set_exception(error)

    Thread(target=perform, name="rm-literature", daemon=True).start()
    try:
        raw = completed.result(timeout=DOWNLOAD_SECONDS)
    except HTTPError as error:
        with error:
            raise ValueError(f"Crossref returned HTTP {error.code}.") from None
    except (TimeoutError, URLError, OSError):
        raise ValueError("Crossref could not complete the metadata request.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Crossref response exceeded the 1 MiB limit.")
    return raw


def _published(item):
    published = item.get("published")
    if not isinstance(published, dict):
        return []
    dates = published.get("date-parts")
    parts = dates[0] if isinstance(dates, list) and dates else None
    if not isinstance(parts, list) or not 1 <= len(parts) <= 3 or any(type(value) is not int for value in parts):
        return []
    try:
        date(*(parts + [1] * (3 - len(parts))))
    except (ValueError, OverflowError):
        return []
    return parts


def _crossref_hits(raw, limit):
    try:
        reply = json.loads(raw)
        message = reply["message"]
        items = message["items"]
        total = message["total-results"]
        if reply["status"] != "ok" or not isinstance(items, list) or type(total) is not int or total < 0:
            raise ValueError
    except (KeyError, ValueError, TypeError, RecursionError):
        raise ValueError("Crossref returned an invalid metadata response.") from None
    hits = {}
    for item in items[:limit]:
        if not isinstance(item, dict) or not isinstance(item.get("DOI"), str) or len(item["DOI"]) > 1024:
            continue
        try:
            identifier = normalize_identifier("doi:" + item["DOI"])
        except ValueError:
            continue
        titles = item.get("title")
        title = titles[0][:1000] if isinstance(titles, list) and titles and isinstance(titles[0], str) else ""
        hits.setdefault(identifier, {"identifier": identifier, "title": title, "published": _published(item),
                                     "landing_page": "https://doi.org/" + quote(identifier[4:], safe="/"),
                                     "status": "metadata_hit"})
    if items and not hits:
        raise ValueError("Crossref returned no recognizable identifiers in the retrieved items.")
    return total, list(hits.values())


def _query(text, limit, since, until):
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        raise ValueError("Provide a public bibliographic query of at most 300 characters.")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Search limit must be between 1 and 50.")
    if since and until and since > until:
        raise ValueError("The publication date range is reversed.")
    parameters = {"query.bibliographic": text, "rows": limit, "select": "DOI,title,published"}
    filters = [f"{key}:{boundary.isoformat()}" for key, boundary in
               (("from-pub-date", since), ("until-pub-date", until)) if boundary]
    if filters:
        parameters["filter"] = ",".join(filters)
    return parameters


def search(store, claim, text, *, limit=12, since=None, until=None, download=None):
    """Make one explicit public query. A failure is recorded as such, not as a zero-hit success."""
    params = _query(text, limit, since, until)
    rows = store.query("human", """
        SELECT contract_sha256 FROM rm.claims
        WHERE id = %s AND sensitivity = 'public' AND frozen_at IS NOT NULL
    """, claim)
    if not rows:
        raise ValueError("Online metadata search requires a frozen public claim.")
    record = {"provider": "crossref", "query": text, "parameters": params}
    hits = []
    try:
        raw = (download or _download)(f"{CROSSREF}?{urlencode(params)}")
    except ValueError as error:
        record.update(status="provider_error", error=str(error)[:500])
    else:
        objects = Artifacts(paths.object_store(), workspace=False)
        record["response"] = objects.put_bytes(raw)
        try:
            total, hits = _crossref_hits(raw, limit)
        except ValueError as error:
            record.update(status="provider_error", error=str(error))
        else:
            record.update(status="complete", total_results=total)
    search_id = store.query("human", "SELECT rm.record_literature_search(%s,%s,%s,%s) AS id",
                            claim, rows[0]["contract_sha256"], store.json(record), store.json(hits))[0]["id"]
    return show(store, search_id)


def _imported_search(raw):
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Local search records must be no larger than 1 MiB.")
    body = json.loads(raw)
    if not isinstance(body, dict) or set(body) != {"query", "searched_in", "identifiers"}:
        raise ValueError("A local search record requires query, searched_in and identifiers.")
    for key, bound in (("query", 4000), ("searched_in", 1000)):
        if not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > bound:
            raise ValueError(f"A local search record needs a bounded {key} field.")
    identifiers = body["identifiers"]
    if not isinstance(identifiers, list) or len(identifiers) > 50 or any(
        not isinstance(item, str) or len(item) > 1000 for item in identifiers
    ):
        raise ValueError("Provide a list of up to 50 versioned arXiv IDs or DOIs.")
    normalized = [normalize_identifier(identifier) for identifier in identifiers]
    if len(normalized) != len(set(normalized)):
        raise ValueError("A local search record requires unique normalized identifiers.")
    return {"query": body["query"], "searched_in": body["searched_in"]}, normalized


def import_search(store, claim, file):
    """Record a first-hand search log from disk without contacting a provider."""
    with file.open("rb") as handle:
        raw = handle.read(MAX_RESPONSE_BYTES + 1)
    scope, identifiers = _imported_search(raw)
    rows = store.query("human", "SELECT contract_sha256 FROM rm.claims WHERE id = %s AND frozen_at IS NOT NULL", claim)
    if not rows:
        raise ValueError("Import a local literature search against a frozen claim.")
    scope["response"] = Artifacts(paths.object_store(), workspace=False).put_bytes(raw)
    saved = store.query("human", "SELECT rm.import_literature_search(%s,%s,%s,%s) AS id",
                        claim, rows[0]["contract_sha256"], store.json(scope), identifiers)[0]["id"]
    return show(store, saved)


def verify_search_archive(store, search_id):
    search_result = show(store, search_id)
    body = search_result["search"]["body"]
    if body["status"] != "complete":
        raise ValueError("Only a completed literature search can support a novelty review.")
    raw = Artifacts(paths.object_store(), workspace=False).read(body["response"])
    if body["provider"] == "manual":
        scope, identifiers = _imported_search(raw)
        if any(body[key] != value for key, value in scope.items()):
            raise ValueError("The archived search scope differs from its recorded scope.")
    else:
        total, hits = _crossref_hits(raw, body["parameters"]["rows"])
        if total != body["total_results"]:
            raise ValueError("The archived search total differs from its recorded total.")
        identifiers = [hit["identifier"] for hit in hits]
    if identifiers != [hit["body"]["identifier"] for hit in search_result["hits"]]:
        raise ValueError("The archived search hits differ from their recorded identifiers.")


def show(store, search_id):
    rows = store.query("human", "SELECT * FROM rm.literature_searches WHERE id = %s", search_id)
    if not rows:
        raise ValueError("No literature search matches this ID.")
    hits = store.query("human", """
        SELECT hit.rank, hit.body, coalesce(matched.sources, '[]'::jsonb) AS originals
        FROM rm.literature_search_hits hit
        LEFT JOIN LATERAL (
            SELECT jsonb_agg(jsonb_build_object('id', source.id, 'source_sha256', source.source_sha256,
                'confirmed_at', source.confirmed_at, 'retired', source.retired) ORDER BY source.recorded_at, source.id) AS sources
            FROM rm.literature_search_links link
            JOIN rm.literature_source_status source ON source.id = link.source_id
            WHERE (link.search_id, link.rank) = (hit.search_id, hit.rank)
        ) matched ON true
        WHERE hit.search_id = %s ORDER BY hit.rank
    """, search_id)
    return {"search": rows[0], "hits": hits}


def link_source(store, search_id, rank, source_id):
    return store.query("human", "SELECT rm.link_literature_search_hit(%s,%s,%s) AS source_id",
                       search_id, rank, source_id)[0]


def history(store, claim):
    return store.query("human", """
        SELECT s.*, (SELECT count(*) FROM rm.literature_search_hits h WHERE h.search_id = s.id) AS hit_count
        FROM rm.literature_searches s WHERE s.claim_id = %s ORDER BY s.recorded_at DESC, s.id
    """, claim)


def duplicates(store, claim, limit=20):
    """Find matching frozen statement and scope records, excluding descriptive metadata."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Exact scope lookup limit must be between 1 and 100.")
    rows = store.query("human", """
        WITH subject AS (
            SELECT domain, rm.exact_claim_scope(contract_snapshot) AS scope
            FROM rm.claims WHERE id = %s AND frozen_at IS NOT NULL
        )
        SELECT encode(sha256(s.scope), 'hex') AS scope_sha256,
            coalesce((
                SELECT jsonb_agg(to_jsonb(candidate) ORDER BY candidate.id) FROM (
                    SELECT c.id, c.title, c.contract_sha256, active.status
                    FROM rm.claims c JOIN rm.claim_foundation_status active ON active.id = c.id
                    WHERE c.id <> %s AND c.domain = s.domain AND c.frozen_at IS NOT NULL
                      AND sha256(rm.exact_claim_scope(c.contract_snapshot)) = sha256(s.scope)
                      AND rm.exact_claim_scope(c.contract_snapshot) = s.scope
                    ORDER BY c.id LIMIT %s
                ) candidate
            ), '[]'::jsonb) AS matches
        FROM subject s
    """, claim, claim, limit)
    if not rows:
        raise ValueError("Exact scope lookup requires a frozen claim.")
    return rows[0]


def find(store, text, limit=30):
    """Rank exact identifiers and literal text matches within this database."""
    if not isinstance(text, str) or not text.strip() or len(text) > 300 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Provide up to 300 characters and a local search limit between 1 and 100.")
    return store.query("human", """
        WITH needle AS (SELECT lower(btrim(%s)) AS text),
        candidates(kind, id, claim_id, exact_key, title, contents, state) AS (
            SELECT 'source', s.id, s.claim_id, s.body ->> 'identifier', s.body ->> 'title',
                   concat_ws(' ', s.body ->> 'locator', s.body ->> 'excerpt', s.body ->> 'convention_map'),
                   CASE WHEN s.retired THEN 'retired'
                        WHEN s.confirmed_at IS NOT NULL THEN 'confirmed' ELSE 'unconfirmed' END
            FROM rm.literature_source_status s
            UNION ALL
            SELECT 'claim', c.id, c.id, c.id::text, c.title, c.statement, current_claim.status
            FROM rm.claims c JOIN rm.claim_foundation_status current_claim ON current_claim.id = c.id
            UNION ALL
            SELECT 'basis', i.id, NULL::uuid, i.logical_key, i.body ->> 'natural', i.body ->> 'formal', 'admitted'
            FROM rm.formal_item_status i WHERE i.admitted AND NOT i.needs_recheck
            UNION ALL
            SELECT 'failure', a.id, a.claim_id, a.id::text, c.title, a.failure_reason, a.finding::text
            FROM rm.attempts a JOIN rm.claims c ON c.id = a.claim_id
            WHERE a.finding = 'no_result' AND a.failure_reason IS NOT NULL
        )
        SELECT kind, id, claim_id, exact_key, title, state
        FROM candidates, needle
        WHERE strpos(lower(concat_ws(' ', exact_key, title, contents)), text) > 0
        ORDER BY (lower(exact_key) = text) DESC, (strpos(lower(title), text) > 0) DESC, kind, id
        LIMIT %s
    """, text, limit)
