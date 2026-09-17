from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from course_search import search_courses, course_sections
from desktop import Api
from tests.test_monitor import page, record

LECTURE = 'https://classes.berkeley.edu/content/2026-fall-math-113-001-lec-001'
DISCUSSION = LECTURE.replace('001-lec-001', '104-dis-104')


def test_latest_term_search_and_pagination():
    facets = '<div id="block-term">' + ''.join(
        f'<a data-drupal-facet-item-value="{key}"><span class="facet-item__value">{label}</span></a>'
        for key, label in [('1', 'Fall 2025'), ('2', 'Spring 2026'), ('3', 'Fall 2026'), ('4', 'Summer Sessions 2026')]
    ) + '</div>'
    rows = f'''<div class="views-row"><div class="st--section-name-wraper"><a href="{LECTURE}">MATH 113 LEC 001</a></div>
        <div class="st--title">Introduction to Abstract Algebra</div><div class="st--term-year">2026 Fall</div></div>
        <div class="pager__item--next"><a href="?page=1">Next</a></div>'''
    with patch('course_search.fetch_page', side_effect=[facets, rows]) as fetch:
        result = search_courses('MATH113')
    assert result['courses'][0]['url'] == LECTURE
    assert result['courses'][0]['title'] == 'Introduction to Abstract Algebra'
    assert result['more'] and result['term_id'] == '3'
    assert parse_qs(urlparse(fetch.call_args.args[0]).query) == {'search':['"MATH 113"'], 'f[0]':['term:3'], 'page':['0']}
    with patch('course_search.fetch_page', return_value=rows) as fetch:
        assert search_courses('Abstract Algebra', '3', 1)['page'] == 1
        assert fetch.call_count == 1
        assert 'Abstract Algebra' in parse_qs(urlparse(fetch.call_args.args[0]).query)['search'][0]
    with patch('course_search.fetch_page', return_value='<p>No results</p>'):
        with pytest.raises(ValueError, match='No matching'):
            search_courses('nonexistent title')


@pytest.mark.parametrize('query,term,page_number', [('', '', 0), ('""', '', 0), (None, '', 0), ('math', 'bad', 0), ('math', '', -1)])
def test_invalid_search_does_not_fetch(query, term, page_number):
    with patch('course_search.fetch_page') as fetch, pytest.raises(ValueError):
        search_courses(query, term, page_number)
    fetch.assert_not_called()


def test_associated_sections_keep_course_term_and_parent():
    profile = {'url':LECTURE, 'label':'MATH 113 LEC 001', 'section_id':'22491', 'component':'LEC', 'term':'2026 Fall', 'meeting':'Mo We'}
    associated = ''.join(f'<div class="detail-class-associated-sections-flex"><h4><a href="{url}">MATH 113 104</a></h4><div><span class="detail-label">Time:</span> Noon</div></div>' for url in [
        DISCUSSION, DISCUSSION, DISCUSSION.replace('2026', '2025'), DISCUSSION.replace('math-113', 'math-104'),
        DISCUSSION.replace('classes.berkeley.edu', 'evil.example')])
    with patch('course_search.discover_course', return_value=profile), patch('course_search.fetch_page', side_effect=[
        '<div data-element="associated_sections" data-section-id="123"></div>', associated]):
        result = course_sections(LECTURE)
    assert len(result['sections']) == 2
    assert result['sections'][1] == {'url':DISCUSSION, 'label':'MATH 113 104 DIS', 'details':'Time: Noon', 'parent':'22491'}
    with patch('course_search.fetch_page') as fetch, pytest.raises(ValueError):
        course_sections('https://evil.example/content/2026-fall-math-113-001-lec-001')
    fetch.assert_not_called()


def test_search_bridge_and_selected_section_save(tmp_path):
    from desktop_backend import Dashboard
    with patch('desktop_backend.read_profiles', return_value=[]):
        dashboard = Dashboard(tmp_path / 'dashboard.json')
    api = Api(dashboard)
    with patch('desktop.search_courses', return_value={'courses':[]}) as search:
        assert api.action('search_courses', {'query':'Abstract Algebra'}) == {'ok':True, 'courses':[]}
        search.assert_called_once_with('Abstract Algebra', '', 0)
    with patch('monitor.fetch_page', return_value=page(record())):
        assert api.action('save', {'url':DISCUSSION, 'parent':'22491', 'mode':'calcentral'})['ok']
    saved = dashboard.snapshot()['classes'][0]
    assert saved['url'] == DISCUSSION and saved['parent'] == '22491'
