"""Pipelined Graph/REST pagination helper.

Most paginated Microsoft Graph endpoints return `@odata.nextLink` opaque
URLs that can only be fetched serially. But the *parse* step is CPU
work — independent of the network. We pipeline them: while parsing
page N, fire page N+1 in a worker thread. Result is ~1.5-2x faster
on I/O-bound paginations.

Use via `paginate(client, url, headers, timeout, parse=...)`. The
`parse` callback is invoked once per response payload with the full
JSON dict; return whatever you want collected. Items are flattened.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, TypeVar

import httpx


T = TypeVar("T")


def paginate(
    client: httpx.Client,
    first_url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    parse: Callable[[dict[str, Any]], Iterable[T]],
    max_pages: int = 200,
    on_status: Callable[[httpx.Response], httpx.Response | None] | None = None,
) -> list[T]:
    """Fetch a paginated Graph/Defender list with one-page-ahead prefetch.

    `parse` receives the response JSON and yields items to collect.

    `on_status` is an optional callback that runs before `raise_for_status`.
    It can mutate / replace the response (e.g. retry with different params
    on 403) and either return a replacement response or `None` to keep the
    original. Return value must be the response to act on."""

    results: list[T] = []
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="graph-paginate") as ex:
        future = ex.submit(client.get, first_url, headers=headers, timeout=timeout)
        pages = 0
        while future is not None and pages < max_pages:
            resp = future.result()
            if on_status is not None:
                replacement = on_status(resp)
                if replacement is not None:
                    resp = replacement
            resp.raise_for_status()
            payload = resp.json()

            # Pre-fire the next page *before* parsing the current one.
            next_url = payload.get("@odata.nextLink")
            future = (
                ex.submit(client.get, next_url, headers=headers, timeout=timeout)
                if next_url
                else None
            )

            for item in parse(payload):
                results.append(item)
            pages += 1
    return results
