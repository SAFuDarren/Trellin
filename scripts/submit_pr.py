# SPDX-License-Identifier: Apache-2.0
"""Validate and submit the current committed branch as a Trellin pull request."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

from check_privacy import scan_text
from review_merge import api, check_version, git
from tasks import REPOSITORY, check_pull_request, repository_name


def existing_pull(upstream: str, source: str, branch: str) -> dict | None:
    pages = api(f"repos/{upstream}/pulls", "--method", "GET", "--paginate", "--slurp",
                "-f", "base=main", "-f", "state=open", "-f", f"head={source.split('/')[0]}:{branch}")
    matches = [pull for page in pages for pull in page
               if ((pull["head"].get("repo") or {}).get("full_name") or "").casefold() == source.casefold()]
    if len(matches) > 1:
        raise SystemExit("Multiple open PRs match the source branch. Inspect them before submitting.")
    return matches[0] if matches else None


def verify_head(pull: dict, head: str, source: str, branch: str) -> None:
    remote = pull["head"]
    remote_source = ((remote.get("repo") or {}).get("full_name") or "").casefold()
    if remote["sha"] != head or remote["ref"] != branch or remote_source != source.casefold():
        raise SystemExit(f"PR #{pull['number']} points to a different source revision. Inspect it before continuing.")


def submit(upstream: str, source: str, branch: str, head: str, title: str, body_file: Path, bump: str) -> dict:
    prior = existing_pull(upstream, source, branch)
    if prior:
        verify_head(prior, head, source, branch)

    base_ref = f"repos/{upstream}/git/ref/heads/main"
    source_ref = f"repos/{source}/git/ref/heads/{quote(branch, safe='/')}"
    base = api(base_ref)["object"]["sha"]
    if api(source_ref)["object"]["sha"] != head:
        raise SystemExit("Push the verified local branch head before submitting.")
    git("fetch", "--no-tags", "--no-write-fetch-head", f"https://github.com/{upstream}.git", "refs/heads/main")
    check_version(base, head, bump)
    if prior:
        if prior["base"]["sha"] != base or api(base_ref)["object"]["sha"] != base:
            raise SystemExit(f"PR #{prior['number']} needs a current main before review. Update the existing PR.")
        return prior

    body = body_file.read_text(encoding="utf-8")
    findings = scan_text(title, "proposed PR title") + scan_text(body, "proposed PR body")
    if findings:
        print("\n".join(str(finding) for finding in findings))
        raise SystemExit("Remove local or private details before submitting the PR.")
    proposed = {"head": {"ref": branch, "repo": {"full_name": source}}, "body": body,
                "user": {"login": api("user")["login"]}}
    if check_pull_request(upstream, proposed):
        raise SystemExit("Resolve the linked issue and active claim before submitting.")
    if api(base_ref)["object"]["sha"] != base or api(source_ref)["object"]["sha"] != head:
        raise SystemExit("Main or the pushed branch moved during preflight. Recheck the current revisions.")

    fields = ["-f", f"head={source.split('/')[0]}:{branch}", "-f", "base=main", "-f", f"title={title}"]
    if source.casefold() != upstream.casefold() and source.split("/")[0].casefold() == upstream.split("/")[0].casefold():
        fields.extend(["-f", f"head_repo={source.split('/')[1]}"])
    with tempfile.TemporaryDirectory(prefix="trellin-submit-") as folder:
        checked_body = Path(folder) / "body.md"
        checked_body.write_text(body, encoding="utf-8")
        try:
            pull = api(f"repos/{upstream}/pulls", "--method", "POST", *fields, "-F", f"body=@{checked_body}")
        except SystemExit:
            # The create may have landed even though its response did not reach us.
            pull = existing_pull(upstream, source, branch)
            if pull is None:
                raise
    verify_head(pull, head, source, branch)
    if pull["base"]["sha"] != base:
        raise SystemExit(f"PR #{pull['number']} was opened against a newer main. Rebase and update that PR.")
    return pull


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="Upstream GitHub repository in owner/name form.")
    parser.add_argument("--head-repo", help="Pushed source repository, if it is a fork of upstream.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--bump", choices=("patch", "minor", "major"), default="patch")
    args = parser.parse_args(argv)
    upstream = args.repo or repository_name()
    source = args.head_repo or upstream
    if not REPOSITORY.fullmatch(upstream) or not REPOSITORY.fullmatch(source):
        parser.error("Use owner/name for both repositories.")
    if not args.body_file.is_file():
        parser.error("Supply a readable Markdown file with --body-file.")
    body_file = args.body_file.resolve()
    os.environ["GH_REPO"] = upstream
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
    if branch == "main" or git("status", "--porcelain"):
        raise SystemExit("Submit from a clean task branch.")
    pull = submit(upstream, source, branch, git("rev-parse", "HEAD"), args.title, body_file, args.bump)
    print(pull["html_url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
