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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from backend import config, main
from backend.adapters import google_calendar
from backend.adapters.mock_data import MOCK_POSTS_DATA
from backend.models import AssetRecord, EditorialPlanRequest
from backend.services import analytics, assets, content, copywriter, editorial, publications


def _upload(filename: str, payload: bytes = b"immagine", mime_type: str = "image/jpeg") -> UploadFile:
    return UploadFile(file=io.BytesIO(payload), filename=filename, headers={"content-type": mime_type})


def _asset(asset_id: str = "a1", filename: str = "foto.jpg") -> AssetRecord:
    return AssetRecord(id=asset_id, filename=filename, mime_type="image/jpeg", size=8, url=f"/storage/media/{asset_id}.jpg")


# Lunedì 21 settembre 2026: settimana di riferimento per i test sulle ricorrenze.
MONDAY = datetime(2026, 9, 21, 9, 0)


def _calendar_event(summary: str, moment: datetime, hours: float = 1.0, event_id: str = "e1") -> dict:
    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": moment.isoformat()},
        "end": {"dateTime": (moment + timedelta(hours=hours)).isoformat()},
    }


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


class TestMeetingSuggestions(unittest.TestCase):
    """Il promemoria scatta con 3 riunioni nello stesso giorno in 3 settimane diverse."""

    def _weeks(self, count: int, hour_offset: int = 0, minutes: int = 0, summary: str = "Riunione editoriale"):
        return [
            _calendar_event(summary, MONDAY + timedelta(weeks=index, hours=hour_offset, minutes=minutes), event_id=f"e{index}")
            for index in range(count)
        ]

    def test_three_weeks_trigger_a_suggestion(self):
        suggestions = editorial.detect_recurring_meetings(self._weeks(3))
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].weekday, "MO")
        self.assertEqual(suggestions[0].hour, 9)
        self.assertEqual(suggestions[0].occurrences, 3)
        self.assertEqual(len(suggestions[0].weeks), 3)
        self.assertIn("piano editoriale", suggestions[0].message)

    def test_two_weeks_are_not_enough(self):
        self.assertEqual(editorial.detect_recurring_meetings(self._weeks(2)), [])

    def test_adjacent_hours_are_merged_before_counting(self):
        """Due lunedì alle 09:00 più un lunedì alle 10:00 sono la stessa abitudine."""
        events = self._weeks(2) + [_calendar_event("Riunione editoriale", MONDAY + timedelta(weeks=2, hours=1), event_id="e2")]
        suggestions = editorial.detect_recurring_meetings(events)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].occurrences, 3)
        self.assertEqual(suggestions[0].hour, 9)

    def test_duplicate_events_on_the_same_day_count_once(self):
        events = self._weeks(3) + [_calendar_event("Duplicato", MONDAY, event_id="dup")]
        self.assertEqual(editorial.detect_recurring_meetings(events)[0].occurrences, 3)

    def test_all_day_events_are_ignored(self):
        events = [
            {"id": f"d{index}", "summary": "Fiera", "start": {"date": f"2026-09-{21 + index * 7}"}, "end": {"date": f"2026-09-{22 + index * 7}"}}
            for index in range(3)
        ]
        self.assertEqual(editorial.detect_recurring_meetings(events), [])

    def test_different_weekdays_are_kept_separate(self):
        events = self._weeks(3) + [
            _calendar_event("Standup", MONDAY + timedelta(weeks=index, days=1), event_id=f"t{index}")
            for index in range(3)
        ]
        suggestions = editorial.detect_recurring_meetings(events)
        self.assertEqual([item.weekday for item in suggestions], ["MO", "TU"])


