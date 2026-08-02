"""``$geoNear`` emulation over mongomock, for the offline E2E harness (PRO-83).

``matching_service.determine_best_pro`` is the routing engine: progressive
10 → 20 → 30 km radius stepping, load balancing at ``MAX_PRO_LOAD``, and a
composite slot/rating/no-show score. mongomock does not implement ``$geoNear``, so
the existing unit tests stub ``users_collection.aggregate`` wholesale — which means
the radius stepping and the scoring never actually run.

An end-to-end harness cannot do that: "no pro found at max radius → customer
fallback" is only a real assertion if the radii are real. So this module replaces
the *one* unsupported Mongo operator and leaves every line of routing logic
executing for real. It is the same category of substitution as mongomock itself.

Distances use the haversine formula on a spherical Earth, matching the
``"spherical": True`` mode the production pipeline requests.
"""

from __future__ import annotations

import math
from typing import Any

EARTH_RADIUS_M = 6371008.8  # IUGG mean Earth radius, as used by MongoDB


def haversine_meters(a: list[float], b: list[float]) -> float:
    """Great-circle distance between two ``[lon, lat]`` points, in metres."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _matches(doc: dict, query: dict) -> bool:
    """Evaluate the small subset of query operators `base_filter` actually uses:
    equality, ``$ne`` and ``$nin``. Anything else is a loud failure rather than a
    silent pass — a widened production filter must not quietly stop being applied.
    """
    for field, condition in query.items():
        value = doc.get(field)
        if isinstance(condition, dict):
            for op, operand in condition.items():
                if op == "$ne":
                    if value == operand:
                        return False
                elif op == "$nin":
                    if value in operand:
                        return False
                elif op == "$in":
                    if value not in operand:
                        return False
                else:
                    raise NotImplementedError(
                        f"geo_shim does not implement the {op!r} operator used by "
                        f"determine_best_pro's base_filter — extend it deliberately."
                    )
        elif value != condition:
            return False
    return True


def _sorted_by(docs: list[dict], spec: dict) -> list[dict]:
    for field, direction in reversed(list(spec.items())):
        # Dotted paths (e.g. "social_proof.rating") are what the pipeline sorts on.
        def key(doc: dict, path: str = field) -> Any:
            cur: Any = doc
            for part in path.split("."):
                cur = (cur or {}).get(part) if isinstance(cur, dict) else None
            return cur if cur is not None else 0

        docs = sorted(docs, key=key, reverse=direction < 0)
    return docs


class _AsyncDocs:
    """Async iterator over an already-materialized result set, so the production
    ``async for doc in users_collection.aggregate(...)`` loop is unchanged."""

    def __init__(self, docs: list[dict]):
        self._docs = docs

    def __aiter__(self) -> "_AsyncDocs":
        self._it = iter(self._docs)
        return self

    async def __anext__(self) -> dict:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def to_list(self, length: int | None = None) -> list[dict]:
        return self._docs[:length] if length else list(self._docs)


class GeoAwareCollection:
    """Transparent proxy around a mongomock collection that understands ``$geoNear``.

    Every other attribute (``find``, ``find_one``, ``update_one``, …) is delegated
    untouched, so the collection behaves exactly as it does for the rest of the suite.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def aggregate(self, pipeline, *args, **kwargs):
        if not pipeline or "$geoNear" not in (pipeline[0] or {}):
            return self._inner.aggregate(pipeline, *args, **kwargs)
        return _GeoNearRun(self._inner, pipeline)


class _GeoNearRun:
    """Lazily runs the emulated pipeline when it is first iterated.

    Deferred because ``aggregate()`` is called synchronously in production but the
    underlying mongomock read is async.
    """

    def __init__(self, collection, pipeline: list[dict]):
        self._collection = collection
        self._pipeline = pipeline

    async def _run(self) -> list[dict]:
        stage = self._pipeline[0]["$geoNear"]
        near = stage["near"]["coordinates"]
        max_distance = stage.get("maxDistance")
        query = stage.get("query", {})
        distance_field = stage.get("distanceField", "dist_meters")

        results: list[dict] = []
        async for doc in self._collection.find({}):
            location = doc.get("location") or {}
            coords = location.get("coordinates")
            # Real $geoNear silently omits documents with no indexed location.
            if not coords or len(coords) != 2:
                continue
            if not _matches(doc, query):
                continue
            distance = haversine_meters(near, coords)
            if max_distance is not None and distance > max_distance:
                continue
            enriched = dict(doc)
            enriched[distance_field] = distance
            results.append(enriched)

        # $geoNear itself orders by distance ascending; later $sort stages re-order.
        results.sort(key=lambda d: d[distance_field])

        for stage_spec in self._pipeline[1:]:
            if "$sort" in stage_spec:
                results = _sorted_by(results, stage_spec["$sort"])
            elif "$limit" in stage_spec:
                results = results[: stage_spec["$limit"]]
            else:
                raise NotImplementedError(
                    f"geo_shim does not implement the pipeline stage "
                    f"{list(stage_spec)} — extend it deliberately."
                )
        return results

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for doc in await self._run():
            yield doc

    async def to_list(self, length: int | None = None) -> list[dict]:
        docs = await self._run()
        return docs[:length] if length else docs
