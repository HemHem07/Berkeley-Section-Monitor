"""Opt-in rendered UI checks: RUN_DESKTOP_UI_TESTS=1 python -m pytest tests/test_desktop_ui.py."""
import os
from pathlib import Path

import pytest

from desktop import dashboard_html

output = Path(__file__).resolve().parents[1] / ".desktop-qa"

pytestmark = pytest.mark.skipif(os.getenv("RUN_DESKTOP_UI_TESTS") != "1", reason="Opt-in desktop browser QA")


@pytest.fixture
def dashboard_page():
    from playwright.sync_api import sync_playwright
    output.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width":1180,"height":820})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(dashboard_html())
        page.evaluate("""() => {
          const profile = (id,label,number,status,state) => ({id,label,section_id:number,
            url:'https://classes.berkeley.edu/content/2026-fall-math-104-009-lec-009',
            term:'2026 Fall',mode:'calcentral',interval:60,notification:'default',parent:'',
            checked_at:new Date().toISOString(),status,state,error:'',
            check_phase:'waiting',next_check_at:Date.now()/1000 + 42});
          window.fixture = {classes:[
            profile('aaa','MATH 104 · Lecture 009','26088',{is_open:true,enrolled:38,capacity:40,waitlisted:0,waitlist_capacity:6,status_description:'Open'},'running'),
            profile('bbb','MATH 113 · Discussion 104','27743',{is_open:false,enrolled:40,capacity:40,waitlisted:3,waitlist_capacity:6,status_description:'Waitlist'},'running'),
            profile('ccc','COMPSCI 61A · Lecture 001','20001',null,'paused')],
            default_notification:'seats',tray_available:true,notification_configured:true,
            activity:[{class_id:'aaa',label:'MATH 104',kind:'changed',at:new Date().toISOString(),message:'Enrolled: 40 → 38'},
              {class_id:'bbb',label:'MATH 113',kind:'error',at:new Date().toISOString(),message:'Notification delivery failed.'}]};
          window.calls = [];
          window.fixture.notifications = {channel:'Discord',configured:true,mentions:true};
          window.fixture.classes[1].component = 'DIS';
          window.fixture.classes[1].lecture = {label:'MATH 113 001 LEC',section_id:'22491',checked_at:new Date().toISOString(),source:'public',status:{enrolled:350,capacity:350,waitlisted:6,waitlist_capacity:48,status_description:'Closed'}};
          window.pywebview = {api:{snapshot:async()=>structuredClone(window.fixture), action:async(name,values)=>{
            window.calls.push({name,values});
            if(name==='theme') window.fixture.theme=values.theme;
            if(name==='move_earlier' || name==='move_later') {
              const items=window.fixture.classes, index=items.findIndex(p=>p.id===values.id), next=index+(name==='move_earlier'?-1:1);
              if(next>=0 && next<items.length) [items[index],items[next]]=[items[next],items[index]];
            }
            if(name==='save_discord') window.fixture.notifications.user_id=values.user_id;
            if(name==='pause') window.fixture.classes.find(p=>p.id===values.id).state='paused';
            if(name==='save') {const item=window.fixture.classes.find(p=>p.id===values.id); if(item) Object.assign(item,values);}
            if(name==='remove') window.fixture.classes=window.fixture.classes.filter(p=>p.id!==values.id);
            return {ok:true};
          }}};
          window.dispatchEvent(new Event('pywebviewready'));
        }""")
        page.locator('.card').first.wait_for()
        try:
            yield page
            assert not errors, errors
        finally:
            browser.close()


def test_dashboard_counts_and_freshness(dashboard_page):
    page = dashboard_page
    page.evaluate("""() => {
      const now = Date.now;
      Date.now = () => 1000000;
      try {
        const item = {...window.fixture.classes[0], interval:60, checked_at:new Date(879000).toISOString()};
        if (!staleReading(item)) throw Error('Old reading not stale');
        if (staleReading({...item,interval:300})) throw Error('Long interval incorrectly stale');
        if (staleReading({...item,checked_at:new Date(999000).toISOString()})) throw Error('Fresh reading stale');
        if (staleReading({...item,checked_at:null})) throw Error('Missing baseline stale');
      } finally { Date.now = now; }
    }""")
    page.evaluate("window.fixture.classes[0].checked_at=new Date(Date.now()-180000).toISOString(); window.dispatchEvent(new Event('focus'))")
    page.locator('[data-id="aaa"] .stale-notice').wait_for()
    assert 'data is stale' in page.locator('[data-id="aaa"] .availability-caption').inner_text()
    page.evaluate("window.fixture.classes[0].checked_at=new Date().toISOString(); window.dispatchEvent(new Event('focus'))")
    page.locator('[data-id="aaa"] .stale-notice').wait_for(state='detached')
    assert page.locator('.card').count() == 3
    assert page.get_by_text('2 seats available').count() == 1
    assert page.locator('#notification-status').count() == 0
    assert page.locator('.topbar nav svg').count() == 3
    assert '350 / 350' in page.locator('[data-id="bbb"] .lecture-context').inner_text()
    assert 'public data' in page.locator('[data-id="bbb"] .lecture-context').inner_text()


