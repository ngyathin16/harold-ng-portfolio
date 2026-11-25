"""Operator resume must preserve the durable wakeup path before unpausing."""
import os
import unittest
from unittest.mock import MagicMock, patch

from ops import serverless_control


class ResumeTests(unittest.TestCase):
    def invoke(self, command, failure=None):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {'paused': command != 'resume'}
        events = []
        connection.execute.side_effect = lambda *args: (
            events.append(args), connection.execute.return_value)[1]
        markers = MagicMock()

        def repair(conn, store):
            self.assertIs(conn, connection)
            self.assertIs(store, markers)
            events.append('repair')
            if failure:
                raise failure
            return 2

        environment = {
            'RESEARCH_OPERATOR_DATABASE_URL': 'postgresql://operator@db.example/research?sslmode=verify-full',
            'RESEARCH_DISPATCH_MARKER_BUCKET': 'research-test-markers',
        }
        with patch.dict(os.environ, environment), patch('sys.argv', ['serverless_control', command]), \
                patch('ops.serverless_control.psycopg.connect') as connect, \
                patch('service.markers.GCSMarkers', return_value=markers), \
                patch('service.markers.repair', side_effect=repair), patch('builtins.print'):
            connect.return_value.__enter__.return_value = connection
            if failure:
                with self.assertRaisesRegex(RuntimeError, 'marker unavailable'):
                    serverless_control.main()
                self.assertIs(connect.return_value.__exit__.call_args.args[0], RuntimeError)
            else:
                serverless_control.main()
        markers.close.assert_called_once()
        return events

    def test_resume_repairs_before_enabling_dispatch(self):
        events = self.invoke('resume')
        update = next(i for i, event in enumerate(events)
                      if event != 'repair' and event[0].startswith('UPDATE research.dispatch_control'))
        self.assertLess(events.index('repair'), update)
        self.assertEqual(events[update][1], (False,))

    def test_failed_repair_never_unpauses(self):
        events = self.invoke('resume', RuntimeError('marker unavailable'))
        self.assertFalse(any(event != 'repair' and event[0].startswith('UPDATE research.dispatch_control')
                             for event in events))

    def test_repair_only_keeps_dispatch_paused(self):
        events = self.invoke('repair-markers')
        self.assertIn('repair', events)
        self.assertFalse(any(event != 'repair' and event[0].startswith('UPDATE research.dispatch_control')
                             for event in events))
