"""Unit tests for bio_toolkit.clients.ern. HTTP mocked, no network."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from bio_toolkit.clients.ern import (
    ERNClient,
    ERN_VIDEO_IDS,
    extract_title,
    sanitize_filename,
)


# --- pure helpers ---

def test_extract_title_from_frontmatter():
    md = '---\ntitle: "ERN Genetics 101"\n---\n\n# Heading\nbody'
    assert extract_title(md) == "ERN Genetics 101"


def test_extract_title_falls_back_to_heading():
    md = "no frontmatter here\n\n# Real Heading\nbody"
    assert extract_title(md) == "Real Heading"


def test_extract_title_none_when_absent():
    assert extract_title("plain text, no title") is None


def test_sanitize_filename_strips_illegal_chars():
    # each of / : * ? " becomes a dash
    assert sanitize_filename('a/b:c*?"d') == "a-b-c---d"


def test_sanitize_filename_truncates_long():
    out = sanitize_filename("x" * 200)
    assert len(out) <= 120


def test_video_id_list_present():
    assert "WJEjGYKDf3A" in ERN_VIDEO_IDS
    assert len(ERN_VIDEO_IDS) == 54


# --- fetch_video (HTTP mocked) ---

def _resp(text, status=200):
    r = MagicMock()
    r.text = text
    r.status_code = status
    r.raise_for_status = MagicMock()
    return r


def test_fetch_video_returns_parsed_record():
    client = ERNClient()
    md = '---\ntitle: "Coloboma lecture"\n---\n\ntranscript body'

    with patch.object(client.session, "get", return_value=_resp(md)) as mock_get:
        rec = client.fetch_video("WJEjGYKDf3A")

    assert rec["video_id"] == "WJEjGYKDf3A"
    assert rec["title"] == "Coloboma lecture"
    assert rec["markdown"] == md
    assert rec["source"] == "https://www.youtube.com/watch?v=WJEjGYKDf3A"
    # request hit the defuddle URL for this video
    called_url = mock_get.call_args[0][0]
    assert "defuddle.md" in called_url and "WJEjGYKDf3A" in called_url


def test_fetch_video_title_falls_back_to_id():
    client = ERNClient()
    with patch.object(client.session, "get", return_value=_resp("no title content")):
        rec = client.fetch_video("abc123")
    assert rec["title"] == "abc123"


def test_fetch_videos_captures_errors():
    client = ERNClient()

    def side_effect(url, **kwargs):
        if "good" in url:
            return _resp("# Good Talk\nbody")
        raise requests.exceptions.ConnectionError("down")

    # patch retry backoff sleep so the exhausted-retry path returns instantly
    with patch.object(client.session, "get", side_effect=side_effect), \
         patch("bio_toolkit.util.retry.time.sleep", return_value=None):
        recs = client.fetch_videos(["good", "bad"])

    assert recs[0]["title"] == "Good Talk"
    assert recs[0]["markdown"] is not None
    assert recs[1]["markdown"] is None
    assert "error" in recs[1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
