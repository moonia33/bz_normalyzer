from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalog_export import notifications
from apps.catalog_export.models import PipelineRun
from apps.suppliers.models import Supplier, SupplierSchedule

Status = PipelineRun.Status


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PIPELINE_ALERT_EMAILS=["admin@example.com"],
    PIPELINE_SUMMARY_HOUR=7,
)
class NotificationTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(code="moss", name="Moss")
        SupplierSchedule.objects.create(
            supplier=self.supplier, enabled=True, catalog_weekday=None,
            price_interval_hours=24, stock_interval_minutes=60,
            last_prices_at=timezone.now(), last_stock_at=timezone.now(),
        )

    def _run(self, stage, status, **kw):
        return PipelineRun.objects.create(supplier=self.supplier, stage=stage, status=status, **kw)

    def test_failed_run_alert_sent_once(self):
        run = self._run("stock", Status.FAILED, error_message="shop down")
        notifications.alert_run(run)
        notifications.alert_run(run)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("KLAIDA", mail.outbox[0].subject)
        self.assertIn("shop down", mail.outbox[0].body)

    def test_held_prices_alert(self):
        run = self._run("prices", Status.DONE, details={"prices": {"held": 3, "held_sample": ["A"]}})
        notifications.alert_run(run)
        self.assertIn("Sulaikytos kainos", mail.outbox[0].subject)

    def test_no_recipients_no_mail(self):
        with override_settings(PIPELINE_ALERT_EMAILS=[]):
            notifications.alert_run(self._run("stock", Status.FAILED))
        self.assertEqual(len(mail.outbox), 0)

    def test_stale_running_marked_failed(self):
        run = self._run("stock", Status.RUNNING)
        PipelineRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(hours=7))
        notifications.check_stuck(timezone.now())
        run.refresh_from_db()
        self.assertEqual(run.status, Status.FAILED)
        self.assertIn("Nutrūko", run.error_message)
        self.assertEqual(len(mail.outbox), 1)

    def test_missed_stage_alert(self):
        SupplierSchedule.objects.filter(supplier=self.supplier).update(
            last_stock_at=timezone.now() - timedelta(hours=3)
        )
        notifications.check_missed(timezone.now())
        notifications.check_missed(timezone.now())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Nepasileido", mail.outbox[0].subject)

    def test_summary_expected_vs_actual(self):
        now = timezone.now()
        for _ in range(20):
            self._run("stock", Status.DONE, details={"stock": {"changed": 2}})
        self._run("stock", Status.FAILED, error_message="timeout")
        subject, text, html = notifications.build_summary(now + timedelta(seconds=1))
        self.assertIn("problemos", subject)
        self.assertIn("turėjo  24", text)
        self.assertIn("likučių pokyčiai: 40", text)
        self.assertIn("nepasileido 3", text)
        self.assertIn("timeout", html)

    def test_summary_sent_once_per_day_after_hour(self):
        local = timezone.localtime().replace(hour=8, minute=0)
        self.assertTrue(notifications.send_daily_summary(local))
        self.assertFalse(notifications.send_daily_summary(local + timedelta(minutes=15)))
        self.assertFalse(notifications.send_daily_summary(local.replace(hour=6)))

    def test_stuck_alert_without_marking_when_locked(self):
        run = self._run("catalog", Status.RUNNING)
        PipelineRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(hours=7))
        notifications.tick(timezone.now(), mark_stale=False)
        run.refresh_from_db()
        self.assertEqual(run.status, Status.RUNNING)
        self.assertIn("Užstrigo", mail.outbox[0].subject)

    def test_failed_delivery_retried_next_tick(self):
        from unittest import mock

        run = self._run("stock", Status.FAILED, error_message="x", finished_at=timezone.now())
        with mock.patch.object(notifications, "send_mail", side_effect=OSError("smtp down")):
            with self.assertRaises(OSError):
                notifications.alert_run(run)
        notifications.retry_recent_alerts(timezone.now())
        self.assertEqual(len(mail.outbox), 1)


class NightMissedTests(TestCase):
    def setUp(self):
        from zoneinfo import ZoneInfo
        self.tz = ZoneInfo("Europe/Vilnius")
        self.supplier = Supplier.objects.create(code="alb", name="Alb")
        self.schedule = SupplierSchedule.objects.create(
            supplier=self.supplier, enabled=True, catalog_weekday=None,
            price_interval_hours=48, stock_interval_minutes=60,
        )

    def test_every_second_night_not_missed_after_one(self):
        from datetime import datetime
        last = datetime(2026, 11, 11, 2, 0, tzinfo=self.tz)
        self.assertFalse(notifications._missed(self.schedule, "prices", last, timedelta(hours=48),
                                               datetime(2026, 11, 12, 10, 0, tzinfo=self.tz)))
        self.assertTrue(notifications._missed(self.schedule, "prices", last, timedelta(hours=48),
                                              datetime(2026, 11, 13, 10, 0, tzinfo=self.tz)))
        self.assertFalse(notifications._missed(self.schedule, "prices", last, timedelta(hours=48),
                                               datetime(2026, 11, 13, 5, 0, tzinfo=self.tz)))

    def test_expected_sub_daily_prices_only_night_window(self):
        from datetime import datetime
        self.schedule.price_interval_hours = 1
        self.schedule.catalog_hour = 0
        since = datetime(2026, 11, 11, 7, 0, tzinfo=self.tz)
        expected = notifications._expected(self.schedule, since, since + timedelta(days=1))
        self.assertEqual(expected["prices"], 7)
