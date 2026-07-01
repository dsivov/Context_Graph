"""Site connectors — pluggable resolvers for platforms that hide documents
behind JS widgets / APIs (so files never appear as ``<a href>`` links).

The generic engine handles most sites; connectors are opt-in plugins for
specific site technologies. To add one, copy :mod:`example` and register it in
``DEFAULT_CONNECTORS`` below.
"""

from context_graph.webingest.connectors.base import Connector, download_as_data
from context_graph.webingest.connectors.finalsite import FinalsiteConnector
from context_graph.webingest.connectors.wordpress import WordPressConnector
from context_graph.webingest.connectors.boarddocs import BoardDocsConnector
from context_graph.webingest.connectors.example import ExampleConnector

# Connectors available on every rendered page. The LLM selector picks which one(s)
# fit the real site; without a selector, each is tried and self-gates via detect().
# ExampleConnector is a template and is intentionally NOT enabled.
DEFAULT_CONNECTORS = [
    FinalsiteConnector(),
    WordPressConnector(),
    BoardDocsConnector(),
]

__all__ = [
    "Connector",
    "download_as_data",
    "FinalsiteConnector",
    "WordPressConnector",
    "BoardDocsConnector",
    "ExampleConnector",
    "DEFAULT_CONNECTORS",
]
