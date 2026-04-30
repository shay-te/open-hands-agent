"""Flask app entrypoint for the Kato planning UI.

Bridges browser tabs to live :class:`StreamingClaudeSession` instances
managed by the kato process. Uses Server-Sent Events (server→browser)
plus regular POST endpoints (browser→server) instead of WebSockets — same
functional surface, but reliable on Werkzeug's dev server.

Endpoints:
    GET  /                                  — HTML shell
    GET  /healthz                           — liveness
    GET  /logo.png                          — kato logo
    GET  /api/sessions                      — list all session records
    GET  /api/sessions/<task_id>            — one record + recent events
    GET  /api/sessions/<task_id>/events     — SSE: live agent events
    GET  /api/sessions/<task_id>/files      — repo file tree (Files tab)
    GET  /api/sessions/<task_id>/diff       — committed + uncommitted diff
    POST /api/sessions/<task_id>/messages   — body: {"text": "..."}
    POST /api/sessions/<task_id>/permission — body: {"request_id", "allow", "rationale"}
    GET  /api/status/recent                 — recent kato-process log entries
    GET  /api/status/events                 — SSE: live kato-process log feed
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    stream_with_context,
)

from kato_webserver.git_diff_utils import (
    current_branch,
    detect_default_branch,
    diff_against_base,
    tracked_file_tree,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
KATO_REPO_ROOT = REPO_ROOT.parent

# Browser-driven SSE stream cadence. The server pushes new events to the
# stream as they appear in the live session's recent_events buffer.
_SSE_POLL_INTERVAL_SECONDS = 0.1
# Periodic SSE comment that keeps proxies / load balancers from idling
# the connection out and lets the browser detect server crashes.
_SSE_HEARTBEAT_SECONDS = 15.0
# How often the SSE stream re-checks the repo's HEAD against the session's
# expected branch and pushes a `branch_state` event to the browser.
_BRANCH_CHECK_INTERVAL_SECONDS = 2.0
def _record_cwd_or_none(manager, task_id: str) -> str | None:
    """Return the session's cwd if a record exists and points to a real dir."""
    record = manager.get_record(task_id)
    if record is None:
        return None
    cwd = getattr(record, 'cwd', '') or ''
    if not cwd or not Path(cwd).is_dir():
        return None
    return cwd


def _branch_lock_state(record, cwd: str) -> dict[str, Any]:
    expected = (
        getattr(record, 'expected_branch', '') if record is not None else ''
    )
    current = current_branch(cwd)
    locked = bool(expected) and bool(current) and expected != current
    return {
        'expected': expected,
        'current': current,
        'locked': locked,
    }


def create_app(
    *,
    session_manager=None,
    fallback_state_dir: str = '',
    status_broadcaster=None,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / 'templates'),
        static_folder=str(REPO_ROOT / 'static'),
    )
    if session_manager is None:
        session_manager = _build_fallback_manager(fallback_state_dir)
    app.config['SESSION_MANAGER'] = session_manager
    app.config['STATUS_BROADCASTER'] = status_broadcaster

    _register_http_routes(app)
    _register_streaming_routes(app)
    _register_status_routes(app)
    return app


# ----- HTTP routes -----


def _register_http_routes(app: Flask) -> None:

    @app.get('/')
    def index() -> str:
        records = _records_as_dicts(app.config['SESSION_MANAGER'])
        return render_template('index.html', sessions=records)

    @app.get('/api/sessions')
    def list_sessions():
        return jsonify(_records_as_dicts(app.config['SESSION_MANAGER']))

    @app.get('/api/sessions/<task_id>')
    def get_session(task_id: str):
        manager = app.config['SESSION_MANAGER']
        record = manager.get_record(task_id)
        if record is None:
            return jsonify({'error': 'session not found'}), 404
        payload = _record_to_dict(record)
        session = manager.get_session(task_id)
        payload['live'] = session is not None and session.is_alive
        if session is not None:
            payload['recent_events'] = [
                event.to_dict() for event in session.recent_events()
            ]
        else:
            payload['recent_events'] = []
        return jsonify(payload)

    @app.get('/healthz')
    def healthz():
        return {'status': 'ok'}

    @app.get('/logo.png')
    def logo():
        candidate = KATO_REPO_ROOT / 'kato.png'
        if not candidate.exists():
            return ('logo not found', 404)
        return send_file(candidate, mimetype='image/png')

    @app.get('/api/sessions/<task_id>/files')
    def list_session_files(task_id: str):
        manager = app.config['SESSION_MANAGER']
        cwd = _record_cwd_or_none(manager, task_id)
        if cwd is None:
            return jsonify({'error': 'session not found'}), 404
        return jsonify({'cwd': cwd, 'tree': tracked_file_tree(cwd)})

    @app.get('/api/sessions/<task_id>/diff')
    def get_session_diff(task_id: str):
        manager = app.config['SESSION_MANAGER']
        cwd = _record_cwd_or_none(manager, task_id)
        if cwd is None:
            return jsonify({'error': 'session not found'}), 404
        base = detect_default_branch(cwd)
        if not base:
            return jsonify({'error': 'could not detect default branch'}), 500
        return jsonify({
            'base': base,
            'head': current_branch(cwd),
            'diff': diff_against_base(cwd, f'origin/{base}'),
        })


