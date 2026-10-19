import asyncio
import json
import unittest

from src.common.settings import VisionSettings
from src.vision.vision_client import VisionTcpClient, normalize_vision_snapshot


class VisionPointMatchTests(unittest.TestCase):
    def match(self, **changes):
        value = dict(recipe_id=1, point_id=2, command_seq=0, matched_recipe_id=1,
                     matched_point_id=3, wrong_point=True, age_seconds=0.05)
        value.update(changes)
        return normalize_vision_snapshot({'point_match': value}, 1).to_dict()['point_match']

    def test_optional_signal_and_explicit_unknown_are_distinct(self):
        self.assertIsNone(normalize_vision_snapshot({}, 1).point_match)
        for value in (None, False, 'wrong', {}, []):
            with self.subTest(value=value):
                match = normalize_vision_snapshot({'point_match': value}, 1).point_match
                self.assertIsNotNone(match)
                self.assertIsNone(match['wrong_point'])

    def test_identified_match_and_mismatch_preserve_boolean(self):
        self.assertIs(self.match()['wrong_point'], True)
        self.assertIs(self.match(matched_point_id=2, wrong_point=False)['wrong_point'], False)
        self.assertIs(self.match(matched_recipe_id=2, matched_point_id=2)['wrong_point'], True)
        self.assertEqual(self.match()['command_seq'], 0)

    def test_invalid_identifiers_age_and_contradictory_verdict_are_unknown(self):
        cases = [dict(wrong_point=value) for value in (None, 0, 1, 'true', 'false')]
        cases += [dict(age_seconds=value) for value in (None, True, -1, '0.1', float('nan'), float('inf'), 10 ** 1000)]
        cases += [dict(wrong_point=False), dict(matched_point_id=2)]
        for key in ('recipe_id', 'point_id', 'command_seq', 'matched_recipe_id', 'matched_point_id'):
            cases += [{key: value} for value in (None, True, -1, 65536, 1.0, '1')]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertIsNone(self.match(**changes)['wrong_point'])


class VisionPointMatchTcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_round_trip_keeps_existing_fields_and_new_signal(self):
        requests = []

        async def respond(reader, writer):
            try:
                request = json.loads(await reader.readline())
                requests.append(request)
                result = {'tracker_valid': False, 'point_match': dict(
                    recipe_id=1, point_id=2, command_seq=3, matched_recipe_id=1,
                    matched_point_id=4, wrong_point=True, age_seconds=0.01)}
                writer.write((json.dumps({'id': request['id'], 'ok': True, 'result': result}) + '\n').encode())
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(respond, '127.0.0.1', 0)
        try:
            settings = VisionSettings(enabled=True, host='127.0.0.1', port=server.sockets[0].getsockname()[1])
            snapshot = await VisionTcpClient(settings).snapshot()
            self.assertFalse(snapshot.tracker_valid)
            self.assertTrue(snapshot.point_match['wrong_point'])
            self.assertEqual(snapshot.point_match['matched_point_id'], 4)
            self.assertEqual(requests[0]['command'], 'vision.status')
            self.assertEqual(requests[0]['params'], {})
        finally:
            server.close()
            await server.wait_closed()
