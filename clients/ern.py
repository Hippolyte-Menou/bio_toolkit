"""European Reference Network (ERN) course-video fetch client.

Ported from the vault's _assets/code/data/ern_fetch.py. That script pulled ERN
YouTube course videos' transcripts as markdown from the defuddle.md service and
wrote them to disk. Here the API-access layer is preserved and the client
RETURNS parsed data (raw markdown + extracted title) instead of writing files —
no vault rendering, no frontmatter assembly, no JSON index.

HTTP is via `requests`, wrapped with `retry_on_failure`. The original used
urllib; the request shape (URL template, User-Agent, Accept headers, timeout)
is preserved.

    from bio_toolkit.clients.ern import ERNClient

    client = ERNClient()
    record = client.fetch_video("WJEjGYKDf3A")
    record["title"]     # extracted from defuddle frontmatter / first heading
    record["markdown"]  # raw transcript markdown
"""

import re

import requests

from bio_toolkit.util.retry import retry_on_failure

DEFUDDLE_URL = "https://defuddle.md/www.youtube.com/watch?v={video_id}"

# Default User-Agent / Accept headers preserved from the vault script.
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,text/markdown,*/*",
}

# The ERN course-video ID list shipped with the vault script (batches 1 + 2).
ERN_VIDEO_IDS = [
    # Batch 1
    "WJEjGYKDf3A", "QA3Uv7aIJlo", "--cA5ZcRdyg", "2BCnH3Jt3pk", "SMKrPg2tKPg",
    "nei6i89YFOU", "_uUsNhcHwSE", "h3aKFbpcbmY", "JTK8vUT6AR4", "ImZZUQjl8fs",
    "m2NcqNVX7fA", "G3VrpYO2jK4", "f9IuIjKqX8U", "PKUJqpfrK4o", "kNAkoa_a8mM",
    "6DovmSEjy4E", "hGCqd0wSfoQ", "uQPdnedM4es", "-WzT29N4uMo", "jYwWAfcwM2I",
    "xNKFF3Zmvec", "zph4pk8c58s", "BJ8xOSXXOUY", "LdmUeJ_QANw", "32BGxmGz1e4",
    # Batch 2
    "aamDDJBd3g0", "vcjj75Ymh-0", "sKsgvH5bcsw", "ERtz902RB90", "6vuE-tr-TTU",
    "XtG1hXFoHZU", "qQ04iYjtKco", "OxtXXbnkV_I", "CLuR48VbB-E", "3D38_7-f898",
    "c42T8f7BaDY", "Jrwk8Xol5vg", "LthIcyET0Pk", "BU6sPJOp1k4", "pctaTUWkxfM",
    "aQcA9g7E51o", "sOye5rp2qOQ", "kH_f1LuDeLA", "YpUeLaN-QPo", "v0MBuBTWGUA",
    "nPlzVzmoihY", "DEIXVX8wnO8", "xYOl_jP96ZE", "MYurkqoFnDI", "H0RujGxV4hY",
    "c3ZiUK4TAHc", "X9vwPctSR9k", "_RJ1KgBbwE0", "rUGY-BXtTMo",
]


def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename (ported verbatim)."""
    name = re.sub(r'[<>:"/\\|?*]', '-', name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = name.rstrip('. ')
    if len(name) > 120:
        name = name[:120].rstrip()
    return name


def extract_title(md_content: str) -> str | None:
    """Extract title from defuddle markdown (YAML frontmatter or first heading)."""
    # First try YAML frontmatter title
    m = re.search(r'^title:\s*"(.+?)"', md_content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # Fallback: first # heading
    for line in md_content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None


class ERNClient:
    """Client for fetching ERN course-video transcripts via defuddle.md."""

    def __init__(self, headers: dict | None = None, timeout: int = 60):
        self.session = requests.Session()
        self.headers = headers or dict(DEFAULT_HEADERS)
        self.timeout = timeout

    @retry_on_failure(max_retries=3, base_delay=1.0)
    def _get(self, url: str) -> requests.Response:
        resp = self.session.get(url, headers=self.headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def fetch_markdown(self, video_id: str) -> str:
        """Fetch the raw defuddle markdown for one YouTube video id."""
        url = DEFUDDLE_URL.format(video_id=video_id)
        resp = self._get(url)
        return resp.text

    def fetch_video(self, video_id: str) -> dict:
        """Fetch one video and RETURN parsed data.

        Returns a dict:
            {"video_id", "title", "markdown", "source"}
        where title falls back to the video id when defuddle has no usable
        title (matching the original's generic-title handling).
        """
        markdown = self.fetch_markdown(video_id)
        title = extract_title(markdown) or video_id
        return {
            "video_id": video_id,
            "title": title,
            "markdown": markdown,
            "source": f"https://www.youtube.com/watch?v={video_id}",
        }

    def fetch_videos(self, video_ids: list[str] | None = None) -> list[dict]:
        """Fetch a list of videos (defaults to the shipped ERN_VIDEO_IDS).

        Returns a list of per-video records. Failed fetches are captured as
        records with markdown=None and an "error" key, so the caller gets the
        full picture without an exception aborting the batch.
        """
        ids = video_ids if video_ids is not None else ERN_VIDEO_IDS
        records: list[dict] = []
        for vid in ids:
            try:
                records.append(self.fetch_video(vid))
            except requests.exceptions.RequestException as e:
                records.append({
                    "video_id": vid,
                    "title": None,
                    "markdown": None,
                    "source": f"https://www.youtube.com/watch?v={vid}",
                    "error": str(e),
                })
        return records
