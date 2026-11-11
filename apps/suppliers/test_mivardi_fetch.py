from unittest import mock

from django.test import SimpleTestCase

from apps.suppliers.adapters import mivardi
from apps.suppliers.adapters.base import AdapterError

FULL = b'<?xml version="1.0"?>\n<SHOP><SHOPITEM></SHOPITEM></SHOP>\n'
TRUNCATED = b'<?xml version="1.0"?>\n<SHOP><SHOPITEM>'


def _adapter():
    feed = mock.Mock(file=None, url="https://example.test/feed", config={"fetch_attempts": 3})
    return mivardi.MivardiAdapter(feed)


def _response(content):
    return mock.Mock(content=content, raise_for_status=mock.Mock())


@mock.patch.object(mivardi.time, "sleep")
class MivardiFetchTests(SimpleTestCase):
    def test_truncated_feed_retried(self, _sleep):
        with mock.patch("requests.Session.get", side_effect=[_response(TRUNCATED), _response(FULL)]):
            self.assertEqual(_adapter().fetch(), FULL)

    def test_truncated_feed_fails_after_attempts(self, _sleep):
        with mock.patch("requests.Session.get", return_value=_response(TRUNCATED)) as get:
            with self.assertRaises(AdapterError):
                _adapter().fetch()
        self.assertEqual(get.call_count, 3)