def test_discord_preferences(dashboard_page):
    page = dashboard_page
    page.locator('#preferences').click()
    assert 'mentions: enabled' in page.locator('#delivery-details').inner_text()
    assert page.locator('#discord-webhook').get_attribute('type') == 'password'
    field = page.locator('#discord-webhook').bounding_box()
    toggle = page.locator('#show-webhook').bounding_box()
    assert toggle['y'] >= field['y'] + field['height'] + 8 or toggle['x'] >= field['x'] + field['width'] + 8
    page.locator('#discord-webhook').fill('https://discord.com/api/webhooks/123/fake_test_token')
    page.locator('#show-webhook').click()
    assert page.locator('#discord-webhook').get_attribute('type') == 'text'
    page.locator('#test-discord').click()
    assert 'Save your Discord changes' in page.locator('#discord-result').inner_text()
    assert not page.evaluate('window.calls.some(p=>p.name==="test_discord")')
    page.locator('#discord-user-id').fill('456')
    page.locator('#save-discord').click()
    page.get_by_text('Discord settings saved and applied.', exact=True).wait_for()
    assert page.locator('#discord-webhook').input_value() == ''
    assert page.locator('#discord-webhook').get_attribute('type') == 'password'
    page.locator('#test-discord').click()
    page.get_by_text('Discord accepted the test notification. Check your channel.', exact=True).wait_for()
    assert page.evaluate('window.calls.filter(p=>p.name==="test_discord").length') == 1
    page.screenshot(path=str(output / 'discord-settings.png'), full_page=True)
    page.get_by_role('button',name='Close preferences').click()


def test_card_menus_and_order(dashboard_page):
    page = dashboard_page
    page.locator('[data-id="aaa"] [data-action="menu"]').click()
    assert page.locator('[data-id="aaa"] [data-action="move_earlier"]').is_disabled()
    page.locator('h1').click()
    assert page.locator('[data-id="aaa"] .menu').is_hidden()
    assert page.locator('[data-id="aaa"] [data-action="menu"]').get_attribute('aria-expanded') == 'false'
    page.locator('[data-id="aaa"] [data-action="menu"]').click()
    page.locator('[data-id="bbb"] [data-action="menu"]').click()
    assert page.locator('[data-id="aaa"] .menu').is_hidden()
    page.keyboard.press('Escape')
    assert page.locator('[data-id="bbb"] .menu').is_hidden()
    assert page.locator('[data-id="bbb"] [data-action="menu"]').evaluate('(node) => node === document.activeElement')
    page.locator('[data-id="aaa"] [data-action="menu"]').click()
    page.locator('[data-id="aaa"] [data-action="move_later"]').click()
    page.wait_for_function("document.querySelector('.card').dataset.id === 'bbb'")
    page.locator('[data-id="aaa"] [data-action="move_earlier"]').click()
    page.wait_for_function("document.querySelector('.card').dataset.id === 'aaa'")
    page.locator('[data-id="aaa"] [data-action="menu"]').click()
    assert page.locator('[data-id="aaa"] .check-label').inner_text().startswith('Next check in 0:')
    assert page.get_by_text('Press Start for the first check').count() == 1


