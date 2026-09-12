import json
import os
from unittest.mock import patch

import pytest

from lecture_context import resolve_lecture, read_lecture, LectureContext
from monitor import MonitorError, determine_status
from desktop_backend import notification_configuration
from tests.test_monitor import page, record

URL = 'https://classes.berkeley.edu/content/2026-fall-math-113-105-dis-105'
LECTURE = 'https://classes.berkeley.edu/content/2026-fall-math-113-001-lec-001'
HTML = '<div data-element="associated_sections" data-section-id="520964"></div>'


def associated(url=LECTURE, identity='22491'):
    return f'<div class="detail-class-associated-sections-flex"><h4><a href="{url}">MATH 113 001</a></h4><div>001 LEC</div><div>Class #: {identity}</div></div>'


def test_parent_is_resolved_from_association_not_discussion_number():
    with patch('lecture_context.fetch_page', side_effect=[HTML, associated()]) as fetch:
        lecture = resolve_lecture(URL)
    assert lecture['url'] == LECTURE
    assert lecture['section_id'] == '22491'
    assert fetch.call_args.args[0] == 'https://classes.berkeley.edu/sections/associated/520964'


@pytest.mark.parametrize('candidate', [
    associated('https://evil.example/content/2026-fall-math-113-001-lec-001'),
    associated(LECTURE.replace('2026','2025')),
    associated(LECTURE.replace('math-113','math-h113')),
    associated(URL), '',
    associated() + associated(LECTURE.replace('001','002'),'22492'),
])
def test_rejects_wrong_host_term_course_component_and_ambiguity(candidate):
    with patch('lecture_context.fetch_page', side_effect=[HTML, candidate]):
        with pytest.raises(MonitorError):
            resolve_lecture(URL)


def test_explicit_parent_must_match_association():
    with patch('lecture_context.fetch_page', side_effect=[HTML, associated()]):
        with pytest.raises(MonitorError):
            resolve_lecture(URL, '99999')


def test_lecture_counts_require_matching_class_identity():
    lecture = {'url':LECTURE,'section_id':'22491','label':'MATH 113 001 LEC'}
    payload = record()
    with patch('lecture_context.fetch_page', return_value=page(payload)):
        with pytest.raises(MonitorError):
            read_lecture(lecture)
    payload['id'] = 22491
    with patch('lecture_context.fetch_page', return_value=page(payload)):
        result = read_lecture(lecture)
    assert result['source'] == 'public'
    assert result['status']['section_id'] == '22491'
    assert result['checked_at']


def test_parent_failure_is_separate_and_retried():
    context = LectureContext(URL)
    events = []
    def emit(kind, **data): events.append((kind,data))
    with patch('lecture_context.resolve_lecture', side_effect=[MonitorError('offline'), {'url':LECTURE}]) as resolve, patch('lecture_context.read_lecture', return_value={'status':{}}):
        context.refresh(emit)
        context.refresh(emit)
    assert [kind for kind,_ in events] == ['lecture_error','lecture']
    assert resolve.call_count == 2


def test_public_closed_is_not_overridden_by_unused_waitlist_capacity():
    status = determine_status(record('C','Closed',45,45))
    assert status.waitlisted < status.waitlist_capacity
    assert not status.is_open
    assert status.status_description == 'Closed'


@pytest.mark.parametrize('environment,channel,mentions', [
    ({},'Not configured',False),
    ({'SMTP_HOST':'smtp.example'},'Email',False),
    ({'NOTIFICATION_WEBHOOK_URL':'https://discord.com/api/webhooks/123/SECRET','DISCORD_USER_ID':'123'},'Discord',True),
    ({'NOTIFICATION_WEBHOOK_URL':'https://discord.com/api/webhooks/123/SECRET','DISCORD_USER_ID':'bad'},'Discord',False),
    ({'NOTIFICATION_WEBHOOK_URL':'https://discord.com.evil.test/SECRET','SMTP_HOST':'smtp.example'},'Webhook',False),
])
def test_notification_indicator_reports_configuration_without_secrets(environment, channel, mentions):
    with patch.dict(os.environ, environment, clear=True):
        result = notification_configuration()
    assert result['channel'] == channel
    assert result['mentions'] is mentions
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.skipif(os.getenv('RUN_LIVE_PUBLIC_TESTS') != '1', reason='Opt-in read-only Berkeley verification')
@pytest.mark.parametrize('url', [URL, URL.replace('105','104')])
def test_live_berkeley_discussion_parent_and_lecture_counts(url):
    lecture = resolve_lecture(url)
    assert lecture['section_id'] == '22491'
    assert lecture['url'] == LECTURE
    data = read_lecture(lecture)
    assert data['status']['section_id'] == '22491'
    assert data['status']['capacity'] > 0
    assert data['status']['enrolled'] >= 0