# ----- live status feed (SSE) -----


def _register_status_routes(app: Flask) -> None:

    @app.get('/api/status/recent')
    def status_recent():
        broadcaster = app.config.get('STATUS_BROADCASTER')
        if broadcaster is None:
            return jsonify({'entries': [], 'latest_sequence': 0})
        return jsonify({
            'entries': [entry.to_dict() for entry in broadcaster.recent()],
            'latest_sequence': broadcaster.latest_sequence(),
        })

    @app.get('/api/status/events')
    def status_events_stream():
        broadcaster = app.config.get('STATUS_BROADCASTER')
        if broadcaster is None:
            # Stream a single "disabled" event then close so the UI can
            # render a tasteful "no live feed" line instead of waiting.
            def _empty():
                yield _sse_message('status_disabled', {})
            return Response(
                stream_with_context(_empty()),
                mimetype='text/event-stream',
                headers={'Cache-Control': 'no-cache, no-transform'},
            )
        return Response(
            stream_with_context(_status_event_stream(broadcaster)),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no',
            },
        )


def _status_event_stream(broadcaster):
    """Yield SSE frames for live kato status entries.

    Pushes the buffered backlog up front (so a freshly-connecting browser
    sees the last 500 lines), then long-polls the broadcaster's condition
    variable for new entries. A periodic SSE comment keeps the connection
    alive through proxies that idle out silent streams.
    """
    backlog = broadcaster.recent()
    last_sequence = backlog[-1].sequence if backlog else 0
    for entry in backlog:
        yield _sse_message('status_entry', entry.to_dict())
    last_heartbeat = time.monotonic()
    while True:
        new_entries = broadcaster.wait_for_new(
            since_sequence=last_sequence,
            timeout=_SSE_HEARTBEAT_SECONDS,
        )
        for entry in new_entries:
            yield _sse_message('status_entry', entry.to_dict())
            last_sequence = entry.sequence
        if not new_entries and time.monotonic() - last_heartbeat >= _SSE_HEARTBEAT_SECONDS:
            yield ': ping\n\n'
            last_heartbeat = time.monotonic()


# ----- streaming routes (SSE + POST) -----


def _register_streaming_routes(app: Flask) -> None:

    @app.get('/api/sessions/<task_id>/events')
    def session_events_stream(task_id: str):
        manager = app.config['SESSION_MANAGER']
        return Response(
            stream_with_context(_event_stream_generator(manager, task_id)),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no',  # don't let buffering proxies stall the stream
            },
        )

    @app.post('/api/sessions/<task_id>/messages')
    def post_message(task_id: str):
        session, error = _resolve_writable_session(
            app.config['SESSION_MANAGER'], task_id,
        )
        if error is not None:
            return error
        payload = request.get_json(silent=True) or {}
        text = str(payload.get('text', '') or '').strip()
        if not text:
            return jsonify({'error': 'text is required'}), 400
        try:
            session.send_user_message(text)
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
        return jsonify({'status': 'delivered', 'text': text})

    @app.post('/api/sessions/<task_id>/stop')
    def stop_session(task_id: str):
        manager = app.config['SESSION_MANAGER']
        if manager.get_record(task_id) is None:
            return jsonify({'error': 'session not found'}), 404
        try:
            manager.terminate_session(task_id)
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
        return jsonify({'status': 'stopped'})

    @app.post('/api/sessions/<task_id>/permission')
    def post_permission(task_id: str):
        session, error = _resolve_writable_session(
            app.config['SESSION_MANAGER'], task_id,
        )
        if error is not None:
            return error
        payload = request.get_json(silent=True) or {}
        request_id = str(payload.get('request_id', '') or '').strip()
        if not request_id:
            return jsonify({'error': 'request_id is required'}), 400
        try:
            session.send_permission_response(
                request_id=request_id,
                allow=bool(payload.get('allow', False)),
                rationale=str(payload.get('rationale', '') or ''),
            )
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500
        return jsonify({'status': 'delivered'})


