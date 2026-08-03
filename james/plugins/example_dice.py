"""Example plugin: a dice roller. Shows how trivially a new capability is added.

Install it by simply being in this package — JAMES discovers it automatically.
"""

from ..tools.base import tool


@tool(
    "roll_dice",
    "Roll one or more dice and return the results (great for games or decisions).",
    {
        "sides": {"type": "integer", "description": "Number of sides per die (default 6)."},
        "count": {"type": "integer", "description": "Number of dice to roll (default 1)."},
    },
)
def roll_dice(sides: int = 6, count: int = 1):
    import random  # nosec B311 - dice simulation, not security/crypto

    rolls = [random.randint(1, sides) for _ in range(max(1, count))]  # nosec B311
    return f"Rolled {count}d{sides}: {rolls} (total {sum(rolls)})"
