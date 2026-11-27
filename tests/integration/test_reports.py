"""Read-only report aggregation with isolated PostgreSQL and synthetic observations."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from agentic_gateway.api import create_app
from agentic_gateway.providers.inference.deployments import attempts, deployments, versions
from test_deployments import target_db

AUTH = {"Authorization": "Bearer test-only-operator"}


def test_report_auth_empty_and_validation(db):
    with TestClient(create_app()) as client:
        assert client.get('/admin/portal/reports').status_code == 401
        for query in ('days=0', 'days=91', 'mode=wrong'):
            assert client.get('/admin/portal/reports?' + query, headers=AUTH).status_code == 422
        response = client.get('/admin/portal/reports', headers=AUTH)
        assert response.status_code == 200
        report = response.json()
        assert report['totals']['attempts'] == 0
        for field in ('total_tokens', 'latency_p95_ms', 'success_rate', 'cost_usd', 'uptime_percent'):
            assert report['totals'][field] is None
        assert report['daily'] == []
        assert client.get('/admin/portal/reports?provider=absent', headers=AUTH).status_code == 404


def test_report_aggregates_beyond_page_limit_with_mode_date_and_safe_details(target_db):
    engine, _ = target_db
    now = datetime.now(timezone.utc)
    # More observations than the Portal table page limit. Synthetic live rows do NOT send requests.
    records = [dict(id=f'report-{i}', deployment_id='test-deployment', generation=1,
        mode='fixture' if i < 120 else 'live', started_at=now - timedelta(seconds=10),
        result_code='ok' if i < 124 else 'credential_missing',
        provider_outcome='succeeded' if i < 124 else 'not_sent',
        report={'latency_ms': i + 1, 'prompt': 'private-prompt-marker',
            'output_text': 'private-output-marker', 'api_key': 'private-key-marker',
            'usage': {'status': 'upstream' if i < 100 else 'unknown',
                      'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}})
        for i in range(125)]
    records.append(dict(records[0], id='old-report', started_at=now-timedelta(days=40)))
    with engine.begin() as connection:
        connection.execute(insert(attempts), records)
    with TestClient(create_app()) as client:
        response = client.get('/admin/portal/reports?provider=nvidia&days=7', headers=AUTH)
        assert response.status_code == 200
        report = response.json()
        total = report['totals']
        assert total['attempts'] == 125 and total['succeeded'] == 124 and total['not_sent'] == 1
        assert total['total_tokens'] == 500 and total['known_usage_attempts'] == 100
        assert total['unknown_usage_attempts'] == 25 and total['latency_p95_ms'] == 118.8
        assert sum(r['attempts'] for r in report['daily']) == 125
        assert len(report['details']['recent_attempts']) == 30
        assert report['details']['offerings'] and report['details']['deployments']
        assert report['errors'] == [{'provider': 'nvidia', 'result_code': 'credential_missing', 'count': 1}]
        for secret in ('private-prompt-marker', 'private-output-marker', 'private-key-marker', 'fixture-secret-token'):
            assert secret not in response.text
        live = client.get('/admin/portal/reports?provider=nvidia&mode=live', headers=AUTH).json()
        assert live['totals']['attempts'] == 5 and live['totals']['total_tokens'] is None
        assert live['totals']['known_usage_attempts'] == 0
        fixture = client.get('/admin/portal/reports?provider=nvidia&mode=fixture', headers=AUTH).json()
        assert fixture['totals']['attempts'] == 120
        provider = next(p for p in fixture['providers'] if p['id'] == 'nvidia')
        assert provider['inference_status'] == 'fixture_only'
        assert provider['current_offerings'] > 0
        assert client.get('/admin/portal/reports?days=90', headers=AUTH).json()['totals']['attempts'] == 126


def test_report_uses_historical_generation_and_ignores_malformed_usage(target_db):
    engine, _ = target_db
    with engine.begin() as connection:
        original = dict(connection.execute(select(versions)).mappings().one())
        connection.execute(insert(versions).values(**dict(original, generation=2, upstream_model='changed-current-model')))
        connection.execute(update(deployments).values(generation=2))
        connection.execute(insert(attempts).values(id='historical', deployment_id='test-deployment', generation=1,
            mode='fixture', result_code='ok', provider_outcome='succeeded',
            report={'latency_ms': 'invalid', 'usage': {'status': 'upstream', 'total_tokens': 'invalid'}}))
    with TestClient(create_app()) as client:
        response = client.get('/admin/portal/reports?provider=nvidia', headers=AUTH)
        assert response.status_code == 200
        report = response.json()
        assert report['models'][0]['upstream_model'] == original['upstream_model']
        assert report['details']['deployments'][0]['upstream_model'] == 'changed-current-model'
        assert report['totals']['total_tokens'] is None
        assert report['totals']['latency_p95_ms'] is None
