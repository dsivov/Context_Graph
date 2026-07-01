"""Web-ingest job service (P-web, step 6).

Crawling a site is slow, so ``/scrape`` starts a background job and returns an id
the client polls. This service owns the job registry and runs each ingest as an
``asyncio`` task, pinning the request's workspace so the crawl writes into the
right tenant even though it runs outside the request.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from lightrag.utils import logger

from context_graph.webingest.ingest import WebIngestor


class WebIngestService:
    """Starts and tracks background site-ingest jobs for a rag instance."""

    def __init__(
        self,
        rag: Any,
        *,
        fetcher: Any = None,                       # inject a StaticFetcher in tests
        input_dir_for: Any = None,                 # ws -> Path (where data files land)
        scan_trigger: Any = None,                  # async ws -> None (runs CG's scan)
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex[:12],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._rag = rag
        self._fetcher = fetcher
        self._input_dir_for = input_dir_for
        self._scan_trigger = scan_trigger
        self._id = id_factory
        self._clock = clock
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    def start(self, workspace: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a job and launch it in the background; returns the job record."""
        job_id = self._id()
        job = {
            "job_id": job_id,
            "workspace": workspace,
            "seed": params.get("seed_url"),
            "state": "running",
            "summary": None,
            "error": None,
            "started_at": self._clock(),
        }
        self._jobs[job_id] = job
        task = asyncio.create_task(self._run(job, workspace, params))
        self._tasks[job_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(job_id, None))
        return dict(job)

    async def _run(self, job: Dict[str, Any], workspace: str, params: Dict[str, Any]) -> None:
        from lightrag.api.workspace_pool import _current_workspace

        token = _current_workspace.set(workspace)
        try:
            download_dir = self._input_dir_for(workspace) if self._input_dir_for else None
            # Optional LLM site analyst (relevance filter + document-URL extraction).
            analyst = None
            if params.pop("analyze", False):
                llm = getattr(self._rag, "llm_model_func", None)
                if llm is not None:
                    from context_graph.webingest.analyst import SiteAnalyst
                    analyst = SiteAnalyst(llm)
            summary = await WebIngestor(self._rag).ingest_site(
                fetcher=self._fetcher, download_dir=download_dir, analyst=analyst, **params
            )
            job["summary"] = summary.to_dict()
            # Data files were saved to the input dir → run CG's scan to ingest them.
            if summary.documents and self._scan_trigger is not None:
                job["state"] = "scanning"
                await self._scan_trigger(workspace)
            job["state"] = "done"
        except Exception as e:  # a failed crawl must not crash the server
            job["error"] = str(e)
            job["state"] = "error"
            logger.error(f"web-ingest job {job['job_id']} failed: {e}", exc_info=True)
        finally:
            _current_workspace.reset(token)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self._jobs.get(job_id)
        return dict(job) if job is not None else None

    def list(self) -> List[Dict[str, Any]]:
        return [dict(j) for j in self._jobs.values()]

    async def wait(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Await a job to completion (used by tests)."""
        task = self._tasks.get(job_id)
        if task is not None:
            await task
        return self.get(job_id)