def test_activity_and_countdown(dashboard_page):
    page = dashboard_page
    summary = page.locator('.activity-panel summary')
    summary.click()
    page.wait_for_function("!document.querySelector('.activity-panel').open")
    summary.focus()
    page.keyboard.press('Enter')
    page.wait_for_function("document.querySelector('.activity-panel').open && document.querySelector('.activity-panel').getAnimations().length === 0")
    assert page.locator('#activity-list li').count() == 2
    page.locator('#activity-filter').select_option('bbb')
    assert page.locator('#activity-list li').count() == 1
    assert 'Notification delivery failed.' in page.locator('#activity-list').inner_text()
    page.locator('#activity-filter').select_option('')
    page.locator('[data-id="aaa"] [data-action="check_now"]').click()
    assert page.evaluate('window.calls.some(p=>p.name==="check_now" && p.values.id==="aaa")')
    page.evaluate("""() => {
      const originalNow = Date.now;
      Date.now = () => 100000;
      try {
        const scheduled = {state:'running',check_phase:'waiting',interval:60,next_check_at:142};
        if (!checkProgress(scheduled).includes('Next check in 0:42')) throw Error('Wrong countdown');
        if (!checkProgress(scheduled).includes('aria-valuenow="30"')) throw Error('Wrong progress');
        if (!checkProgress({...scheduled,state:'error'}).includes('Retry in 0:42')) throw Error('Missing retry label');
        if (!checkProgress({...scheduled,state:'signin'}).includes('Waiting for CalNet')) throw Error('Missing sign-in label');
        if (!checkProgress({...scheduled,check_phase:'checking',next_check_at:null}).includes('Checking now')) throw Error('Missing checking label');
        if (!checkProgress({...scheduled,next_check_at:99}).includes('Next check due')) throw Error('Expired deadline went negative');
      } finally {Date.now = originalNow;}
    }""")
    page.screenshot(path=str(output / "dashboard.png"), full_page=True)


def test_class_dialog_and_preferences(dashboard_page):
    page = dashboard_page
    page.locator('[data-id="aaa"] [data-action="pause"]').click()
    page.locator('[data-id="aaa"] [data-action="start"]').wait_for()
    assert 'Last known availability' in page.locator('[data-id="aaa"]').inner_text()
    assert page.locator('[data-id="aaa"] .check-label').inner_text() == 'Paused · no check scheduled'
    page.locator('[data-id="aaa"] [data-action="settings"]').click()
    page.locator('input[name="interval"]').fill('120')
    page.locator('select[name="notification"]').select_option('muted')
    page.screenshot(path=str(output / "settings.png"), full_page=True)
    page.mouse.click(10, 100)
    page.locator('#class-dialog').wait_for(state='hidden')
    assert page.evaluate('window.calls.find(p=>p.name==="save").values.interval') == '120'
    assert page.evaluate('window.calls.find(p=>p.name==="save").values.id') == 'aaa'
    page.get_by_role('button',name='Preferences',exact=True).click()
    page.locator('select[name="theme"]').select_option('dark')
    assert page.locator('html').get_attribute('data-theme') == 'dark'
    page.screenshot(path=str(output / 'dark-preferences.png'), full_page=True)
    page.mouse.click(10, 100)
    page.locator('#preferences-dialog').wait_for(state='hidden')
    assert page.evaluate('window.fixture.theme') == 'dark'
    page.screenshot(path=str(output / 'dark-dashboard.png'), full_page=True)
    page.get_by_role('button',name='Preferences',exact=True).click()
    assert page.locator('select[name="theme"]').input_value() == 'dark'
    page.locator('select[name="policy"]').select_option('waitlist')
    page.get_by_role('button',name='Save preferences',exact=True).click()
    page.locator('#preferences-dialog').wait_for(state='hidden')


def test_remove_and_narrow_layout(dashboard_page):
    page = dashboard_page
    page.locator('[data-id="ccc"] [data-action="menu"]').click()
    page.locator('[data-id="ccc"] [data-action="remove"]').click()
    page.locator('#confirm-remove').click()
    page.locator('#confirm-dialog').wait_for(state='hidden')
    assert page.locator('.card').count() == 2
    page.set_viewport_size({"width":720,"height":700})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(output / "narrow.png"), full_page=True)


