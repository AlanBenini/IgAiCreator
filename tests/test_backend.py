"""Test del backend MOMENT.

Esecuzione:
    python -m unittest discover -s tests -t . -v

I test non richiedono rete, credenziali o Supabase: usano i post demo (USE_MOCK)
e cartelle temporanee al posto di storage/.
"""

import asyncio
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from backend import config, main
from backend.adapters import google_calendar
from backend.adapters.mock_data import MOCK_POSTS_DATA
from backend.models import AssetRecord
from backend.services import analytics, assets, content, copywriter


def _upload(filename: str, payload: bytes = b"immagine", mime_type: str = "image/jpeg") -> UploadFile:
    return UploadFile(file=io.BytesIO(payload), filename=filename, headers={"content-type": mime_type})


def _asset(asset_id: str = "a1", filename: str = "foto.jpg") -> AssetRecord:
    return AssetRecord(id=asset_id, filename=filename, mime_type="image/jpeg", size=8, url=f"/storage/media/{asset_id}.jpg")


class _FakeRequest:
    """Request minima: al backend serve solo l'header Authorization."""

    def __init__(self, authorization: str = ""):
        self.headers = {"authorization": authorization}


class TestMockDataset(unittest.TestCase):
    """I post demo restano la sorgente dati obbligatoria finché non colleghiamo Meta."""

    def test_demo_posts_are_complete(self):
        self.assertEqual(len(MOCK_POSTS_DATA), 3)
        for post in MOCK_POSTS_DATA:
            self.assertIn("insights", post)
            self.assertIn("media_url", post)
            self.assertIn("caption", post)

    @unittest.skipUnless(config.USE_MOCK, "USE_MOCK disattivato: nessun dato demo in uso")
    def test_mock_mode_never_calls_the_graph_api(self):
        calls = []
        original = analytics.fetch_graph_posts
        analytics.fetch_graph_posts = lambda *args, **kwargs: calls.append(args) or []
        try:
            summary = analytics.calculate_analytics(user_id=None)
        finally:
            analytics.fetch_graph_posts = original
        self.assertEqual(calls, [])
        self.assertEqual(summary.total_posts, len(MOCK_POSTS_DATA))


@unittest.skipUnless(config.USE_MOCK, "richiede i post demo")
class TestAnalytics(unittest.TestCase):
    def test_summary_is_computed_from_demo_posts(self):
        summary = analytics.calculate_analytics(user_id=None)
        self.assertEqual(summary.total_posts, 3)
        self.assertEqual(summary.top_post_id, summary.posts[0].id)
        self.assertGreater(summary.avg_engagement_rate, 0)
        self.assertGreater(summary.avg_save_rate, 0)
        self.assertGreater(summary.avg_conversation_rate, 0)
        self.assertGreater(summary.total_reach, 0)

    def test_posts_are_sorted_by_engagement_rate(self):
        rates = [post.engagement_rate for post in analytics.calculate_analytics(user_id=None).posts]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_every_post_carries_a_diagnosis(self):
        for post in analytics.calculate_analytics(user_id=None).posts:
            self.assertIn("•", post.strengths + post.weaknesses)

    def test_empty_account_returns_an_empty_summary(self):
        original = analytics.MOCK_POSTS_DATA
        analytics.MOCK_POSTS_DATA = []
        try:
            summary = analytics.calculate_analytics(user_id=None)
        finally:
            analytics.MOCK_POSTS_DATA = original
        self.assertEqual(summary.total_posts, 0)
        self.assertEqual(summary.posts, [])
        self.assertEqual(summary.top_post_id, "")


