"""OntologyService — glue between the ontology store and the ``/ontology`` API.

Holds a per-workspace :class:`~context_graph.ontology.store.OntologyStore` and
offers the operations the router and server need:

* ``get_summary(ws)``       — metadata + object/link type overview + lint,
* ``get_ontology(ws)``      — the full ontology dict (for the editor),
* ``save`` / ``delete``     — persist / remove (validated on save),
* ``validate_extraction``   — check a batch of extracted entities/relations
                              against the saved ontology.

Everything here is offline (no LLM); the NL author is invoked from the router.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from context_graph.ontology.schema import LinkType, ObjectType, Ontology
from context_graph.ontology.store import OntologyStore
from context_graph.ontology.validate import ExtractionValidator


class OntologyService:
    def __init__(self, store: OntologyStore) -> None:
        self._store = store

    @property
    def store(self) -> OntologyStore:
        return self._store

    # -- queries -----------------------------------------------------------

    def get_summary(self, workspace: str) -> Dict[str, Any]:
        onto = self._store.load(workspace)
        if onto is None:
            return {"workspace": workspace, "exists": False,
                    "object_types": [], "link_types": []}
        meta = self._store.meta(workspace) or {}
        return {
            "workspace": workspace,
            "exists": True,
            "name": onto.name,
            "version": onto.version,
            "updated_at": meta.get("updated_at"),
            "object_types": [self._obj_info(o) for o in onto.object_types.values()],
            "link_types": [self._link_info(lt) for lt in onto.link_types.values()],
            "lint": onto.lint(),
            "ontology": onto.to_dict(),
        }

    def get_ontology(self, workspace: str) -> Optional[Dict[str, Any]]:
        onto = self._store.load(workspace)
        return onto.to_dict() if onto is not None else None

    @staticmethod
    def _obj_info(o: ObjectType) -> Dict[str, Any]:
        return {
            "name": o.name,
            "description": o.description,
            "properties": [{"name": p.name, "kind": p.kind.value, "required": p.required}
                           for p in o.properties.values()],
        }

    @staticmethod
    def _link_info(lt: LinkType) -> Dict[str, Any]:
        return {
            "name": lt.name,
            "source_types": list(lt.source_types),
            "target_types": list(lt.target_types),
            "cardinality": lt.cardinality.value,
            "property_count": len(lt.properties),
        }

    # -- mutations ---------------------------------------------------------

    def save(self, workspace: str, ontology_dict: Dict[str, Any]) -> Ontology:
        """Deserialize + persist. Raises ValueError on a malformed or
        self-inconsistent ontology (store validates via lint)."""
        onto = Ontology.from_dict(ontology_dict)   # KeyError/ValueError on bad shape
        return self._store.save(workspace, onto)   # validate_ontology + version bump

    def delete(self, workspace: str) -> bool:
        return self._store.delete(workspace)

    # -- extraction validation --------------------------------------------

    def validate_extraction(self, workspace: str,
                            entities: List[Any], relations: List[Any],
                            *, closed_world: bool = False) -> Dict[str, Any]:
        onto = self._store.load(workspace)
        if onto is None:
            return {"exists": False, "workspace": workspace}
        report = ExtractionValidator(onto, closed_world=closed_world).validate(
            entities=entities, relations=relations)
        return {
            "exists": True,
            **report.summary(),
            "items": [{"kind": i.kind, "ref": i.ref, "status": i.status, "ok": i.ok,
                       "errors": i.errors, "warnings": i.warnings, "coerced": i.coerced}
                      for i in report.items],
        }
