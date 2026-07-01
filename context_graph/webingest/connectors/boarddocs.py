"""BoardDocs (Diligent Community / eBOARDsolutions) connector.

BoardDocs is the other dominant K-12 board-governance platform (agendas, minutes,
policies) — it complements Finalsite for the RFP domain. Its public portal
(``go.boarddocs.com/<state>/<district>/Board.nsf/Public``) is a Lotus Domino app
that loads everything through ``BD-*`` AJAX endpoints, so the agenda/policy PDFs
never appear as ``<a href>`` links — same problem Finalsite has, different API.

Documented public flow (BoardDocs "Pro"):

    POST .../Board.nsf/BD-GetMeetingsList   body: current_committee_id=<CID>
        -> JSON list of meetings, each with a "unique" id
    POST .../Board.nsf/BD-GetAgenda         body: id=<meeting_unique>&current_committee_id=<CID>
        -> agenda HTML/JSON; attachment files are Domino URLs of the form
           .../Board.nsf/files/<docid>/$file/<name>.pdf

The connector is platform-generic — the committee id and base are read from what
the page actually sent; nothing about any district is hardcoded.

NOTE: built to the *documented* BoardDocs API. Unlike the Finalsite connector it
could not be live-validated from this host (BoardDocs' CloudFront WAF blocks
datacenter IPs), so field/endpoint shapes are handled defensively and this needs
a validation run from an unblocked network.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional
from urllib.parse import urlsplit

from lightrag.utils import logger

from context_graph.webingest.connectors.base import Connector, download_as_data
from context_graph.webingest.fetch import FetchResult

# Domino attachment links: .../Board.nsf/files/<docid>/$file/<name>
_FILE_RE = re.compile(r'([^"\'<>\s]*?/Board\.nsf/files/[^"\'<>\s]+?/\$[Ff]ile/[^"\'<>\s]+)',
                      re.IGNORECASE)
# committee id embedded in the page's JS (a few observed shapes)
_CID_RE = re.compile(
    r'(?:current_committee_id|committee_id|committee)["\']?\s*[:=]\s*["\']([A-Za-z0-9]{4,})["\']',
    re.IGNORECASE)
_MEETING_UNIQUE_KEYS = ("unique", "Unique", "id", "meeting_unique")


class BoardDocsConnector(Connector):
    name = "boarddocs"
    description = (
        "BoardDocs (Diligent Community / eBOARDsolutions) K-12 board portal on "
        "go.boarddocs.com/<st>/<dist>/Board.nsf. Signs: host contains boarddocs.com, "
        "requests to Board.nsf/BD-GetMeetingsList or other BD-* AJAX endpoints. "
        "Pulls agenda/policy PDFs from Domino /$file/ attachments."
    )

    def __init__(self, *, max_files: int = 500, max_meetings: int = 60) -> None:
        self._max_files = max_files
        self._max_meetings = max_meetings

    def detect(self, *, requests: List[Any], responses: List[Any],
               page_url: str, html: str) -> Optional[dict]:
        base = None
        cid = None
        # 1) prefer a captured BD- request: gives us the base + committee id verbatim
        for req in requests:
            url = getattr(req, "url", "") or ""
            if "/Board.nsf/BD-" in url:
                base = url.split("/Board.nsf/")[0] + "/Board.nsf"
                body = getattr(req, "post_data", None) or ""
                m = re.search(r'current_committee_id=([A-Za-z0-9]+)', body)
                if m:
                    cid = m.group(1)
                break
        # 2) fall back to the page URL (…/Board.nsf/Public) + committee id from the JS
        if base is None:
            if "boarddocs.com" not in (page_url or "") or "/Board.nsf" not in (page_url or ""):
                return None
            base = page_url.split("/Board.nsf")[0] + "/Board.nsf"
        if cid is None:
            m = _CID_RE.search(html or "")
            cid = m.group(1) if m else None
        if cid is None:
            return None
        return {"base": base, "cid": cid}

    async def resolve(self, request_context: Any, template: dict,
                      *, max_files: Optional[int] = None) -> List[FetchResult]:
        cap = max_files if max_files is not None else self._max_files
        base, cid = template["base"], template["cid"]
        host = "{0.scheme}://{0.netloc}".format(urlsplit(base))
        form = {"content-type": "application/x-www-form-urlencoded"}

        async def _post(endpoint: str, body: str) -> Any:
            resp = await request_context.post(f"{base}/{endpoint}", data=body, headers=form)
            if resp.status >= 400:
                return None
            text = await resp.text()
            try:
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return text            # some BD- endpoints return HTML, not JSON

        meetings = await _post("BD-GetMeetingsList", f"current_committee_id={cid}")
        uniques = self._meeting_uniques(meetings)
        logger.info(f"boarddocs: {len(uniques)} meeting(s) for committee {cid}")

        files: List[FetchResult] = []
        seen: set = set()
        for unique in uniques[: self._max_meetings]:
            if len(files) >= cap:
                break
            agenda = await _post("BD-GetAgenda", f"id={unique}&current_committee_id={cid}")
            for link in self._file_links(agenda, host):
                if len(files) >= cap:
                    break
                if link in seen:
                    continue
                seen.add(link)
                fr = await download_as_data(request_context, link,
                                            filename=link.rsplit("/", 1)[-1])
                if fr is not None:
                    files.append(fr)
        logger.info(f"boarddocs connector: {len(files)} file(s)")
        return files

    @staticmethod
    def _meeting_uniques(meetings: Any) -> List[str]:
        if isinstance(meetings, str):
            # HTML fallback: pull unique ids out of the markup
            return list(dict.fromkeys(re.findall(r'unique=["\']?([A-Za-z0-9]{6,})', meetings)))
        out: List[str] = []
        if isinstance(meetings, list):
            for m in meetings:
                if isinstance(m, dict):
                    for k in _MEETING_UNIQUE_KEYS:
                        if m.get(k):
                            out.append(str(m[k]))
                            break
        return list(dict.fromkeys(out))

    @staticmethod
    def _file_links(agenda: Any, host: str) -> List[str]:
        text = agenda if isinstance(agenda, str) else json.dumps(agenda)
        links = []
        for raw in _FILE_RE.findall(text or ""):
            raw = raw.replace("\\/", "/")
            links.append(raw if raw.startswith("http") else host + ("" if raw.startswith("/") else "/") + raw)
        return list(dict.fromkeys(links))
