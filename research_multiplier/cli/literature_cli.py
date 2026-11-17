"""Search explicit public metadata and retain the evidence trail."""

from datetime import date, datetime
import json
from pathlib import Path
from uuid import UUID

from research_multiplier.workflows import literature, literature_vectors
from research_multiplier.storage.postgres import Store


from ._command import register
from ._output import json_default as _encode


def add_commands(commands):
    command = commands.add_parser("literature", help="Query the local literature and search public Crossref metadata.")
    command.set_defaults(_handler=run)
    actions = command.add_subparsers(dest="literature_action", required=True)
    find = register(actions, "find", help="Find exact identifiers and text in the local workspace database.")
    find.add_argument("text")
    find.add_argument("--limit", type=int, default=30)
    formalpedia = register(actions, "formalpedia", roles=(), workspace=False,
                           help="Find theorems in a local Formalpedia snapshot without network access.")
    formalpedia.add_argument("text")
    formalpedia.add_argument("--snapshot", type=Path, required=True)
    formalpedia.add_argument("--environment", help="Exact environment identifier from MANIFEST.json.")
    formalpedia.add_argument("--limit", type=int, default=20)
    formalpedia.add_argument("--proved-only", action="store_true",
                            help="Show only theorems with a complete accepted upstream proof.")
    exact = register(actions, "duplicates", help="Find other frozen claims with the same recorded statement and scope.")
    exact.add_argument("claim_id", type=UUID)
    exact.add_argument("--limit", type=int, default=20)
    search = register(actions, "search", help="Explicitly send a public query to Crossref for a frozen public claim.")
    search.add_argument("claim_id", type=UUID)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=12)
    search.add_argument("--since", type=date.fromisoformat)
    search.add_argument("--until", type=date.fromisoformat)
    local = register(actions, "import-search", help="Record a completed search performed outside the app.")
    local.add_argument("claim_id", type=UUID)
    local.add_argument("file", type=Path)
    show = register(actions, "show", help="Show an exact recorded search and its linked originals.")
    show.add_argument("search_id", type=UUID)
    history = register(actions, "history", help="Show the metadata searches performed for a claim.")
    history.add_argument("claim_id", type=UUID)
    link = register(actions, "link-source", help="Link a retrieved identifier to its archived original for this claim.")
    link.add_argument("search_id", type=UUID)
    link.add_argument("rank", type=int)
    link.add_argument("source_id", type=UUID)
    document = register(actions, "document", help="Export an eligible document and its exact embedding input hash.")
    document.add_argument("kind", choices=literature_vectors.KINDS)
    document.add_argument("id", type=UUID)
    upload = register(actions, "import-vectors", help="Index external vectors for exact document versions.")
    upload.add_argument("file", type=Path)
    similar = register(actions, "similar", help="Search one indexed model and revision using a supplied query vector.")
    similar.add_argument("file", type=Path)
    similar.add_argument("--limit", type=int, default=20)
    similar.add_argument("--kind", choices=literature_vectors.KINDS)
    register(actions, "vector-profiles", help="List indexed model profiles.")


def run(args):
    if args.literature_action == "formalpedia":
        from research_multiplier.sources.formalpedia import FormalpediaSnapshot

        snapshot = FormalpediaSnapshot(args.snapshot)
        result = snapshot.search(args.text, environment=args.environment, limit=args.limit,
                                 proved_only=args.proved_only)
        print(json.dumps({"matches": result}, indent=2, ensure_ascii=False, default=_encode))
        return 0
    with Store() as store:
        action = args.literature_action
        if action == "find":
            result = literature.find(store, args.text, args.limit)
        elif action == "duplicates":
            result = literature.duplicates(store, args.claim_id, args.limit)
        elif action == "search":
            result = literature.search(store, args.claim_id, args.query,
                                       limit=args.limit, since=args.since, until=args.until)
        elif action == "import-search":
            result = literature.import_search(store, args.claim_id, args.file)
        elif action == "show":
            result = literature.show(store, args.search_id)
        elif action == "history":
            result = literature.history(store, args.claim_id)
        elif action == "link-source":
            result = literature.link_source(store, args.search_id, args.rank, args.source_id)
        elif action == "document":
            result = literature_vectors.document(store, args.kind, args.id)
        elif action == "import-vectors":
            result = literature_vectors.import_vectors(store, literature_vectors.read_document(args.file))
        elif action == "similar":
            result = literature_vectors.similar(store, literature_vectors.read_document(args.file), args.limit, args.kind)
        else:
            result = literature_vectors.profiles(store)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=_encode))
    if action == "search" and result["search"]["body"]["status"] == "provider_error":
        return 1
    return 0
