"""State persistence — tracks processed article URLs across runs."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def load_state(path: str) -> dict:
    """Load state from disk. Returns empty state if file doesn't exist."""
    p = Path(path)
    if not p.exists():
        return {'processed_urls': [], 'last_run': None}
    try:
        with open(p) as f:
            data = json.load(f)
        # Normalise: ensure processed_urls is a list
        if 'processed_urls' not in data:
            data['processed_urls'] = []
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not load state file %s: %s — starting fresh", path, e)
        return {'processed_urls': [], 'last_run': None}


def save_state(path: str, state: dict) -> None:
    """Persist state to disk."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w') as f:
        json.dump(state, f, indent=2)
    logger.debug("State saved to %s (%d URLs)", path, len(state['processed_urls']))


def filter_new_articles(articles: list[dict], state: dict) -> list[dict]:
    """Return only articles whose URLs have not been seen before."""
    seen = set(state.get('processed_urls', []))
    return [a for a in articles if a.get('url') and a['url'] not in seen]


def mark_processed(state: dict, articles: list[dict]) -> dict:
    """Add article URLs to the processed set. Returns updated state."""
    seen = set(state.get('processed_urls', []))
    for a in articles:
        url = a.get('url')
        if url:
            seen.add(url)
    return {
        **state,
        'processed_urls': sorted(seen),
        'last_run': datetime.now(tz=timezone.utc).isoformat(),
    }