class TestEditorialPlanCreation(unittest.TestCase):
    """Un piano crea due serie su Google Calendar e resta elencabile in locale."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_file = editorial.PLANS_FILE
        editorial.PLANS_FILE = Path(self.tmp.name) / "editorial_plans.json"
        self.calls = []

        def fake_series(user_id, title, description, start, end, frequency, days, weeks):
            self.calls.append({"user_id": user_id, "title": title, "description": description, "start": start, "end": end, "frequency": frequency, "days": list(days), "weeks": weeks})
            return {"id": f"ev{len(self.calls)}", "htmlLink": f"https://calendar.google.com/ev{len(self.calls)}"}

        self.series = mock.patch.object(editorial, "create_recurring_event", fake_series)
        self.series.start()

    def tearDown(self):
        self.series.stop()
        editorial.PLANS_FILE = self.original_file
        self.tmp.cleanup()

    def _request(self, **overrides):
        payload = {
            "topic": "Dietro le quinte stabilimento",
            "start_date": "2026-09-23",
            "weeks": 3,
            "post_days": ["MO", "WE"],
            "post_time": "10:00",
            "meeting_shift_days": -1,
            "meeting_time": "09:00",
        }
        payload.update(overrides)
        return EditorialPlanRequest(**payload)

    def test_creates_a_post_series_and_a_meeting_series(self):
        editorial.create_plan(self._request(), "user-1")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["title"], "Post · Dietro le quinte stabilimento")
        self.assertEqual(self.calls[1]["title"], "Riunione · Dietro le quinte stabilimento")
        self.assertIn("Materiale da raccogliere", self.calls[0]["description"])
        self.assertIn("Riunione legata al post: Dietro le quinte stabilimento", self.calls[1]["description"])

    def test_start_date_is_snapped_to_the_monday_of_that_week(self):
        plan = editorial.create_plan(self._request(), "user-1")
        self.assertEqual(plan.start_date, "2026-09-21")
        self.assertEqual(self.calls[0]["start"], "2026-09-21T10:00:00")

    def test_meeting_series_is_anchored_the_day_before_the_post(self):
        """Lunedì e mercoledì con spostamento -1: riunione la domenica prima e il martedì."""
        plan = editorial.create_plan(self._request(), "user-1")
        self.assertEqual(plan.meeting_days, ["TU", "SU"])
        self.assertEqual(self.calls[1]["start"], "2026-09-20T09:00:00")

    def test_counts_days_times_weeks(self):
        plan = editorial.create_plan(self._request(), "user-1")
        self.assertEqual(plan.posts_planned, 6)
        self.assertEqual(plan.meetings_planned, 6)

    def test_same_day_meetings_keep_the_post_order(self):
        plan = editorial.create_plan(self._request(meeting_shift_days=0), "user-1")
        self.assertEqual(plan.meeting_days, ["MO", "WE"])
        self.assertEqual(self.calls[1]["start"], "2026-09-21T09:00:00")

    def test_plan_is_stored_and_reloadable(self):
        created = editorial.create_plan(self._request(), "user-1")
        plans = editorial.list_plans()
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].id, created.id)
        self.assertEqual(plans[0].post_event_id, "ev1")

    def test_delete_removes_both_series_and_the_record(self):
        plan = editorial.create_plan(self._request(), "user-1")
        deleted = []
        with mock.patch.object(editorial, "delete_event", lambda user_id, event_id: deleted.append(event_id)):
            editorial.delete_plan(plan.id, "user-1")
        self.assertEqual(deleted, ["ev1", "ev2"])
        self.assertEqual(editorial.list_plans(), [])

    def test_unknown_plan_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            editorial.delete_plan("inesistente", "user-1")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_record_is_removed_even_if_google_refuses(self):
        plan = editorial.create_plan(self._request(), "user-1")

        def failing(user_id, event_id):
            raise RuntimeError("Google non raggiungibile")

        with mock.patch.object(editorial, "delete_event", failing):
            with self.assertRaises(RuntimeError) as ctx:
                editorial.delete_plan(plan.id, "user-1")
        self.assertIn("Google non raggiungibile", str(ctx.exception))
        self.assertEqual(editorial.list_plans(), [])


class TestEditorialPlanValidation(unittest.TestCase):
    def test_rejects_empty_topic(self):
        with self.assertRaises(HTTPException) as ctx:
            editorial.create_plan(EditorialPlanRequest(topic="  ", start_date="2026-09-21", post_days=["MO"]), "user-1")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_unknown_weekday(self):
        with self.assertRaises(HTTPException) as ctx:
            editorial._validate_days(["MO", "XX"], "i post")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("XX", ctx.exception.detail)

    def test_rejects_empty_day_selection(self):
        with self.assertRaises(HTTPException) as ctx:
            editorial._validate_days([], "i post")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_invalid_time(self):
        with self.assertRaises(HTTPException) as ctx:
            editorial._parse_time("25:99")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_invalid_date(self):
        with self.assertRaises(HTTPException) as ctx:
            editorial._parse_date("21 settembre")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_days_are_normalised_in_week_order(self):
        self.assertEqual(editorial._validate_days(["WE", "MO", "MO"], "i post"), ["MO", "WE"])

    def test_shift_wraps_around_the_week(self):
        self.assertEqual(editorial.shift_weekdays(["WE"], -1), ["TU"])
        self.assertEqual(editorial.shift_weekdays(["MO"], -1), ["SU"])
        self.assertEqual(editorial.shift_weekdays(["SU"], 1), ["MO"])
        self.assertEqual(editorial.shift_weekdays(["MO", "WE"], 0), ["MO", "WE"])


class TestPublicationQueue(unittest.TestCase):
    """La coda pubblica una sola volta le voci scadute e registra i motivi di errore."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_file = publications.PUBLICATIONS_FILE
        self.original_mock = publications.USE_MOCK
        publications.PUBLICATIONS_FILE = Path(self.tmp.name) / "publications.json"
        publications.USE_MOCK = True

    def tearDown(self):
        publications.PUBLICATIONS_FILE = self.original_file
        publications.USE_MOCK = self.original_mock
        self.tmp.cleanup()

    def _schedule(self, when: str = "2026-01-01T10:00:00+00:00", assets: list[str] | None = None):
        return publications.create_publication("Didascalia proposta", assets or ["a1"], when, user_id="user-1")

    def test_due_item_is_published_once(self):
        entry = self._schedule()
        result = publications.process_due()
        self.assertEqual((result.due, result.published, result.failed), (1, 1, 0))
        self.assertTrue(result.results[0].platform_id.startswith("mock_"))
        stored = publications.list_publications()[0]
        self.assertEqual(stored.status, "PUBLISHED")
        self.assertIsNotNone(stored.published_at)
        # Una voce pubblicata non viene più toccata.
        self.assertEqual(publications.process_due().due, 0)
        self.assertEqual(publications.list_publications()[0].id, entry.id)

    def test_future_item_stays_scheduled(self):
        self._schedule("2999-01-01T10:00:00+00:00")
        self.assertEqual(publications.process_due().due, 0)
        self.assertEqual(publications.list_publications()[0].status, "SCHEDULED")

    def test_failure_is_recorded_with_the_reason(self):
        self._schedule()

        def failing(entry):
            raise RuntimeError("PUBLIC_BASE_URL mancante")

        result = publications.process_due(publisher=failing)
        self.assertEqual((result.published, result.failed), (0, 1))
        stored = publications.list_publications()[0]
        self.assertEqual(stored.status, "FAILED")
        self.assertIn("PUBLIC_BASE_URL", stored.error)

    def test_real_publisher_reports_the_reason_from_meta(self):
        publications.USE_MOCK = False
        self._schedule()
        with mock.patch.object(publications, "asset_public_url", lambda asset_id: "/storage/media/a1.jpg"), mock.patch.object(publications, "publish_photo", side_effect=RuntimeError("Configura PUBLIC_BASE_URL")):
            result = publications.process_due()
        self.assertEqual(result.failed, 1)
        self.assertIn("PUBLIC_BASE_URL", publications.list_publications()[0].error)

    def test_missing_asset_is_reported_before_calling_meta(self):
        """Senza il file in storage la coda si ferma prima di contattare Instagram."""
        publications.USE_MOCK = False
        self._schedule()
        with mock.patch.object(publications, "publish_photo") as publisher:
            result = publications.process_due()
        self.assertEqual(result.failed, 1)
        self.assertIn("Asset non trovato", publications.list_publications()[0].error)
        publisher.assert_not_called()

    def test_one_failure_does_not_block_the_next(self):
        self._schedule()
        self._schedule()
        calls = []

        def sometimes(entry):
            calls.append(entry.id)
            if len(calls) == 1:
                raise RuntimeError("prima fallisce")
            return "platform-id"

        result = publications.process_due(publisher=sometimes)
        self.assertEqual((result.due, result.published, result.failed), (2, 1, 1))

    def test_cancelled_item_is_skipped(self):
        entry = self._schedule()
        self.assertEqual(publications.cancel_publication(entry.id).status, "CANCELLED")
        self.assertEqual(publications.process_due().due, 0)

    def test_published_item_cannot_be_cancelled(self):
        entry = self._schedule()
        publications.process_due()
        with self.assertRaises(HTTPException) as ctx:
            publications.cancel_publication(entry.id)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_unknown_publication_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            publications.cancel_publication("inesistente")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_images_are_required(self):
        with self.assertRaises(HTTPException) as ctx:
            publications.create_publication("Senza foto", [], "2026-01-01T10:00:00+00:00")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_invalid_schedule_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            publications.create_publication("x", ["a1"], "domani mattina")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_naive_schedule_is_read_as_utc(self):
        self.assertTrue(self._schedule("2026-01-01T10:00:00").scheduled_at.endswith("+00:00"))

    def test_link_to_the_calendar_event_is_kept(self):
        entry = publications.create_publication("x", ["a1"], "2026-01-01T10:00:00+00:00", calendar_event_id="ev1", calendar_event_title="Post · Tema")
        self.assertEqual(entry.calendar_event_id, "ev1")
        self.assertEqual(entry.calendar_event_title, "Post · Tema")

    def test_entries_are_filtered_by_user(self):
        self._schedule()
        self.assertEqual(len(publications.list_publications("user-1")), 1)
        # In locale le voci senza utente restano visibili: il workspace non ha login.
        publications.create_publication("Anonimo", ["a2"], "2026-01-01T10:00:00+00:00")
        self.assertEqual(len(publications.list_publications("user-1")), 2)