class TestInsightsCache(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.original_fetch = analytics.fetch_graph_insights
        analytics.fetch_graph_insights = self._fake_fetch

    def tearDown(self):
        analytics.fetch_graph_insights = self.original_fetch

    def _fake_fetch(self, media_id, user_id=None):
        self.calls.append(media_id)
        return {"reach": 100, "saved": 10}

    def test_fresh_entry_avoids_a_second_graph_call(self):
        cache = {}
        first = analytics._insights_for("media-1", None, cache)
        second = analytics._insights_for("media-1", None, cache)
        self.assertEqual(first, second)
        self.assertEqual(self.calls, ["media-1"])

    def test_expired_entry_is_refreshed(self):
        cache = {"media-1": {"expires_at": 0, "insights": {"reach": 1}}}
        self.assertEqual(analytics._insights_for("media-1", None, cache), {"reach": 100, "saved": 10})
        self.assertEqual(self.calls, ["media-1"])

    def test_cache_round_trip_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = analytics.CACHE_FILE
            analytics.CACHE_FILE = Path(tmp) / "insights_cache.json"
            try:
                self.assertEqual(analytics._read_cache(), {})
                analytics._write_cache({"media-1": {"expires_at": 0, "insights": {}}})
                self.assertIn("media-1", analytics._read_cache())
            finally:
                analytics.CACHE_FILE = original


class TestCopyParser(unittest.TestCase):
    """Il parser deve sopravvivere al JSON sporco che a volte restituiscono gli LLM."""

    def test_parses_fenced_json(self):
        payload = '```json\n{"copies": [{"hook": "H", "caption": "C", "hashtags": ["#a"]}]}\n```'
        items = copywriter._parse_json(payload, [_asset()])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].number, 1)
        self.assertEqual(items[0].hook, "H")
        self.assertEqual(items[0].hashtags, ["#a"])

    def test_repairs_unquoted_hashtags(self):
        payload = '{"copies": [{"hook": "H", "caption": "C", "hashtags": [#a", #b"]}]}'
        self.assertEqual(copywriter._parse_json(payload, [_asset()])[0].hashtags, ["#a", "#b"])

    def test_repairs_trailing_commas(self):
        payload = '{"copies": [{"hook": "H", "caption": "C",},]}'
        self.assertEqual(copywriter._parse_json(payload, [_asset()])[0].caption, "C")

    def test_never_returns_more_copies_than_assets(self):
        payload = '{"copies": [{"hook": "1"}, {"hook": "2"}, {"hook": "3"}]}'
        self.assertEqual(len(copywriter._parse_json(payload, [_asset(), _asset("a2")])), 2)


class TestRecurringValidation(unittest.TestCase):
    """Le validazioni scattano prima di qualunque chiamata a Google."""

    def _schedule(self, frequency="weekly", days=None, weeks=1):
        return google_calendar.create_recurring_event("user-1", "Serie", "", "2026-09-14T09:00:00", "2026-09-14T10:00:00", frequency, ["MO"] if days is None else days, weeks)

    def test_rejects_unknown_frequency(self):
        with self.assertRaises(RuntimeError):
            self._schedule(frequency="daily")

    def test_rejects_unknown_weekday_code(self):
        with self.assertRaises(RuntimeError):
            self._schedule(days=["XX"])

    def test_rejects_empty_day_selection(self):
        with self.assertRaises(RuntimeError):
            self._schedule(days=[])

    def test_rejects_weeks_out_of_range(self):
        for weeks in (0, 53):
            with self.assertRaises(RuntimeError):
                self._schedule(weeks=weeks)


class TestGoogleTokenErrors(unittest.TestCase):
    def test_refresh_failure_reports_google_reason(self):
        class _FakeResponse:
            status_code = 400
            text = '{"error": "invalid_grant"}'

            def json(self):
                return {"error": "invalid_grant"}

        with mock.patch.object(google_calendar.requests, "post", return_value=_FakeResponse()):
            with self.assertRaises(RuntimeError) as ctx:
                google_calendar._refresh_token("refresh-token")
        self.assertIn("invalid_grant", str(ctx.exception))

    def test_expired_connection_without_refresh_token_is_reported(self):
        class _FakeResponse:
            status_code = 200

            def json(self):
                return [{"access_token": "token-vecchio", "refresh_token": "", "expires_at": 0}]

            def raise_for_status(self):
                return None

        with mock.patch.object(google_calendar.requests, "get", return_value=_FakeResponse()):
            with self.assertRaises(RuntimeError) as ctx:
                google_calendar._access_token("user-1")
        self.assertIn("scaduto", str(ctx.exception))


