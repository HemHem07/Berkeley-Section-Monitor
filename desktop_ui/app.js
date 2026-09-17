const $ = (selector, root = document) => root.querySelector(selector);
let state = null, pendingRemoval = null, toastTimer, polling = false;
const busy = new Set();
const activeStates = ['starting', 'running', 'signin', 'error', 'stopping'];
const policies = {seats: 'When a seat opens', waitlist: 'When the waitlist opens', changes: 'Any enrollment change', muted: 'Muted · dashboard only'};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(message) { const node = $('#toast'); node.textContent = message; node.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => node.hidden = true, 6500); }
function elapsed(value) { if (!value) return 'Not checked yet'; const seconds = Math.max(0, Math.floor((Date.now() - new Date(value)) / 1000)); return seconds < 60 ? `Checked ${seconds}s ago` : seconds < 3600 ? `Checked ${Math.floor(seconds/60)}m ago` : `Checked ${Math.floor(seconds/3600)}h ago`; }
function staleReading(item, checkedAt = item.checked_at) {
  if (!checkedAt) return false;
  const checked = Date.parse(checkedAt);
  // Allow a full extra interval (and at least one minute) for a slow check.
  const threshold = Math.max(120, Number(item.interval || 60) * 2);
  return !Number.isFinite(checked) || Date.now() / 1000 - checked / 1000 > threshold;
}
function freshnessNotice(item) {
  if (!item.status || !staleReading(item)) return '';
  const age = elapsed(item.checked_at).replace(/^Checked /, '');
  return `<div class="stale-notice" role="status"><strong>Stale availability</strong><span>Last successful check: ${escapeHTML(age)}. ${item.state === 'paused' ? 'Monitoring is paused. Use Check now or Start to refresh.' : 'These counts may have changed. Waiting for a successful check.'}</span></div>`;
}
function checkProgress(item) {
  let label, percent = 0, checking = false, waiting = false;
  if (item.state === 'paused') label = item.status ? 'Paused · no check scheduled' : 'Press Start for the first check';
  else if (item.state === 'stopped') label = 'Stopped · no check scheduled';
  else if (item.state === 'stopping') label = 'Stopping monitoring…';
  else if (item.state === 'signin') label = 'Waiting for CalNet / Duo sign-in';
  else if (item.check_phase === 'waiting' && Number.isFinite(item.next_check_at)) {
    waiting = true;
    const remaining = Math.max(0, Math.ceil(item.next_check_at - Date.now() / 1000));
    percent = Math.min(100, Math.max(0, 100 * (1 - remaining / item.interval)));
    const clock = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
    label = remaining ? `${item.state === 'error' ? 'Retry' : 'Next check'} in ${clock}` : 'Next check due…';
  } else {
    checking = true;
    label = item.state === 'starting' ? 'Running first check…' : 'Checking now…';
  }
  const canCheck = !busy.has(item.id) && (['paused','stopped'].includes(item.state) || (item.check_phase === 'waiting' && !['signin','stopping'].includes(item.state)));
  return `<div class="check-progress ${checking ? 'is-checking' : ''}"><div class="check-heading"><div class="check-label">${label}</div><button class="check-now" data-action="check_now" ${canCheck ? '' : 'disabled'} title="${['paused','stopped'].includes(item.state) ? 'Run one check and stay paused' : 'Check immediately, then restart the countdown'}">↻ Check now</button></div><div class="check-track" role="progressbar" aria-label="Next enrollment check" aria-valuetext="${label}" ${waiting ? `aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(percent)}"` : ''}><div class="check-fill" style="width:${percent}%"></div></div></div>`;
}
function renderActivity() {
  const events = state.activity || [], select = $('#activity-filter'), selected = select.value;
  const classes = new Map(state.classes.map(item => [item.id,item.label]));
  events.forEach(event => classes.set(event.class_id,event.label));
  const options = '<option value="">All classes</option>' + [...classes].map(([id,label]) => `<option value="${escapeHTML(id)}">${escapeHTML(label)}</option>`).join('');
  if (select.innerHTML !== options) {select.innerHTML = options; select.value = selected;}
  const filtered = events.filter(event => !select.value || event.class_id === select.value);
  $('#activity-count').textContent = `${events.length} event${events.length === 1 ? '' : 's'}`;
  $('#activity-empty').hidden = filtered.length !== 0;
  $('#activity-list').innerHTML = filtered.map(event => `<li><span class="activity-dot ${['error','signin','stopped'].includes(event.kind) ? 'attention' : event.kind === 'changed' ? 'change' : ''}"></span><div><strong>${escapeHTML(event.label)}</strong><p>${escapeHTML(event.message)}</p></div><time datetime="${escapeHTML(event.at)}" title="${escapeHTML(new Date(event.at).toLocaleString())}">${escapeHTML(new Date(event.at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}))}</time></li>`).join('');
}
function lecturePanel(item) {
  if (item.component !== 'DIS') return '';
  const lecture = item.lecture, s = lecture?.status;
  if (!s) return `<div class="lecture-context"><strong>Parent lecture · public data</strong><p>${escapeHTML(item.lecture_error || 'Lecture details load after the first successful discussion check.')}</p></div>`;
  return `<div class="lecture-context"><strong>${escapeHTML(lecture.label)}</strong><p class="course-meta">${escapeHTML(item.term)} · #${escapeHTML(lecture.section_id)} · ${escapeHTML(lecture.meeting || "Time / location TBD")}</p><p>Parent lecture · public data · ${escapeHTML(s.status_description)}</p><div class="lecture-counts"><span>Enrolled <b>${s.enrolled} / ${s.capacity}</b></span><span>Waitlist <b>${s.waitlisted} / ${s.waitlist_capacity ?? '?'}</b></span></div><p>${elapsed(lecture.checked_at)}${item.state === 'paused' || item.lecture_error ? ' · last known counts' : ''}</p>${item.lecture_error ? `<p class="form-error">${escapeHTML(item.lecture_error)}</p>` : ''}<p>Context only. This card’s alerts track the discussion.</p></div>`;
}
function notificationLabel(item, policy) {
  const channel = state.notifications?.channel || 'Not configured';
  const delivery = item.notification_delivery;
  return `${escapeHTML(channel)} · ${policies[policy] || 'Default notifications'}${delivery ? `<br><span class="delivery-result">Last enrollment alert: ${delivery.state === 'delivered' ? 'delivered' : 'delivery failed'} · ${escapeHTML(new Date(delivery.at).toLocaleString())}</span>` : ''}`;
}
function card(item, grouped, groups) {
  const siblings = grouped ? groups.find(group => group.some(p => p.id === item.id)) : groups.map(group => group[0]);
  const s = item.status, active = activeStates.includes(item.state), paused = !active;
  const waitlistStatus = s && !s.is_open && /waitlist/i.test(s.status_description);
  const waitlist = waitlistStatus && (s.waitlist_capacity === null || s.waitlisted < s.waitlist_capacity);
  const open = s?.is_open;
  const stale = staleReading(item);
  const seats = s ? Math.max(0, s.capacity - s.enrolled) : 0;
  const title = !s ? 'Awaiting first check' : open ? (seats ? `${seats} seat${seats === 1 ? '' : 's'} available` : 'Open enrollment') : waitlist ? 'Waitlist available' : waitlistStatus ? 'Waitlist full' : s.status_description || 'Closed';
  const tone = paused || stale ? 'paused' : open ? 'open' : waitlist ? 'waitlist' : 'closed';
  const labels = {paused:'Paused', starting:'Starting', running:'Monitoring', signin:'Sign-in needed', error:'Check failed', stopped:'Stopped', stopping:'Stopping'};
  const policy = item.notification === 'default' || !item.notification ? state.default_notification : item.notification;
  const count = (value, capacity) => s ? `${escapeHTML(value)} <span>/ ${capacity === null ? '?' : escapeHTML(capacity)}</span>` : '—';
  const percent = (value, capacity) => capacity > 0 ? Math.min(100, Math.max(0, value / capacity * 100)) : 0;
  return `<article class="card tone-${tone}" data-id="${item.id}" aria-label="${escapeHTML(item.label)}">
    <div class="card-top"><div><h3 class="course-label">${escapeHTML(item.label)}</h3><p class="course-meta">${escapeHTML(item.term)} · #${escapeHTML(item.section_id)} · ${escapeHTML(item.meeting || "Time / location TBD")}</p></div><span class="source">${item.mode === 'calcentral' ? 'Live' : 'Public'}</span></div>
    <div class="availability"><div class="availability-main">${escapeHTML(title)}</div><div class="availability-caption">${stale && s ? 'Last known availability · data is stale' : paused && s ? 'Last known availability · monitoring paused' : item.state === 'error' || item.state === 'signin' ? 'Last known availability · check needs attention' : open ? 'Available for immediate enrollment' : waitlist ? 'Section full · join the waitlist' : !s ? 'Start monitoring to get availability' : 'No immediate seats available'}</div></div>
    ${freshnessNotice(item)}
    <div class="counts"><div><div class="count-name">Enrolled</div><div class="count-value">${count(s?.enrolled,s?.capacity)}</div><div class="meter"><div class="meter-fill" style="width:${s ? percent(s.enrolled,s.capacity) : 0}%"></div></div></div><div><div class="count-name">Waitlist</div><div class="count-value">${count(s?.waitlisted,s?.waitlist_capacity)}</div><div class="meter"><div class="meter-fill" style="width:${s ? percent(s.waitlisted,s.waitlist_capacity) : 0}%"></div></div></div></div>
    <div class="card-info"><div>${elapsed(item.checked_at)} · Every ${escapeHTML(item.interval)}s</div>${checkProgress(item)}<div class="notification-label"><span aria-hidden="true">♧</span><span>${notificationLabel(item, policy)}</span></div></div>
    ${!open && !waitlist && s && s.waitlist_capacity > s.waitlisted ? '<p class="source-status-note">Reported closed. Unused waitlist capacity does not mean the waitlist is accepting students.</p>' : ''}
    ${grouped ? '' : lecturePanel(item)}
    ${item.error ? `<div class="card-notice">${escapeHTML(item.error)}${item.state === 'signin' ? '<button data-action="focus_signin">Open sign-in window ↗</button>' : ''}</div>` : ''}
    <div class="card-bottom"><span class="state state-${item.state}"><span class="state-dot"></span>${labels[item.state] || 'Paused'}</span><div class="card-actions"><button data-action="${active ? 'pause' : 'start'}" ${busy.has(item.id) || item.state === 'stopping' ? 'disabled' : ''}>${active ? 'Ⅱ Pause' : '▶ Start'}</button><button data-action="settings" ${busy.has(item.id) ? 'disabled' : ''}>Settings</button><div class="card-menu"><button data-action="menu" aria-label="More actions for ${escapeHTML(item.label)}" aria-expanded="false">···</button><div class="menu" hidden><button data-action="move_earlier" ${siblings[0].id === item.id ? 'disabled' : ''} ${grouped ? 'title="Reorder discussions within this course"' : ''}>← Move earlier</button><button data-action="move_later" ${siblings[siblings.length - 1].id === item.id ? 'disabled' : ''} ${grouped ? 'title="Reorder discussions within this course"' : ''}>Move later →</button><button data-action="open_class">Open class page ↗</button><button data-action="remove">Remove class</button></div></div></div></div></article>`;

}
function courseGroups(items) {
  const groups = new Map();
  for (const item of items) {
    const key = item.group_key || item.id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return [...groups.values()];
}
function courseCard(items, groups) {
  if (items.length === 1) return card(items[0], false, groups);
  const first = items[0];
  const position = groups.findIndex(group => group.some(p => p.id === first.id));
  const label = first.label.replace(/\s*(?:·\s*)?(?:Discussion|DIS)\s*\d+.*$/i, '').trim();
  const opened = items.filter(p => p.status?.is_open && p.state === 'running' && !p.error && !staleReading(p));
  const lectures = new Map();
  for (const item of items) {
    if (!item.lecture?.status) continue;
    const key = item.lecture.section_id;
    const previous = lectures.get(key);
    if (!previous || Date.parse(item.lecture.checked_at) > Date.parse(previous.lecture.checked_at)) lectures.set(key, item);
  }
  return `<section class="course-group" aria-label="${escapeHTML(label)} discussions">
    <div class="course-group-heading"><div><h3>${escapeHTML(label)}</h3><p>${escapeHTML(first.term)} · ${items.length} acceptable discussions</p></div><div class="actions"><button data-group-control="start" data-group-id="${first.id}">Start course</button><button data-group-control="pause" data-group-id="${first.id}">Pause course</button><button data-group-add="${first.id}">＋ Add discussion</button></div></div>
    <div class="course-order actions"><button data-course-move="earlier" data-group-id="${first.id}" ${position === 0 ? 'disabled' : ''}>← Move course earlier</button><button data-course-move="later" data-group-id="${first.id}" ${position === groups.length - 1 ? 'disabled' : ''}>Move course later →</button></div>
    <p class="course-group-summary">${opened.length ? `${opened.length} discussion${opened.length === 1 ? '' : 's'} reported open` : 'Watching your selected discussions'} · Each section keeps its own alert preference.</p>
    ${[...lectures.values()].map(lecturePanel).join('') || lecturePanel(first)}
    <div class="group-discussions">${items.map(item => card(item, true, groups)).join('')}</div>
  </section>`;
}
function render() {
  applyTheme($('#preferences-dialog').open ? $('#preferences-form').elements.theme.value : state.theme || 'system');
  const items = state.classes;
  $('#total').textContent = items.length;
  $('#running').textContent = items.filter(p => ['starting','running'].includes(p.state)).length;
  $('#attention').textContent = items.filter(p => ['signin','error','stopped'].includes(p.state)).length;
  $('#tray-status').textContent = state.tray_available ? 'Close the window to keep monitoring in the tray.' : 'Keep this window open while monitoring.';
  $('#hide').disabled = !state.tray_available;
  $('#notification-warning').hidden = !!state.notifications?.configured;
  const delivery = state.notifications;
  $('#delivery-channel').textContent = delivery?.configured ? `${delivery.channel} configured` : 'Notifications not configured';
  $('#delivery-details').textContent = delivery?.channel === 'Discord' ? `Discord mentions: ${delivery.mentions ? 'enabled' : 'off'}. Individual classes can still mute enrollment alerts.` : 'Choose enrollment alert preferences below. Webhooks take precedence over email when both are configured.';
  $('#empty').hidden = items.length !== 0;
  $('#start-all').disabled = !items.length || items.every(p => activeStates.includes(p.state));
  $('#pause-all').disabled = !items.some(p => activeStates.includes(p.state));
  // Preserve keyboard focus and an open action menu across live refreshes.
  const focused = document.activeElement;
  const groupFocus = ['data-group-id', 'data-group-control', 'data-course-move', 'data-group-add']
    .filter(attr => focused?.hasAttribute(attr))
    .map(attr => `[${attr}="${CSS.escape(focused.getAttribute(attr))}"]`).join('');
  const focusId = focused?.closest('.card')?.dataset.id, focusAction = focused?.dataset.action;
  const openMenu = [...document.querySelectorAll('.menu:not([hidden])')].map(n => n.closest('.card').dataset.id);
  const groups = courseGroups(items);
  $('#cards').innerHTML = groups.map(group => courseCard(group, groups)).join('');
  for (const id of openMenu) { const node = $(`[data-id="${id}"] .menu`); if (node) {node.hidden = false; node.previousElementSibling.setAttribute('aria-expanded','true');} }
  if (focusId && focusAction) $(`[data-id="${focusId}"] [data-action="${focusAction}"]`)?.focus({preventScroll:true});
  if (groupFocus) $(groupFocus)?.focus({preventScroll:true});
  renderActivity();
}
async function refresh() {
  if (polling || !window.pywebview?.api) return;
  polling = true;
  try { state = await window.pywebview.api.snapshot(); render(); }
  catch { $('#tray-status').textContent = 'Connection interrupted. Reopen the dashboard to reconnect.'; }
  finally { polling = false; }
}
async function action(name, values = {}) {
  if (!window.pywebview?.api) throw new Error('Open this interface through Start Monitor, not directly in a browser.');
  const result = await window.pywebview.api.action(name, values);
  if (!result.ok) throw new Error(result.error);
  await refresh();
  return result;
}
async function run(name, values = {}) { try { await action(name, values); } catch (error) { toast(error.message); } }
let searchVersion = 0, searchPage = null, searchSections = [];
function resetSearch() {
  searchVersion++; searchPage = null; searchSections = [];
  $('#search-results').replaceChildren(); $('#search-status').textContent = '';
  $('#section-choice').hidden = true; $('#section-details').textContent = '';
  $('#more-courses').hidden = true; $('#search-courses').disabled = false;
  $('#search-courses').textContent = 'Search';
}
async function searchClasses(more = false) {
  const query = $('#course-query').value.trim();
  const previous = more ? searchPage : null;
  if (!more) {
    resetSearch(); $('#class-form').elements.url.value = ''; $('#class-form').elements.parent.value = '';
  }
  const version = ++searchVersion;
  $('#search-courses').disabled = true; $('#more-courses').hidden = true;
  $('#search-courses').textContent = 'Searching…'; $('#search-status').textContent = 'Finding the newest published term…';
  try {
    const result = await action('search_courses', {query, term_id:previous?.term_id || '', page:previous ? previous.page + 1 : 0});
    if (version !== searchVersion) return;
    searchPage = result;
    $('#search-status').textContent = result.courses.length ? `${result.courses[0].term} · Newest matching term. Choose an offering below.` : 'No matching offerings on this page. Try a more specific title or course code.';
    for (const course of result.courses) {
      const button = document.createElement('button'); button.type = 'button';
      const title = document.createElement('strong'); title.textContent = `${course.label} · ${course.title}`;
      const details = document.createElement('span'); details.textContent = course.details;
      button.append(title, details); button.onclick = () => chooseOffering(course);
      $('#search-results').append(button);
    }
    $('#more-courses').hidden = !result.more;
  } catch (error) {
    if (version === searchVersion) $('#search-status').textContent = error.message;
  } finally {
    if (version === searchVersion) { $('#search-courses').disabled = false; $('#search-courses').textContent = 'Search'; }
  }
}
async function chooseOffering(course) {
  const version = ++searchVersion;
  $('#search-courses').disabled = false; $('#search-courses').textContent = 'Search';
  const form = $('#class-form'); form.elements.url.value = ''; form.elements.parent.value = '';
  $('#section-choice').hidden = true; $('#section-details').textContent = '';
  $('#search-status').textContent = `Loading sections for ${course.label}…`;
  try {
    const result = await action('course_sections', {url:course.url});
    if (version !== searchVersion) return;
    searchSections = result.sections;
    $('#course-section').replaceChildren(new Option('Choose a lecture or associated section…', ''),
      ...searchSections.map((section, index) => new Option(`${section.label}${section.details ? ' · ' + section.details : ''}`, String(index))));
    $('#section-choice').hidden = false;
    $('#search-status').textContent = `${result.term} · ${course.title}`;
    if (searchSections.length === 1) { $('#course-section').value = '0'; $('#course-section').onchange(); }
    $('#course-section').focus();
  } catch (error) {
    if (version === searchVersion) $('#search-status').textContent = error.message;
  }
}
$('#search-courses').onclick = () => searchClasses();
$('#more-courses').onclick = () => searchClasses(true);
$('#course-query').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); searchClasses(); } };
$('#course-query').oninput = () => {
  resetSearch(); $('#class-form').elements.url.value = ''; $('#class-form').elements.parent.value = '';
};
$('#course-section').onchange = () => {
  const value = $('#course-section').value;
  const section = value === '' ? null : searchSections[Number(value)];
  $('#class-form').elements.url.value = section?.url || '';
  $('#class-form').elements.parent.value = section?.parent || '';
  $('#section-details').textContent = section?.details || '';
};
$('#class-form').elements.url.oninput = () => {
  if (searchSections.length) $('#class-form').elements.parent.value = '';
  resetSearch();
};
function edit(item = null) {
  resetSearch(); $('#course-query').value = ''; $('#class-search').hidden = !!item;
  const form = $('#class-form'); form.reset();
  form.elements.id.value = item?.id || '';
  form.elements.url.value = item?.url || ''; form.elements.url.readOnly = !!item;
  form.elements.mode.value = item?.mode || 'public';
  form.elements.interval.value = item?.interval || 60;
  form.elements.parent.value = item?.parent || '';
  form.elements.notification.value = item?.notification || 'default';
  $('#calcentral-fields').hidden = form.elements.mode.value !== 'calcentral';
  $('#form-title').textContent = item ? 'Class settings' : 'Add a class';
  $('#save-class').textContent = item ? 'Save changes' : 'Add to dashboard';
  $('#form-error').hidden = true;
  $('#class-dialog').showModal();
}
$('#add').onclick = $('#add-empty').onclick = () => edit();
$('#start-all').onclick = () => run('start_all');
$('#pause-all').onclick = () => run('pause_all');
$('#hide').onclick = () => run('hide');
$('#quit').onclick = () => run('quit');
$('#find-class').onclick = () => run('find_class');
$('#activity-filter').onchange = renderActivity;
$('#class-form').elements.mode.onchange = event => $('#calcentral-fields').hidden = event.target.value !== 'calcentral';
document.querySelectorAll('[data-close]').forEach(button => button.onclick = () => document.getElementById(button.dataset.close).close());
$('#class-form').onsubmit = async event => {
  event.preventDefault(); const button = $('#save-class'); const original = button.textContent;
  if (button.disabled) return;
  button.disabled = true; button.textContent = 'Saving…'; $('#form-error').hidden = true;
  try { await action('save', Object.fromEntries(new FormData(event.target))); $('#class-dialog').close(); toast('Class saved to your dashboard.'); }
  catch(error) { $('#form-error').textContent = error.message; $('#form-error').hidden = false; }
  finally { button.disabled = false; button.textContent = original; }
};
let classBackdropPress = false;
function outsideDialog(event, dialog) {
  const rect = dialog.getBoundingClientRect();
  return event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom);
}
$('#class-dialog').addEventListener('pointerdown', event => { classBackdropPress = outsideDialog(event, $('#class-dialog')); });
$('#class-dialog').addEventListener('click', event => {
  if (classBackdropPress && outsideDialog(event, $('#class-dialog')) && !$('#save-class').disabled) $('#class-form').requestSubmit();
  classBackdropPress = false;
});
$('#cards').onclick = async event => {
  const courseMove = event.target.closest('[data-course-move]');
  if (courseMove) {
    await run(`move_${courseMove.dataset.courseMove}`, {id:courseMove.dataset.groupId, whole_group:true});
    return;
  }
  const groupAdd = event.target.closest('[data-group-add]');
  if (groupAdd) {
    const item = state.classes.find(p => p.id === groupAdd.dataset.groupAdd);
    edit();
    const form = $('#class-form');
    form.elements.mode.value = item.mode;
    form.elements.interval.value = item.interval;
    form.elements.parent.value = item.parent || '';
    form.elements.notification.value = item.notification || 'default';
    $('#calcentral-fields').hidden = item.mode !== 'calcentral';
    $('#form-title').textContent = 'Add an acceptable discussion';
    return;
  }
  const groupControl = event.target.closest('[data-group-control]');
  if (groupControl) {
    const items = courseGroups(state.classes).find(group => group.some(p => p.id === groupControl.dataset.groupId));
    groupControl.disabled = true;
    try { for (const item of items) await action(groupControl.dataset.groupControl, {id:item.id}); }
    catch(error) { toast(error.message); }
    finally { await refresh(); }
    return;
  }
  const button = event.target.closest('button[data-action]'); if (!button) return;
  const id = button.closest('.card').dataset.id, item = state.classes.find(p => p.id === id), name = button.dataset.action;
  if (name === 'settings') return edit(item);
  if (name === 'menu') { const menu = button.nextElementSibling; const opening = menu.hidden; closeCardMenus(); menu.hidden = !opening; button.setAttribute('aria-expanded', opening); return; }
  if (name === 'remove') { pendingRemoval = id; $('#confirm-text').textContent = `${item.label} will stop monitoring and be removed from your dashboard.`; $('#confirm-dialog').showModal(); return; }
  busy.add(id); button.disabled = true;
  try { await action(name, {id}); } catch(error) {toast(error.message);} finally {busy.delete(id); await refresh();}
};
$('#confirm-remove').onclick = async () => { const button = $('#confirm-remove'); button.disabled = true; try {await action('remove', {id:pendingRemoval}); $('#confirm-dialog').close();} catch(error) {toast(error.message);} finally {button.disabled = false;} };
$('#preferences').onclick = () => { $('#preferences-form').elements.theme.value = state?.theme || 'system'; $('#preferences-form').elements.policy.value = state?.default_notification || 'seats'; $('#discord-webhook').value = ''; $('#discord-webhook').type = 'password'; $('#show-webhook').textContent = 'Show entered URL'; $('#discord-user-id').value = state?.notifications?.user_id || ''; $('#discord-result').textContent = '';  $('#preferences-error').hidden = true; $('#preferences-dialog').showModal(); };
let savingPreferences = false;
async function savePreferences() {
  if (savingPreferences) return;
  savingPreferences = true;
  const form = $('#preferences-form'), button = $('button[type="submit"]', form);
  button.disabled = true;
  try {
    await action('theme', {theme:form.elements.theme.value});
    await action('default', {policy:form.elements.policy.value});
    $('#preferences-dialog').close(); toast('Preferences saved.');
  } catch(error) { $('#preferences-error').textContent = error.message; $('#preferences-error').hidden = false; }
  finally { button.disabled = false; savingPreferences = false; }
}
$('#preferences-form').onsubmit = event => { event.preventDefault(); savePreferences(); };
const systemTheme = matchMedia('(prefers-color-scheme: dark)');
function applyTheme(theme) { document.documentElement.dataset.theme = theme === 'system' ? (systemTheme.matches ? 'dark' : 'light') : theme; }
systemTheme.addEventListener('change', () => applyTheme(state?.theme || 'system'));
$('#preferences-form').elements.theme.onchange = event => applyTheme(event.target.value);
let backdropPress = false;
$('#preferences-dialog').addEventListener('pointerdown', event => { backdropPress = outsideDialog(event, $('#preferences-dialog')); });
$('#preferences-dialog').addEventListener('click', event => { if (backdropPress && outsideDialog(event, $('#preferences-dialog'))) savePreferences(); backdropPress = false; });
$('#preferences-dialog').addEventListener('close', () => applyTheme(state?.theme || 'system'));

