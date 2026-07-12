"""Context Graph apps — Stage-2 App bundles (the L2 composition layer, P0).

An App is a versioned bundle of signed blocks addressed as
Domain(workspace) / Project / App(app-id). See docs/PLATFORM_ARCHITECTURE.html.
"""

from context_graph.apps.schema import AppBundle

__all__ = ["AppBundle"]