def test_course_grouping(dashboard_page):
    page = dashboard_page
    page.evaluate("""() => {
      const first = window.fixture.classes.find(p=>p.id==='bbb');
      first.url='https://classes.berkeley.edu/content/2026-fall-math-113-104-dis-104';
      first.group_key='2026-fall-math-113';
      const second=structuredClone(first);
      Object.assign(second,{id:'ddd',label:'MATH 113 DIS 105',section_id:'27744',url:'https://classes.berkeley.edu/content/2026-fall-math-113-105-dis-105'});
      window.fixture.classes.push(second);
      const otherTerm={...second,id:'eee',group_key:'2027-spring-math-113',url:second.url.replace('2026-fall','2027-spring')};
      if(courseGroups([first,second,otherTerm]).length!==2) throw Error('Cross-term grouping');
      const otherCourse={...second,id:'fff',group_key:'2026-fall-math-114',url:second.url.replace('math-113','math-114')};
      if(courseGroups([first,second,otherCourse]).length!==2) throw Error('Cross-course grouping');
      window.dispatchEvent(new Event('pywebviewready'));
    }""")
    page.locator('.course-group').wait_for()
    assert page.locator('.course-group .card').count() == 2
    assert page.locator('.course-group .lecture-context').count() == 1
    assert page.locator('.course-group .check-progress').count() == 2
    for selector in ('[data-group-control="start"]', '[data-course-move="later"]', '[data-group-add]'):
        button = page.locator('.course-group ' + selector)
        button.focus()
        page.evaluate("refresh()")
        assert button.evaluate('(node) => node === document.activeElement')
    page.get_by_role('button', name='Pause course', exact=True).click()
    page.wait_for_function("window.calls.some(p=>p.name==='pause' && p.values.id==='ddd')")
    page.get_by_role('button', name='＋ Add discussion', exact=True).click()
    assert page.locator('#class-form select[name="mode"]').input_value() == 'calcentral'
    assert page.locator('#class-form input[name="url"]').input_value() == ''
    page.get_by_role('button',name='Close class settings').click()
    page.set_viewport_size({'width':1180,'height':820})
    page.screenshot(path=str(output / 'grouped-courses.png'), full_page=True)


def test_empty_watchlist(dashboard_page):
    page = dashboard_page
    page.evaluate("window.fixture.classes=[];window.dispatchEvent(new Event('pywebviewready'))")
    page.locator('#empty').wait_for(state='visible')
    page.get_by_role('button',name='Add your first class').click()
    page.locator('#class-dialog').wait_for(state='visible')
    assert not page.locator('input[name="url"]').get_attribute('readonly')


def test_class_dialog_validation_and_failed_save(dashboard_page):
    page = dashboard_page
    page.locator('#add').click()
    page.locator('input[name="url"]').fill('invalid')
    page.mouse.click(10, 100)
    assert page.locator('#class-dialog').is_visible()
    assert not page.evaluate("window.calls.some(call => call.name === 'save')")
    page.locator('input[name="url"]').fill('https://classes.berkeley.edu/content/test')
    page.evaluate("""() => {
      window.pywebview.api.action = async (name, values) => {
        window.calls.push({name, values});
        await new Promise(resolve => setTimeout(resolve, 100));
        return {ok:false, error:'Fixture save failed'};
      };
    }""")
    page.mouse.click(10, 100)
    page.locator('#form-error').get_by_text('Fixture save failed', exact=True).wait_for()
    assert page.locator('#class-dialog').is_visible()
    assert page.evaluate("window.calls.filter(call => call.name === 'save').length") == 1
    page.get_by_role('button', name='Cancel', exact=True).click()
    assert page.locator('#class-dialog').is_hidden()


def test_drag_from_inside_dialog_does_not_save(dashboard_page):
    page = dashboard_page
    page.locator('[data-id="aaa"] [data-action="settings"]').click()
    box = page.locator('#class-dialog h2').bounding_box()
    page.mouse.move(box['x'] + 5, box['y'] + 5)
    page.mouse.down()
    page.mouse.move(10, 100)
    page.mouse.up()
    assert page.locator('#class-dialog').is_visible()
    assert not page.evaluate("window.calls.some(call => call.name === 'save')")


def test_meeting_details_inline(dashboard_page):
    page = dashboard_page
    page.evaluate("""() => {
      window.fixture.classes[0].meeting = 'Mo, We · 01:00 pm - 01:59 pm · Wheeler 202';
      window.fixture.classes[1].meeting = 'Tu · 10:00 am - 10:59 am · Evans <3>';
      window.fixture.classes[1].lecture.meeting = 'Fr · 02:00 pm - 02:59 pm · Evans 10';
      return refresh();
    }""")
    assert '2026 Fall · #26088 · Mo, We' in page.locator('[data-id="aaa"] .course-meta').inner_text()
    assert 'Evans <3>' in page.locator('[data-id="bbb"] .card-top .course-meta').inner_text()
    assert 'Evans 10' in page.locator('.lecture-context .course-meta').inner_text()
    assert 'Time / location TBD' in page.locator('[data-id="ccc"] .course-meta').inner_text()
    page.set_viewport_size({'width':720,'height':700})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(output / 'meeting-details.png'), full_page=True)