window.addEventListener('pywebviewready', refresh);
window.addEventListener('focus', refresh);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
function closeCardMenus(restoreFocus = false) {
  document.querySelectorAll('.card-menu .menu:not([hidden])').forEach(menu => {
    menu.hidden = true;
    menu.previousElementSibling.setAttribute('aria-expanded', 'false');
    if (restoreFocus) menu.previousElementSibling.focus();
  });
}
document.addEventListener('click', event => {
  if (!event.target.closest('.card-menu')) closeCardMenus();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && document.querySelector('.card-menu .menu:not([hidden])')) {
    closeCardMenus(true);
    event.preventDefault();
  }
});
refresh();
setInterval(refresh, 1000);

$('#preferences-dialog').addEventListener('close', () => { $('#discord-webhook').value = ''; });
$('#show-webhook').onclick = () => {
  const field = $('#discord-webhook'); field.type = field.type === 'password' ? 'text' : 'password';
  $('#show-webhook').textContent = field.type === 'password' ? 'Show entered URL' : 'Hide entered URL';
};
async function discordAction(test) {
  const buttons = [$('#save-discord'), $('#test-discord')];
  const result = $('#discord-result');
  if (test && ($('#discord-webhook').value.trim() || $('#discord-user-id').value.trim() !== (state?.notifications?.user_id || ''))) {
    result.textContent = 'Save your Discord changes before sending a test.'; return;
  }
  buttons.forEach(button => button.disabled = true);
  result.textContent = test ? 'Sending test notification…' : 'Saving Discord settings…';
  try {
    await action(test ? 'test_discord' : 'save_discord', test ? {} : {webhook:$('#discord-webhook').value, user_id:$('#discord-user-id').value});
    if (!test) { $('#discord-webhook').value = ''; $('#discord-webhook').type = 'password'; $('#show-webhook').textContent = 'Show entered URL'; }
    result.textContent = test ? 'Discord accepted the test notification. Check your channel.' : 'Discord settings saved and applied.';
  } catch(error) { result.textContent = error.message; }
  finally { buttons.forEach(button => button.disabled = false); }
}
$('#save-discord').onclick = () => discordAction(false);
$('#test-discord').onclick = () => discordAction(true);

// Keep native details semantics; animate height before committing the closed state.
const activityPanel = $('.activity-panel');
let activityAnimation = null, activityExpanded = activityPanel.open;
activityPanel.querySelector('summary').addEventListener('click', event => {
  event.preventDefault();
  const from = activityPanel.getBoundingClientRect().height;
  activityExpanded = !activityExpanded;
  activityAnimation?.cancel();
  activityPanel.style.height = '';
  activityPanel.open = true;
  const style = getComputedStyle(activityPanel);
  const collapsed = activityPanel.querySelector('summary').getBoundingClientRect().height +
    parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
  const to = activityExpanded ? activityPanel.getBoundingClientRect().height : collapsed;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) { activityPanel.open = activityExpanded; return; }
  activityAnimation = activityPanel.animate([{height:`${from}px`},{height:`${to}px`}], {duration:240,easing:'cubic-bezier(.2,.7,.2,1)'});
  activityAnimation.onfinish = () => { activityPanel.open = activityExpanded; activityAnimation = null; };
});
