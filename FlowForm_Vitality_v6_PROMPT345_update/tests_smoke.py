from app_server import create_app


def test_ready_and_health():
    app = create_app(port=5410)
    client = app.test_client()

    health = client.get('/api/health')
    assert health.status_code == 200
    payload = health.get_json()
    assert payload['status'] in {'ok', 'degraded'}
    assert payload['port'] == 5410
    assert isinstance(payload['db_ok'], bool)
    assert payload['version']

    ready = client.get('/ready')
    assert ready.status_code == 200
    assert b'/api/health' in ready.data


def test_cli_port_overrides_env_port(monkeypatch):
    monkeypatch.setenv('PORT', '5488')
    app = create_app(port=5444)
    client = app.test_client()

    payload = client.get('/api/health').get_json()
    assert payload['port'] == 5444


def test_plan_create_and_current_view(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'plan.db'))
    app = create_app(port=5445)
    client = app.test_client()

    response = client.post(
        '/api/plan/create',
        json={
            'goal': 'hybrid',
            'days_per_week': 4,
            'minutes_per_session': 50,
            'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
            'constraints': 'No jumping',
            'equipment': 'Dumbbells',
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['ok'] is True
    assert payload['plan_id'] > 0

    current = client.get('/plan/current')
    assert current.status_code == 200
    body = current.data
    assert b'Current Plan' in body
    assert b'Week 1' in body
    assert b'Today' in body


def test_regenerate_next_week_preserves_completed_sessions(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setenv('DB_PATH', str(tmp_path / 'regen.db'))
    app = create_app(port=5446)
    client = app.test_client()

    response = client.post(
        '/api/plan/create',
        json={
            'goal': 'hybrid',
            'days_per_week': 3,
            'minutes_per_session': 45,
            'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
        },
    )
    assert response.status_code == 200

    db_path = app.config['DB_PATH']
    con = sqlite3.connect(db_path)
    plan_id = con.execute("SELECT id FROM plan ORDER BY id DESC LIMIT 1").fetchone()[0]

    week2_day = con.execute(
        "SELECT id FROM plan_day WHERE plan_id = ? AND week = 2 ORDER BY day_index LIMIT 1",
        (plan_id,),
    ).fetchone()[0]
    now = '2026-01-01T00:00:00+00:00'
    con.execute(
        """
        INSERT INTO session_completion (plan_day_id, completed_at, rpe, notes, minutes_done, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (week2_day, now, 7, 'done', 45, now, now),
    )
    con.commit()
    con.close()

    regen = client.post('/api/plan/regenerate-next-week', json={})
    assert regen.status_code == 200
    assert regen.get_json()['ok'] is True

    con = sqlite3.connect(db_path)
    completion_count = con.execute(
        "SELECT COUNT(*) FROM session_completion WHERE plan_day_id = ?",
        (week2_day,),
    ).fetchone()[0]
    plan_day_exists = con.execute(
        "SELECT COUNT(*) FROM plan_day WHERE id = ?",
        (week2_day,),
    ).fetchone()[0]
    con.close()

    assert completion_count == 1
    assert plan_day_exists == 1


def test_diagnostics_endpoints():
    app = create_app(port=5411)
    client = app.test_client()

    html = client.get('/diagnostics')
    assert html.status_code == 200
    assert b'Diagnostics' in html.data

    api = client.get('/api/diagnostics')
    assert api.status_code == 200
    payload = api.get_json()
    assert 'status' in payload
    assert 'checks' in payload


def test_session_start_finish_and_summary(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'session.db'))
    app = create_app(port=5412)
    client = app.test_client()

    create = client.post('/api/plan/create', json={
        'goal': 'hybrid',
        'days_per_week': 3,
        'minutes_per_session': 45,
        'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
    })
    assert create.status_code == 200

    current_before = client.get('/plan/current')
    assert current_before.status_code == 200
    assert b'/session/start/' in current_before.data

    import sqlite3
    con = sqlite3.connect(app.config['DB_PATH'])
    plan_day_id = con.execute('SELECT id FROM plan_day ORDER BY id LIMIT 1').fetchone()[0]
    con.close()

    start = client.get(f'/session/start/{plan_day_id}')
    assert start.status_code == 200
    assert b'Start' in start.data
    assert b'Finish' in start.data

    finish = client.post('/api/session/finish', json={
        'plan_day_id': plan_day_id,
        'rpe': 8,
        'notes': 'solid work',
        'minutes_done': 44,
    })
    assert finish.status_code == 200
    payload = finish.get_json()
    assert payload['ok'] is True
    completion_id = payload['completion_id']

    summary = client.get(f'/session/summary/{completion_id}')
    assert summary.status_code == 200
    assert b'Session Summary' in summary.data
    assert b'solid work' in summary.data

    current_after = client.get('/plan/current')
    assert current_after.status_code == 200
    assert b'Completed' in current_after.data


def test_recovery_checkin_persists_and_influences_plan(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'recovery.db'))
    app = create_app(port=5413)
    client = app.test_client()

    create = client.post('/api/plan/create', json={
        'goal': 'hybrid',
        'days_per_week': 3,
        'minutes_per_session': 45,
        'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
    })
    assert create.status_code == 200

    checkin = client.post('/api/recovery/checkin', json={
        'date': '2026-03-01',
        'sleep_hours': 4.5,
        'stress_1_10': 9,
        'soreness_1_10': 8,
        'mood_1_10': 3,
        'notes': 'rough day',
    })
    assert checkin.status_code == 200
    out = checkin.get_json()
    assert out['ok'] is True
    assert out['readiness_label'] == 'low'

    recovery = client.get('/recovery')
    assert recovery.status_code == 200
    assert b'Daily Recovery Check-in' in recovery.data
    assert b'not medical advice' in recovery.data

    plan = client.get('/plan/current')
    assert plan.status_code == 200
    assert b'Readiness:' in plan.data
    assert b'Suggestion:' in plan.data


def test_analytics_updates_after_completion(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'analytics.db'))
    app = create_app(port=5414)
    client = app.test_client()

    create = client.post('/api/plan/create', json={
        'goal': 'hybrid',
        'days_per_week': 3,
        'minutes_per_session': 45,
        'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
    })
    assert create.status_code == 200

    import sqlite3
    con = sqlite3.connect(app.config['DB_PATH'])
    plan_day_id = con.execute('SELECT id FROM plan_day ORDER BY id LIMIT 1').fetchone()[0]
    con.close()

    finish = client.post('/api/session/finish', json={
        'plan_day_id': plan_day_id,
        'rpe': 7,
        'notes': 'complete',
        'minutes_done': 42,
    })
    assert finish.status_code == 200

    checkin = client.post('/api/recovery/checkin', json={
        'date': '2026-03-01',
        'sleep_hours': 7.5,
        'stress_1_10': 4,
        'soreness_1_10': 4,
        'mood_1_10': 7,
    })
    assert checkin.status_code == 200

    analytics = client.get('/analytics')
    assert analytics.status_code == 200
    body = analytics.data
    assert b'Analytics' in body
    assert b'Streak' in body
    assert b'Weekly completion rate' in body
    assert b'Average RPE' in body
    assert b'Readiness trend' in body
    assert b'Takeaway:' in body


def test_exports_downloads_include_required_data(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'exports.db'))
    app = create_app(port=5415)
    client = app.test_client()

    client.post('/api/plan/create', json={
        'goal': 'hybrid',
        'days_per_week': 3,
        'minutes_per_session': 45,
        'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
    })

    import sqlite3, json as _json, zipfile, io
    con = sqlite3.connect(app.config['DB_PATH'])
    plan_day_id = con.execute('SELECT id FROM plan_day ORDER BY id LIMIT 1').fetchone()[0]
    con.close()

    client.post('/api/session/finish', json={
        'plan_day_id': plan_day_id,
        'rpe': 7,
        'notes': 'done',
        'minutes_done': 40,
    })
    client.post('/api/recovery/checkin', json={
        'date': '2026-03-01',
        'sleep_hours': 7.2,
        'stress_1_10': 4,
        'soreness_1_10': 4,
        'mood_1_10': 7,
    })

    exports_page = client.get('/exports')
    assert exports_page.status_code == 200
    assert b'Download Full Backup JSON' in exports_page.data

    plan_html = client.get('/api/export/plan')
    assert plan_html.status_code == 200
    assert b'FlowForm Plan Export' in plan_html.data

    backup = client.get('/api/export/json')
    assert backup.status_code == 200
    payload = _json.loads(backup.data.decode('utf-8'))
    assert payload['plan'] is not None
    assert len(payload['templates']) > 0
    assert len(payload['completions']) > 0
    assert len(payload['recovery']) > 0

    bundle = client.get('/api/export/zip')
    assert bundle.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(bundle.data))
    names = set(zf.namelist())
    assert 'flowform_backup.json' in names
    assert 'flowform_plan_export.html' in names
    assert 'flowform.db' in names


def test_full_backup_endpoint_contains_manifest_and_settings(tmp_path, monkeypatch):
    import io
    import json as _json
    import zipfile

    monkeypatch.setenv('DB_PATH', str(tmp_path / 'full-backup.db'))
    app = create_app(port=5423)
    client = app.test_client()

    backup = client.get('/api/export/backup')
    assert backup.status_code == 200
    assert backup.headers['Content-Type'].startswith('application/zip')

    zf = zipfile.ZipFile(io.BytesIO(backup.data))
    names = set(zf.namelist())
    assert 'flowform.db' in names
    assert 'flowform_backup.json' in names
    assert 'settings.json' in names
    assert 'manifest.json' in names

    manifest = _json.loads(zf.read('manifest.json').decode('utf-8'))
    assert 'counts' in manifest
    assert 'warning' in manifest


def test_restore_backup_preview_and_apply(tmp_path, monkeypatch):
    import io
    import sqlite3

    source_db = tmp_path / 'source.db'
    monkeypatch.setenv('DB_PATH', str(source_db))
    app = create_app(port=5424)
    client = app.test_client()

    client.post('/api/plan/create', json={
        'goal': 'hybrid',
        'days_per_week': 3,
        'minutes_per_session': 45,
        'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
    })
    backup_zip = client.get('/api/export/backup').data

    target_db = tmp_path / 'target.db'
    monkeypatch.setenv('DB_PATH', str(target_db))
    app2 = create_app(port=5425)
    client2 = app2.test_client()

    preview = client2.post(
        '/api/import/backup',
        data={'file': (io.BytesIO(backup_zip), 'backup.zip')},
        content_type='multipart/form-data',
    )
    assert preview.status_code == 200
    preview_payload = preview.get_json()
    assert preview_payload['requires_confirmation'] is True
    assert 'warning' in preview_payload['summary']

    restore = client2.post(
        '/api/import/backup',
        data={
            'file': (io.BytesIO(backup_zip), 'backup.zip'),
            'confirm_overwrite': 'true',
        },
        content_type='multipart/form-data',
    )
    assert restore.status_code == 200
    assert restore.get_json()['ok'] is True

    con = sqlite3.connect(app2.config['DB_PATH'])
    plans = con.execute('SELECT COUNT(*) FROM plan').fetchone()[0]
    con.close()
    assert plans >= 1


def test_pdf_exports_for_plan_and_session(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setenv('DB_PATH', str(tmp_path / 'pdf.db'))
    app = create_app(port=5426)
    client = app.test_client()

    create = client.post('/api/plan/create', json={
        'goal': 'hybrid',
        'days_per_week': 3,
        'minutes_per_session': 45,
        'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
    })
    assert create.status_code == 200
    plan_id = create.get_json()['plan_id']

    con = sqlite3.connect(app.config['DB_PATH'])
    plan_day_id = con.execute('SELECT id FROM plan_day ORDER BY id LIMIT 1').fetchone()[0]
    con.close()

    finish = client.post('/api/session/finish', json={
        'plan_day_id': plan_day_id,
        'rpe': 7,
        'notes': 'pdf test',
        'minutes_done': 42,
    })
    completion_id = finish.get_json()['completion_id']

    plan_pdf = client.get(f'/api/export/plan_pdf/{plan_id}')
    assert plan_pdf.status_code == 200
    assert plan_pdf.headers['Content-Type'].startswith('application/pdf')
    assert plan_pdf.data.startswith(b'%PDF')

    session_pdf = client.get(f'/api/export/session_summary/{completion_id}')
    assert session_pdf.status_code == 200
    assert session_pdf.headers['Content-Type'].startswith('application/pdf')
    assert session_pdf.data.startswith(b'%PDF')


def test_auth_two_users_have_isolated_plans(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'auth.db'))
    monkeypatch.setenv('ENABLE_AUTH', 'true')
    app = create_app(port=5427)
    client = app.test_client()

    signup_a = client.post('/signup', data={
        'display_name': 'User A',
        'email': 'a@example.com',
        'password': 'pass1234',
    })
    assert signup_a.status_code in (302, 303)
    create_a = client.post('/api/plan/create', json={
        'goal': 'strength',
        'days_per_week': 3,
        'minutes_per_session': 45,
        'disciplines': ['strength', 'mobility', 'recovery', 'cardio', 'conditioning'],
    })
    assert create_a.status_code == 200
    client.get('/logout')

    signup_b = client.post('/signup', data={
        'display_name': 'User B',
        'email': 'b@example.com',
        'password': 'pass5678',
    })
    assert signup_b.status_code in (302, 303)
    create_b = client.post('/api/plan/create', json={
        'goal': 'mobility',
        'days_per_week': 2,
        'minutes_per_session': 30,
        'disciplines': ['mobility', 'recovery', 'strength', 'cardio', 'conditioning'],
    })
    assert create_b.status_code == 200
    page_b = client.get('/plan/current')
    assert b'Mobility 4-Week Plan' in page_b.data
    client.get('/logout')

    login_a = client.post('/login', data={'email': 'a@example.com', 'password': 'pass1234'})
    assert login_a.status_code in (302, 303)
    page_a = client.get('/plan/current')
    assert b'Strength 4-Week Plan' in page_a.data
    assert b'Mobility 4-Week Plan' not in page_a.data


def test_free_tier_blocks_second_active_plan(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'gating.db'))
    monkeypatch.setenv('ENABLE_AUTH', 'true')
    app = create_app(port=5428)
    client = app.test_client()

    client.post('/signup', data={
        'display_name': 'Free User',
        'email': 'free@example.com',
        'password': 'pass1111',
    })

    first = client.post('/api/plan/create', json={
        'goal': 'hybrid',
        'days_per_week': 3,
        'minutes_per_session': 45,
        'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
    })
    assert first.status_code == 200

    second = client.post('/api/plan/create', json={
        'goal': 'hybrid',
        'days_per_week': 3,
        'minutes_per_session': 45,
        'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
    })
    assert second.status_code == 403
    payload = second.get_json()
    assert payload['error'] == 'free_tier_limit_reached'
    assert payload['pay_now_link'] is None


def test_single_user_mode_when_auth_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'single-user.db'))
    monkeypatch.setenv('ENABLE_AUTH', 'false')
    app = create_app(port=5429)
    client = app.test_client()

    wizard = client.get('/plan/wizard')
    assert wizard.status_code == 200
    create = client.post('/api/plan/create', json={
        'goal': 'hybrid',
        'days_per_week': 3,
        'minutes_per_session': 45,
        'disciplines': ['strength', 'cardio', 'mobility', 'recovery', 'conditioning'],
    })
    assert create.status_code == 200


def test_ready_shows_counts_and_links(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'ready.db'))
    app = create_app(port=5416)
    client = app.test_client()

    response = client.get('/ready')
    assert response.status_code == 200
    body = response.data
    assert b'Data snapshot' in body
    assert b'Templates:' in body
    assert b'Plan Wizard' in body
    assert b'Current Plan' in body
    assert b'Templates' in body
    assert b'Recovery' in body
    assert b'Analytics' in body
    assert b'Exports' in body


def test_friendly_html_404():
    app = create_app(port=5417)
    client = app.test_client()

    html = client.get('/no-such-route')
    assert html.status_code == 404
    assert b'Page not found' in html.data

    api = client.get('/api/no-such-route')
    assert api.status_code == 404
    assert api.get_json()['error'] == 'not_found'


def test_nav_tabs_and_dashboard_toggle(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'nav.db'))
    app = create_app(port=5415)
    client = app.test_client()

    # Core nav pages
    assert client.get('/ready').status_code == 200
    assert client.get('/dashboard').status_code == 200
    assert client.get('/dashboard?view=week').status_code == 200
    assert client.get('/dashboard?view=month').status_code == 200
    assert client.get('/sessions').status_code == 200
    assert client.get('/sessions/new').status_code == 200
    assert client.get('/recovery').status_code == 200
    # Plan/current must not crash even if no plan exists
    assert client.get('/plan/current').status_code == 200


def test_standalone_session_create_complete_updates_dashboard(tmp_path, monkeypatch):
    import re

    monkeypatch.setenv('DB_PATH', str(tmp_path / 'manual.db'))
    app = create_app(port=5416)
    client = app.test_client()

    # Create a manual session (pending)
    resp = client.post(
        '/sessions/create',
        data={
            'title': 'Manual Boxing',
            'category': 'boxing',
            'intensity': 'high',
            'duration_minutes': '30',
            'notes': 'test',
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    loc = resp.headers.get('Location', '')
    m = re.search(r'/sessions/(\d+)', loc)
    assert m, f'expected redirect to session detail, got {loc}'
    session_id = int(m.group(1))

    # Complete it with perceived exertion
    done = client.post(
        f'/sessions/{session_id}/complete',
        data={'heart_rate_avg': '140', 'calories': '250', 'perceived_exertion': '8'},
        follow_redirects=False,
    )
    assert done.status_code in (302, 303)

    dash_week = client.get('/dashboard?view=week')
    assert dash_week.status_code == 200
    body = dash_week.data
    assert b'Sessions completed' in body
    assert b'Minutes completed' in body
    # At minimum, the minutes should include the 30-minute completion
    assert b'30' in body

    dash_month = client.get('/dashboard?view=month')
    assert dash_month.status_code == 200



def test_assistant_fallback_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'assistant.db'))
    app = create_app(port=5418)
    client = app.test_client()

    chat = client.post('/api/assistant/chat', json={'message': 'Give me motivation for today'})
    assert chat.status_code == 200
    payload = chat.get_json()
    assert payload['ok'] is True
    assert isinstance(payload['reply'], str)
    assert 'not a healthcare professional' in payload['reply'].lower()

    page = client.get('/assistant')
    assert page.status_code == 200
    assert b'Assistant Coach' in page.data


def test_media_upload_and_attach_to_manual_session(tmp_path, monkeypatch):
    import io
    import sqlite3
    from pathlib import Path

    monkeypatch.setenv('DB_PATH', str(tmp_path / 'media.db'))
    app = create_app(port=5419)
    client = app.test_client()

    # Upload a small PDF
    data = {
        'tags': 'test',
        'file': (io.BytesIO(b'%PDF-1.4\n%test\n'), 'test.pdf'),
    }
    res = client.post('/media/upload', data=data, content_type='multipart/form-data', follow_redirects=True)
    assert res.status_code == 200
    assert b'test.pdf' in res.data

    con = sqlite3.connect(app.config['DB_PATH'])
    media_id, stored_name = con.execute('SELECT id, stored_name FROM media_item ORDER BY id DESC LIMIT 1').fetchone()

    # Create a manual session with attached media
    form = {
        'title': 'Manual Test Session',
        'category': 'mobility',
        'intensity': '5',
        'duration_minutes': '20',
        'notes': 'notes',
        'media_id': str(media_id),
    }
    created = client.post('/sessions/create', data=form, follow_redirects=True)
    assert created.status_code == 200
    assert b'Attached media' in created.data
    assert b'test.pdf' in created.data

    # Cleanup: remove stored media file
    con.close()
    media_path = Path(app.root_path) / 'instance' / 'media' / stored_name
    if media_path.exists():
        media_path.unlink()
