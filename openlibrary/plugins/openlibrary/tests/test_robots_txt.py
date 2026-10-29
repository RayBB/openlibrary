from pathlib import Path
from urllib.robotparser import RobotFileParser

import pytest

ROBOTS_TXT = Path(__file__).parents[4] / "static" / "robots.txt"


def parse_groups(text: str) -> list[tuple[list[str], list[str]]]:
    """Return (user_agents, disallows) for each group in a robots.txt."""
    groups: list[tuple[list[str], list[str]]] = []
    in_agents = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = (part.strip() for part in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            if not in_agents:
                groups.append(([], []))
            groups[-1][0].append(value)
            in_agents = True
        else:
            in_agents = False
            if field == "disallow" and groups:
                groups[-1][1].append(value)
    return groups


def test_every_group_has_the_full_disallow_list():
    # A crawler that matches a named group ignores the "*" group entirely,
    # so a group with fewer rules silently unblocks paths for that crawler.
    groups = parse_groups(ROBOTS_TXT.read_text())
    default = next(disallows for agents, disallows in groups if "*" in agents)
    for agents, disallows in groups:
        assert sorted(disallows) == sorted(default), agents


@pytest.mark.parametrize("agent", ["Googlebot", "bingbot", "ClaudeBot", "SomeOtherCrawler"])
@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/works/OL45804W/Fantastic_Mr_Fox", True),
        ("/authors/OL34184A/Roald_Dahl", True),
        ("/search?q=fox", False),
        ("/search.json?q=fox", False),
        ("/account/login", False),
        ("/api/books", False),
    ],
)
def test_crawler_access(agent, path, allowed):
    parser = RobotFileParser()
    parser.parse(ROBOTS_TXT.read_text().splitlines())
    assert parser.can_fetch(agent, f"https://openlibrary.org{path}") is allowed
