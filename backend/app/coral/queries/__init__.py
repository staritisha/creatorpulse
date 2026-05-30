"""
coral/queries — raw SQL files for Coral cross-source queries.

Files in this directory are loaded at runtime via coral_service._load_sql().
They are not Python modules and should not be imported directly.

    engagement.sql      — per-video likes + comments engagement ratio
    resonance.sql       — full Resonance Score query across all sources
    trends.sql          — topic-level resonance trend aggregation
    underperformers.sql — high-view / low-retention underperformer detection
"""