class TestContentLibrary(unittest.TestCase):
    """La coda bozze usa storage/exports: nei test lavora su una cartella temporanea."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = content.CONTENT_FILE
        content.CONTENT_FILE = Path(self.tmp.name) / "content_library.json"

    def tearDown(self):
        content.CONTENT_FILE = self.original
        self.tmp.cleanup()

    def test_draft_lifecycle(self):
        draft = content.create_draft("Lancio", "instagram_post", ["a1"], [copywriter.CopyItem(number=1, hook="H", caption="C", hashtags=["#a"])])
        self.assertEqual(draft.status, "DRAFT")
        self.assertEqual(len(content.list_drafts()), 1)
        self.assertEqual(content.update_status(draft.id, "APPROVED").status, "APPROVED")

    def test_unknown_content_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            content.update_status("inesistente", "APPROVED")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_invalid_status_is_rejected(self):
        draft = content.create_draft("Bozza", "instagram_post", [], [])
        with self.assertRaises(HTTPException) as ctx:
            content.update_status(draft.id, "ARCHIVIATO")
        self.assertEqual(ctx.exception.status_code, 400)


class TestAssetUploads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_dir = assets.MEDIA_DIR
        self.original_manifest = assets.MANIFEST_PATH
        assets.MEDIA_DIR = Path(self.tmp.name)
        assets.MANIFEST_PATH = Path(self.tmp.name) / "uploaded_assets.json"

    def tearDown(self):
        assets.MEDIA_DIR = self.original_dir
        assets.MANIFEST_PATH = self.original_manifest
        self.tmp.cleanup()

    def test_saves_a_supported_image(self):
        records = asyncio.run(assets.save_uploads([_upload("foto.jpg")]))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].filename, "foto.jpg")
        self.assertTrue(records[0].url.startswith("/storage/media/"))
        self.assertEqual(len(assets.list_assets()), 1)
        self.assertEqual(assets.asset_public_url(records[0].id), records[0].url)

    def test_rejects_unsupported_format(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(assets.save_uploads([_upload("contratto.pdf", mime_type="application/pdf")]))
        self.assertEqual(ctx.exception.status_code, 415)

    def test_rejects_too_many_files(self):
        files = [_upload(f"foto-{index}.jpg") for index in range(config.MAX_UPLOADS + 1)]
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(assets.save_uploads(files))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_empty_file(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(assets.save_uploads([_upload("vuota.jpg", payload=b"")]))
        self.assertEqual(ctx.exception.status_code, 413)


class TestOauthState(unittest.TestCase):
    """Lo state OAuth è firmato: serve a non fidarsi del user_id che torna da Meta."""

    def setUp(self):
        self.original = main.SESSION_SECRET
        main.SESSION_SECRET = "segreto-di-test"

    def tearDown(self):
        main.SESSION_SECRET = self.original

    def test_round_trip_returns_the_user(self):
        self.assertEqual(main._decode_state(main._encode_state("user-1")), "user-1")

    def test_tampered_signature_is_rejected(self):
        body, signature = main._encode_state("user-1").split(".", 1)
        with self.assertRaises(HTTPException) as ctx:
            main._decode_state(f"{body}.{'0' * len(signature)}")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_missing_session_secret_blocks_the_flow(self):
        main.SESSION_SECRET = ""
        with self.assertRaises(HTTPException) as ctx:
            main._encode_state("user-1")
        self.assertEqual(ctx.exception.status_code, 503)


class TestAuthGate(unittest.TestCase):
    """Senza Supabase il workspace resta aperto in locale; con Supabase il token è obbligatorio."""

    def test_anonymous_access_allowed_when_supabase_is_missing(self):
        with mock.patch.object(main, "AUTH_REQUIRED", False):
            self.assertIsNone(main.require_user(_FakeRequest()))

    def test_missing_token_is_rejected_when_supabase_is_configured(self):
        with mock.patch.object(main, "AUTH_REQUIRED", True):
            with self.assertRaises(HTTPException) as ctx:
                main.require_user(_FakeRequest())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_calendar_endpoints_always_require_a_user(self):
        with mock.patch.object(main, "AUTH_REQUIRED", False):
            with self.assertRaises(HTTPException) as ctx:
                main.require_calendar_user(_FakeRequest())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_static_user_facing_routes_are_registered(self):
        paths = {route.path for route in main.app.routes}
        for path in ("/api/content", "/api/generate-copy", "/api/assets", "/api/calendar/events/recurring"):
            self.assertIn(path, paths)


if __name__ == "__main__":
    unittest.main()


