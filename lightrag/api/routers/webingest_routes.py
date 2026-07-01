"""Web-ingest API — crawl a site into CG (P-web).

  POST /scrape          — start a background crawl+ingest job; returns a job id
  GET  /scrape/{job_id} — poll a job's status + summary
  GET  /scrape          — list jobs in this workspace

The crawl is polite by default (robots respected, rate-limited, same-domain,
page/depth caps). Each page's URL becomes ``rc.provenance`` on everything CG
extracts, so answers can cite the page they came from.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.utils import logger

from context_graph.webingest.urls import is_http


class ScrapeRequest(BaseModel):
    url: str = Field(description="Seed URL to crawl (http/https).")
    max_pages: int = Field(default=50, ge=1, le=500, description="Max HTML pages to ingest.")
    max_documents: int = Field(default=200, ge=0, le=2000, description="Max data files to download.")
    max_depth: int = Field(default=2, ge=0, le=6, description="Max link depth from the seed.")
    same_domain: bool = Field(default=True, description="Stay on the seed's host.")
    respect_robots: bool = Field(default=True, description="Honor robots.txt.")
    render_js: bool = Field(default=False, description="Render pages with a headless browser (JS sites).")
    analyze: bool = Field(default=False, description="Use the LLM site analyst to filter relevance + extract document URLs.")
    use_sitemap: bool = Field(default=True, description="Seed the crawl from sitemap.xml.")
    min_interval: float = Field(default=1.0, ge=0.0, le=30.0,
                                description="Seconds between requests to the same host.")

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        v = v.strip()
        if not is_http(v):
            raise ValueError("url must be an http(s) URL")
        return v


class JobResponse(BaseModel):
    job_id: str
    workspace: Optional[str] = None
    seed: Optional[str] = None
    state: str
    summary: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def create_webingest_routes(rag, service, *, api_key: Optional[str] = None,
                            workspace_resolver=None):
    """Build the /scrape router bound to *rag* and a WebIngestService."""
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["web-ingest"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    def _require_capable() -> None:
        if not hasattr(rag, "ainsert"):
            raise HTTPException(status_code=503, detail="This server cannot ingest documents.")

    @router.post("/scrape", response_model=JobResponse, status_code=202,
                 dependencies=[Depends(combined_auth)],
                 summary="Crawl a website into the Context Graph")
    async def start_scrape(request: ScrapeRequest):
        _require_capable()
        params = {
            "seed_url": request.url,
            "max_pages": request.max_pages,
            "max_documents": request.max_documents,
            "max_depth": request.max_depth,
            "same_domain": request.same_domain,
            "respect_robots": request.respect_robots,
            "render_js": request.render_js,
            "analyze": request.analyze,
            "use_sitemap": request.use_sitemap,
            "min_interval": request.min_interval,
        }
        job = service.start(_ws(), params)
        logger.info(f"web-ingest job {job['job_id']} started for {request.url} (ws={_ws()})")
        return JobResponse(**job)

    @router.get("/scrape/{job_id}", response_model=JobResponse,
                dependencies=[Depends(combined_auth)],
                summary="Poll a web-ingest job")
    async def get_scrape(job_id: str):
        _require_capable()
        job = service.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"No web-ingest job '{job_id}'.")
        return JobResponse(**job)

    @router.get("/scrape", dependencies=[Depends(combined_auth)],
                summary="List web-ingest jobs")
    async def list_scrape() -> List[Dict[str, Any]]:
        _require_capable()
        return service.list()

    logger.info("Web-ingest API routes registered")
    return router
