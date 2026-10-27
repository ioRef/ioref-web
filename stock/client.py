"""Read-only client for the ioref-inventory API.

The two applications have separate databases and separate release cycles, so
this is the only channel between them. Everything here fails soft: inventory
being down degrades a part page to "availability unavailable" rather than
returning a 500, because the guide content is the primary thing on the page
and is perfectly readable without a stock count.
"""

import logging

import httpx
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

CACHE_PREFIX = "inventory"


class InventoryUnavailable(Exception):
    """Raised by the paged endpoints, where an empty page would mislead."""


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.INVENTORY_API_URL.rstrip("/"),
        headers={"Authorization": f"Bearer {settings.INVENTORY_API_KEY}"},
        timeout=settings.INVENTORY_API_TIMEOUT,
    )


def _get(path: str, params: dict | None = None) -> dict:
    with _client() as client:
        response = client.get(path, params=params or {})
        response.raise_for_status()
        return response.json()


def get_stock(part_number: str) -> dict | None:
    """Current stock for one part, or None if unavailable.

    Cached briefly: a part page is the most-hit page on the site and stock
    changes at most a few times a day, so this trades a little staleness for
    not putting inventory in the request path of every pageview.
    """
    key = f"{CACHE_PREFIX}:part:{part_number}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None  # Empty dict is a cached miss.

    try:
        data = _get(f"/api/v1/parts/{part_number}/")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            # Guide content exists for a part inventory does not stock. Cache
            # the miss so a typo'd part number is not retried on every request.
            cache.set(key, {}, settings.INVENTORY_CACHE_SECONDS)
            return None
        log.warning(
            "Inventory returned %s for %s", exc.response.status_code, part_number
        )
        return None
    except httpx.HTTPError as exc:
        log.warning("Inventory unreachable for %s: %s", part_number, exc)
        return None

    cache.set(key, data, settings.INVENTORY_CACHE_SECONDS)
    return data


def get_stock_many(part_numbers: list[str]) -> dict[str, dict]:
    """Stock for several parts at once, keyed by part number.

    A component page covers every part stocked under it, and the ceramic
    capacitors run to 33. Fetching them individually would mean 33 requests to
    render one page, so this uses the API's `part_number__in` filter.

    Returns {} when inventory is unreachable, and omits parts inventory does not
    know about -- a component can list a part number that was never stocked.
    """
    if not part_numbers:
        return {}

    found, missing = {}, []
    for number in part_numbers:
        cached = cache.get(f"{CACHE_PREFIX}:part:{number}")
        if cached is None:
            missing.append(number)
        elif cached:  # Empty dict is a cached miss.
            found[number] = cached

    if not missing:
        return found

    try:
        data = _get(
            "/api/v1/parts/",
            {"part_number__in": ",".join(missing), "limit": len(missing)},
        )
    except httpx.HTTPError as exc:
        # Partial results beat none: whatever was cached still renders.
        log.warning("Bulk stock lookup failed for %s: %s", missing, exc)
        return found

    returned = {part["part_number"]: part for part in data.get("results", [])}
    for number in missing:
        part = returned.get(number)
        # Cache misses as {} too, so a component listing a part number that
        # inventory has never heard of is not re-requested every pageview.
        cache.set(
            f"{CACHE_PREFIX}:part:{number}",
            part or {},
            settings.INVENTORY_CACHE_SECONDS,
        )
        if part:
            found[number] = part

    return found


def list_by_group(group_slug: str) -> list[dict]:
    """Every part inventory files under a group, newest stock included.

    This is what lets a component page stop hand-listing part numbers: the
    membership question -- "is this a potentiometer" -- is answered in
    inventory, where the part is maintained, rather than duplicated here.

    Returns [] when inventory is unreachable. The page still renders its
    documentation, which is the part that matters.

    An empty result is logged, because inventory answers an unknown group with
    HTTP 200 and count 0 -- exactly what a real but empty group returns. That
    ambiguity is not hypothetical: inventory renamed the `resistors` group to
    `resistor`, the resistor page rendered an empty stock table for two days,
    and nothing anywhere reported it. See `manage.py check_groups` for the
    deliberate version of this check.
    """
    if not group_slug:
        return []

    key = f"{CACHE_PREFIX}:group:{group_slug}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        data = _get("/api/v1/parts/", {"group": group_slug, "limit": 200})
    except httpx.HTTPError as exc:
        log.warning("Group lookup failed for %s: %s", group_slug, exc)
        return []

    results = data.get("results", [])
    if not results:
        log.warning(
            "Group %r matched no parts. Either it was renamed in inventory and "
            "the front matter is stale, or every part in it was retired.",
            group_slug,
        )
    cache.set(key, results, settings.INVENTORY_CACHE_SECONDS)
    return results


def list_parts(
    *,
    search: str = "",
    status: str = "",
    needs_restock: bool = False,
    include_discontinued: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """A page of parts. Raises InventoryUnavailable rather than returning empty.

    The browse view must be able to tell "no parts match your filter" apart
    from "inventory is down"; returning an empty list for both would quietly
    show an empty catalogue during an outage.
    """
    params = {"limit": limit, "offset": offset}
    if search:
        params["search"] = search
    if status:
        params["status"] = status
    if needs_restock:
        params["needs_restock"] = "1"
    if not include_discontinued:
        params["include_discontinued"] = "0"

    try:
        return _get("/api/v1/parts/", params)
    except httpx.HTTPError as exc:
        log.warning("Inventory list failed: %s", exc)
        raise InventoryUnavailable from exc


def list_categories() -> list[dict]:
    """Every category a group can belong to: Input, Output, Power...

    Raises InventoryUnavailable rather than returning [], matching list_parts():
    this drives the /categories/<slug>/ browse page, and an empty list here would be
    indistinguishable from "no categories exist" rather than "inventory is down".

    Not cached: this is a handful of rows, requested once per category-page
    view, and freshness matters more than shaving one request off a page that
    is already doing a live lookup for its group listing.
    """
    try:
        data = _get("/api/v1/categories/", {"limit": 100})
    except httpx.HTTPError as exc:
        log.warning("Category list failed: %s", exc)
        raise InventoryUnavailable from exc
    return data.get("results", [])


def list_groups_by_category(category_slug: str) -> list[dict]:
    """Every group inventory files under a category, guided or not.

    This is what the /categories/<slug>/ page is built from. A group with no guide in
    content/ still appears, linking to its filtered view in ioref-inventory
    instead of a guide page. Category is inventory's fact, having a guide is
    ours, and the two lists are not the same list.

    Raises InventoryUnavailable rather than returning []. Rendering an outage
    as "this category is empty" would be worse than an error page.
    """
    try:
        data = _get("/api/v1/groups/", {"category": category_slug, "limit": 200})
    except httpx.HTTPError as exc:
        log.warning("Group-by-category lookup failed for %s: %s", category_slug, exc)
        raise InventoryUnavailable from exc
    return data.get("results", [])


def list_ungrouped_parts_by_category(category_slug: str) -> list[dict]:
    """Parts filed directly under a category because they have no group."""
    try:
        data = _get(
            "/api/v1/parts/",
            {"category": category_slug, "ungrouped": "true", "limit": 200},
        )
    except httpx.HTTPError as exc:
        log.warning("Ungrouped-part lookup failed for %s: %s", category_slug, exc)
        raise InventoryUnavailable from exc
    return data.get("results", [])


def get_part(part_number: str) -> dict | None:
    try:
        return _get(f"/api/v1/parts/{part_number}/")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise InventoryUnavailable from exc
    except httpx.HTTPError as exc:
        raise InventoryUnavailable from exc
