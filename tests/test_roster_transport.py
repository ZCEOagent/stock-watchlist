import unittest
from unittest.mock import MagicMock, patch
import requests
from fetch_tw_universe import fetch_roster_rows


class RosterTransportTests(unittest.TestCase):
    def response(self, rows):
        response = MagicMock()
        response.__enter__.return_value = response
        response.json.return_value = rows
        return response

    @patch('fetch_tw_universe.time.sleep')
    @patch('fetch_tw_universe.requests.get')
    def test_truncated_body_recovers_with_full_new_request(self, get, sleep):
        response = self.response([{'company': 'complete'}])
        get.side_effect = [requests.exceptions.ChunkedEncodingError('Response ended prematurely'), response]
        self.assertEqual(fetch_roster_rows('https://example.test'), [{'company': 'complete'}])
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(1)
        response.__exit__.assert_called_once()

    @patch('fetch_tw_universe.time.sleep')
    @patch('fetch_tw_universe.requests.get')
    def test_retry_budget_exhaustion_still_fails(self, get, sleep):
        get.side_effect = requests.exceptions.ChunkedEncodingError('truncated')
        with self.assertRaises(requests.exceptions.ChunkedEncodingError):
            fetch_roster_rows('https://example.test')
        self.assertEqual(get.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

    @patch('fetch_tw_universe.time.sleep')
    @patch('fetch_tw_universe.requests.get')
    def test_permanent_http_failure_is_not_retried(self, get, sleep):
        response = self.response([])
        response.status_code = 404
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
        get.return_value = response
        with self.assertRaises(requests.exceptions.HTTPError):
            fetch_roster_rows('https://example.test')
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    @patch('fetch_tw_universe.time.sleep')
    @patch('fetch_tw_universe.requests.get')
    def test_invalid_json_does_not_become_empty_success(self, get, sleep):
        response = self.response([])
        response.json.side_effect = ValueError('invalid JSON')
        get.return_value = response
        with self.assertRaises(ValueError):
            fetch_roster_rows('https://example.test')
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()
