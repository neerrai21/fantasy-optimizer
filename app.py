import asyncio
import os
import time

import httpx
from fastmcp import FastMCP

mcp = FastMCP("Fantasy Sports Hyper-Optimizer")

BASE_URL = "https://api.sleeper.app/v1"
SUPPORTED_SPORTS = {"nfl", "nba"}

# Sleeper's full player list is ~5 MB, so we download it once and reuse it.
_PLAYER_CACHE: dict = {}
CACHE_SECONDS = 6 * 60 * 60

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}


async def _get_players(client: httpx.AsyncClient, sport: str) -> dict:
    cached = _PLAYER_CACHE.get(sport)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    res = await client.get(f"{BASE_URL}/players/{sport}")
    res.raise_for_status()
    data = res.json()
    _PLAYER_CACHE[sport] = (time.time(), data)
    return data


def _describe(players: dict, player_id: str) -> str:
    p = players.get(str(player_id))
    if not p:
        return f"Unknown player ({player_id})"
    name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    pos = p.get("position") or "?"
    team = p.get("team") or "FA"
    return f"{name} ({pos}, {team})"


@mcp.tool(annotations={"title": "Trending waiver wire players", **READ_ONLY})
async def get_trending_waiver_players(sport: str = "nfl", hours: int = 24, limit: int = 10) -> str:
    """Shows the players most added in fantasy leagues on Sleeper recently.

    sport: "nfl" or "nba". hours: look-back window (1-72). limit: how many players (1-25).
    """
    sport = sport.lower().strip()
    if sport not in SUPPORTED_SPORTS:
        return f"Sport '{sport}' is not supported. Use one of: {', '.join(sorted(SUPPORTED_SPORTS))}."
    hours = max(1, min(int(hours), 72))
    limit = max(1, min(int(limit), 25))

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            trend_res, players = await asyncio.gather(
                client.get(f"{BASE_URL}/players/{sport}/trending/add", params={"lookback_hours": hours, "limit": limit}),
                _get_players(client, sport),
            )
            trend_res.raise_for_status()
            trending = trend_res.json()[:limit]
    except httpx.HTTPError as exc:
        return f"Could not reach Sleeper right now ({exc.__class__.__name__}). Try again in a minute."

    if not trending:
        return "Sleeper returned no trending players for that window."

    lines = [f"Top {len(trending)} {sport.upper()} waiver adds in the past {hours} hours (source: Sleeper):"]
    for i, item in enumerate(trending, 1):
        lines.append(f"{i}. {_describe(players, item.get('player_id'))} - added in {item.get('count'):,} leagues")
    return "\n".join(lines)


@mcp.tool(annotations={"title": "League standings", **READ_ONLY})
async def get_league_standings(league_id: str) -> str:
    """Shows standings (owner, record, points scored) for a Sleeper league ID."""
    league_id = league_id.strip()
    if not league_id.isdigit():
        return "A Sleeper league ID is a long number, for example 991751322049077248."

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            rosters_res, users_res = await asyncio.gather(
                client.get(f"{BASE_URL}/league/{league_id}/rosters"),
                client.get(f"{BASE_URL}/league/{league_id}/users"),
            )
    except httpx.HTTPError as exc:
        return f"Could not reach Sleeper right now ({exc.__class__.__name__}). Try again in a minute."

    if rosters_res.status_code != 200 or users_res.status_code != 200:
        return f"League ID '{league_id}' was not found. Please double check the number."

    rosters = rosters_res.json() or []
    owners = {u.get("user_id"): u.get("display_name") for u in (users_res.json() or [])}
    if not rosters:
        return "That league has no rosters yet."

    rows = []
    for r in rosters:
        s = r.get("settings") or {}
        points = (s.get("fpts") or 0) + (s.get("fpts_decimal") or 0) / 100
        rows.append(
            (
                s.get("wins") or 0,
                points,
                owners.get(r.get("owner_id")) or "Unclaimed team",
                s.get("losses") or 0,
                s.get("ties") or 0,
            )
        )
    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)

    lines = [f"Standings for league {league_id} ({len(rows)} teams):"]
    for i, (wins, points, owner, losses, ties) in enumerate(rows, 1):
        record = f"{wins}-{losses}" + (f"-{ties}" if ties else "")
        lines.append(f"{i}. {owner}: {record}, {points:,.2f} points")
    return "\n".join(lines)


if __name__ == "__main__":
    # On your PC (Claude Desktop) this runs locally over stdio, exactly as before.
    # On a hosting service, set MCP_TRANSPORT=http and it serves at /mcp.
    if os.environ.get("MCP_TRANSPORT", "stdio").lower() == "http":
        mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
    else:
        mcp.run()