def _resolve_writable_session(manager, task_id: str):
    """Return (session, None) if the session is writable; (None, error_response) otherwise.

    Encapsulates the "is there a live subprocess and is the repo on the
    right branch?" preamble that every POST endpoint does. Keeps each
    handler focused on parsing its own payload and forwarding the call.
    """
    session = manager.get_session(task_id)
    if session is None or not session.is_alive:
        return None, (jsonify({'error': 'session is not running'}), 409)
    record = manager.get_record(task_id)
    lock = _branch_lock_state(record, session.cwd)
    if lock['locked']:
        return None, (
            jsonify({
                'error': (
                    f"refusing to forward: repo is on '{lock['current']}' "
                    f"but this session expects '{lock['expected']}'. "
                    'kato has moved on; wait for the repo to return to '
                    'the right branch before sending.'
                ),
                'branch_state': lock,
            }),
            409,
        )
    return session, None


def _event_stream_generator(manager, task_id: str):
    """Yield SSE frames for one tab's session.

    Three lifecycle outcomes:
      * `session_missing`  — no record exists for this task.
      * `session_idle`     — a record exists but no live subprocess.
      * (live stream + `session_closed`) — events flow until the
        subprocess exits and the buffer drains.
    """
    record = manager.get_record(task_id)
    if record is None:
        yield _sse_message('session_missing', {})
        return
    session = manager.get_session(task_id)
    if session is None:
        yield _sse_message('session_idle', _record_to_dict(record))
        return
    yield from _replay_session_backlog(session, record)
    yield from _follow_live_session(session, record)


def _replay_session_backlog(session, record):
    """Catch a freshly-connecting browser up on everything seen so far.

    Pushes every buffered event in order plus the current branch-lock
    state, so the UI's send button can be enabled/disabled before the
    user even tries to type.
    """
    backlog = session.recent_events()
    for event in backlog:
        yield _sse_message('session_event', {'event': event.to_dict()})
    yield _sse_message('branch_state', _branch_lock_state(record, session.cwd))


def _follow_live_session(session, record):
    """Tail new events as they arrive, plus periodic branch-state and heartbeat."""
    last_index = len(session.recent_events())
    last_heartbeat = time.monotonic()
    last_branch_check = time.monotonic()
    last_branch_state = _branch_lock_state(record, session.cwd)
    while True:
        current = session.recent_events()
        if len(current) > last_index:
            for event in current[last_index:]:
                yield _sse_message('session_event', {'event': event.to_dict()})
            last_index = len(current)

        if not session.is_alive and last_index >= len(session.recent_events()):
            yield _sse_message('session_closed', {})
            return

        if time.monotonic() - last_branch_check >= _BRANCH_CHECK_INTERVAL_SECONDS:
            fresh = _branch_lock_state(record, session.cwd)
            if fresh != last_branch_state:
                yield _sse_message('branch_state', fresh)
                last_branch_state = fresh
            last_branch_check = time.monotonic()

        if time.monotonic() - last_heartbeat >= _SSE_HEARTBEAT_SECONDS:
            yield ': ping\n\n'
            last_heartbeat = time.monotonic()

        time.sleep(_SSE_POLL_INTERVAL_SECONDS)


def _sse_message(event_type: str, data: dict[str, Any]) -> str:
    """Serialize one SSE message frame.

    Format follows the W3C SSE spec: an `event:` line names the event type
    (we route on this in JS), and a `data:` line carries the JSON payload.
    """
    body = dict(data)
    body['type'] = event_type
    return f'event: {event_type}\ndata: {json.dumps(body)}\n\n'


# ----- helpers -----


def _records_as_dicts(manager) -> list[dict[str, Any]]:
    return [_record_to_dict(record) for record in manager.list_records()]


def _record_to_dict(record) -> dict[str, Any]:
    if hasattr(record, 'to_dict'):
        return record.to_dict()
    if isinstance(record, dict):
        return record
    return {'task_id': str(getattr(record, 'task_id', '') or '')}


def _build_fallback_manager(fallback_state_dir: str):
    """Stand up a minimal manager so dev runs of the webserver don't crash."""
    try:
        from kato.client.claude.session_manager import ClaudeSessionManager
    except ImportError:
        from kato_webserver.session_registry import SessionRegistry

        class _RegistryAsManager:
            def __init__(self) -> None:
                self._registry = SessionRegistry()

            def list_records(self):
                return []

            def get_record(self, task_id: str):  # noqa: ARG002
                return None

            def get_session(self, task_id: str):  # noqa: ARG002
                return None

        return _RegistryAsManager()

    state_dir = (
        fallback_state_dir
        or os.environ.get('KATO_SESSION_STATE_DIR')
        or str(Path.home() / '.kato' / 'sessions')
    )
    return ClaudeSessionManager(state_dir=state_dir)


def main() -> None:
    """Run the dev server. Use kato.main for a real run with shared state."""
    app = create_app()
    host = os.environ.get('KATO_WEBSERVER_HOST', '127.0.0.1')
    port = int(os.environ.get('KATO_WEBSERVER_PORT', '5050'))
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
