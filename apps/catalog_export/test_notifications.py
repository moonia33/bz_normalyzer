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