class TestSchedulerGate(unittest.TestCase):
    """Il processore delle pubblicazioni pubblica davvero: non resta mai aperto."""

    def test_local_development_without_supabase_stays_open(self):
        with mock.patch.object(main, "CRON_SECRET", ""), mock.patch.object(main, "AUTH_REQUIRED", False):
            self.assertIsNone(main._require_scheduler(_FakeRequest()))

    def test_production_without_secret_is_closed(self):
        with mock.patch.object(main, "CRON_SECRET", ""), mock.patch.object(main, "AUTH_REQUIRED", True):
            with self.assertRaises(HTTPException) as ctx:
                main._require_scheduler(_FakeRequest())
        self.assertEqual(ctx.exception.status_code, 503)

    def test_wrong_secret_is_rejected(self):
        with mock.patch.object(main, "CRON_SECRET", "s3gr3t0"):
            with self.assertRaises(HTTPException) as ctx:
                main._require_scheduler(_FakeRequest("Bearer sbagliato"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_shared_secret_is_accepted(self):
        with mock.patch.object(main, "CRON_SECRET", "s3gr3t0"):
            self.assertIsNone(main._require_scheduler(_FakeRequest("Bearer s3gr3t0")))


class TestNewRoutes(unittest.TestCase):
    def test_editorial_and_publication_routes_are_registered(self):
        paths = {route.path for route in main.app.routes}
        for path in (
            "/api/editorial/suggestions",
            "/api/editorial/plans",
            "/api/editorial/plans/{plan_id}",
            "/api/publications",
            "/api/publications/{publication_id}",
            "/api/publications/run",
        ):
            self.assertIn(path, paths)

    def test_config_declares_storage_and_scheduler_state(self):
        payload = main.frontend_config()
        self.assertIn("storage_ephemeral", payload)
        self.assertIn("scheduler_configured", payload)
        self.assertNotIn("session_secret", {key.lower() for key in payload})


if __name__ == "__main__":
    unittest.main()


