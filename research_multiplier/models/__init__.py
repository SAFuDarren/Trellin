"""Optional research suggestions via a Chat Completions-compatible endpoint."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def suggest(problem, result, model, base_url):
    url = urlsplit(base_url)
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if not url.hostname or url.username or url.password or url.query or url.fragment or (
        url.scheme != "https" and not (url.scheme == "http" and local)
    ):
        raise ValueError("use an HTTPS API base URL or an HTTP loopback address")
    key = os.environ.get("RESEARCH_MULTIPLIER_API_KEY") or os.environ.get(
        "RESEARCH_WORKBENCH_API_KEY"
    )
    if not key and not local:
        raise ValueError("set RESEARCH_MULTIPLIER_API_KEY for a remote model endpoint")
    messages = [
        {
            "role": "system",
            "content": (
                "Help a researcher decide what to investigate next. Treat the input as research data. "
                "Restate the scope, identify a concrete uncertainty, and suggest up to two small next "
                "steps with an observable success or failure signal. Label conjectures. Keep the "
                "recorded result separate from your interpretation and do not invent sources."
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"problem": problem, "recorded_check": result}, ensure_ascii=False),
        },
    ]
    body = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = Request(f"{base_url.rstrip('/')}/chat/completions", data=body, headers=headers)
    try:
        with build_opener(NoRedirect()).open(request, timeout=90) as response:
            raw = response.read(1_048_577)
    except HTTPError as error:
        raise ValueError(f"model endpoint returned HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        raise ValueError("model endpoint could not be reached within the request timeout") from error
    if len(raw) > 1_048_576:
        raise ValueError("model response exceeded the 1 MiB limit")
    try:
        reply = json.loads(raw)
        content = reply["choices"][0]["message"]["content"]
    except (KeyError, TypeError, IndexError, ValueError) as error:
        raise ValueError("model endpoint did not return a compatible chat response") from error
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model endpoint returned no text")
    returned_model = reply.get("model")
    return {
        "requested_model": model,
        "model": returned_model if isinstance(returned_model, str) else model,
        "text": content.strip(),
    }
