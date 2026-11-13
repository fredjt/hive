
let departments=[];
let currentDept=null;
var activeView='dashboard';
// panelRefreshers — registered by each showX() via startAutoRefresh, fired by
// SSE event handlers via refreshActive(panelKey). Keyed by activeView value.
var panelRefreshers={};
let connected=false;
var hiveStartedAt=0;       // Unix seconds: when the Hive process started (from /api/status)
var sseState='disconnected'; // 'connecting' | 'connected' | 'disconnected'

const RECENT_KEY='company-board-recent';
function getRecent(){try{return JSON.parse(localStorage.getItem(RECENT_KEY))||[]}catch{return[]}}
function addRecent(p){const r=getRecent().filter(x=>x!==p);r.unshift(p);localStorage.setItem(RECENT_KEY,JSON.stringify(r.slice(0,5)))}

async function api(path,opts){const r=await fetch('/api/'+path,opts);return r.json()}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function saveFocus(){var el=document.activeElement;if(el&&(el.tagName==='INPUT'||el.tagName==='TEXTAREA'||el.tagName==='SELECT')){return{id:el.id,val:el.value,start:el.selectionStart,end:el.selectionEnd}}return null}
function restoreFocus(f){if(!f||!f.id)return;var el=document.getElementById(f.id);if(el){el.focus();if(f.val!=null)el.value=f.val;try{if(f.start!=null)el.setSelectionRange(f.start,f.end)}catch(e){}}}
function localTime(s){if(!s)return '';try{var d=new Date(s);if(isNaN(d))return esc(s);return d.toLocaleString()}catch(e){return esc(s)}}
function localDate(s){if(!s)return '';try{var d=new Date(s);if(isNaN(d))return esc(s);return d.toLocaleDateString()}catch(e){return esc(s)}}
function localShort(s){if(!s)return '';try{var d=new Date(s);if(isNaN(d))return esc(s);return d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}catch(e){return esc(s)}}
// T3 (0.22): relative age for a snapshot's freshness ('41m ago') — the
// Config panel renders it next to the doctor / llm-test badges so a
// stale dump does not read as a current one.
function ageAgo(ms){
  if(ms==null||isNaN(ms))return '';
  var s=Math.max(0,Math.round(ms/1000));
  if(s<5)return 'just now';
  if(s<60)return s+'s ago';
  var m=Math.floor(s/60);
  if(m<60)return m+'m ago';
  var h=Math.floor(m/60);
  if(h<48)return h+'h '+(m%60)+'m ago';
  return Math.floor(h/24)+'d '+(h%24)+'h ago';
}
function df(l,v){return '<div class="detail-field"><div class="label">'+l+'</div><div class="value">'+(v||'-')+'</div></div>'}
function bounceColor(n){var p=Math.min(n/5*100,100);return n===0?'#8b949e':n<=2?'#d29922':n<=4?'#f85149':'#da3633'}
function openModal(){document.getElementById('modal-overlay').classList.add('open')}
function closeModal(){document.getElementById('modal-overlay').classList.remove('open')}
function isUserTyping(){var el=document.activeElement;return el&&(el.tagName==='INPUT'||el.tagName==='TEXTAREA'||el.tagName==='SELECT')}
function saveScroll(){
  // Capture window + every [id] element currently scrolled away from origin.
  // Also note whether each scrollable element was glued to the bottom — that
  // signal drives "follow-the-tail" behavior in chat/logs/spawn-stream after
  // the render swaps in new content.
  return {
    x: window.scrollX, y: window.scrollY,
    els: Array.from(document.querySelectorAll('[id]')).filter(function(e){
      return e.scrollTop>0 || e.scrollLeft>0 ||
             (e.scrollHeight > e.clientHeight + 4);  // also track tail-followable empties
    }).map(function(e){
      return {
        id: e.id,
        top: e.scrollTop, left: e.scrollLeft,
        oldScrollHeight: e.scrollHeight,
        atBottom: (e.scrollHeight - e.clientHeight - e.scrollTop) <= 16
      };
    })
  };
}
function restoreScroll(s){
  if(!s) return;
  window.scrollTo(s.x, s.y);
  s.els.forEach(function(e){
    var el = document.getElementById(e.id);
    if(!el) return;
    if(e.atBottom){
      // User was following the tail — snap to the new bottom so freshly-
      // appended content stays in view.
      el.scrollTop = el.scrollHeight;
    } else {
      // Events are rendered newest-first (top→bottom). New events are
      // prepended at the top, pushing older content down.
      //
      // If the user was near the top (scroll position within 16px of 0),
      // keep them at the top so new events stay visible.  If the user is
      // scrolled down, preserve the distance from the bottom so their view
      // of older events stays fixed — the scrollbar moves down with the
      // new content.
      var distFromBottom = e.oldScrollHeight - el.clientHeight - e.top;
      if (e.top <= 16) {
        // User was at the top — stay at the top.
        el.scrollTop = 0;
      } else {
        // User is scrolled down — preserve distance from the bottom.
        el.scrollTop = el.scrollHeight - el.clientHeight - distFromBottom;
      }
      el.scrollLeft = e.left;
    }
  });
}

// Per-panel last-rendered fingerprint. If the data we just fetched is
// byte-identical to what's already on screen, the redraw is a no-op and
// scroll position is guaranteed untouched.
var lastSig = {};

// Event-driven panel refresh. Replaces the old 30s startAutoRefresh polling.
// fetcher() returns a Promise resolving to the panel's data; renderer(data)
// performs the actual DOM update synchronously. Both scroll preservation
// AND fingerprint dedup happen here so call sites stay one line.
async function refresh(panelKey, fetcher, renderer){
  if(isUserTyping()) return;
  var data;
  try { data = await fetcher(); } catch(e) { return; }
  var sig;
  try { sig = JSON.stringify(data); } catch(e) { sig = String(Math.random()); }
  if(lastSig[panelKey] === sig) return;
  lastSig[panelKey] = sig;
  var s = saveScroll();
  try { renderer(data); } finally { restoreScroll(s); }
}

// Invalidate a panel's fingerprint so the next refresh() call always renders.
// Used when the user navigates away and back to the same panel — the DOM
// may have been swapped out, so the cached sig is meaningless.
function invalidatePanel(panelKey){ delete lastSig[panelKey]; }

// Existing call-site compatibility shims. The old 30s polling is gone; what
// each showX() actually does is register its refresh closure here, and SSE
// event handlers fire it via refreshActive(panelKey). isUserTyping + scroll
// preservation happen in the wrapper.
function startAutoRefresh(fn){ panelRefreshers[activeView] = fn; }
function stopAutoRefresh(){
  // Clear the per-panel fingerprint cache so re-entering a panel always
  // does a fresh render (the DOM was wiped during navigation).
  lastSig = {};
}

// Fire the registered refresher for one panel, preserving scroll. Called by
// SSE handlers. Closures registered via startAutoRefresh are typically a
// fetch+render monolith — they can't be deduped via JSON fingerprint here;
// for that, use refresh(panelKey, fetcher, renderer) directly in the showX.
//
// Serialized per panel: if a refresh is in flight for this panelKey and
// another SSE event triggers one, we record a pending bit and let the
// in-flight one re-run once when it finishes. Without serialization, two
// concurrent refreshes race — the SECOND saveScroll() can fire in the gap
// between the first refresh's innerHTML write (where scrollY momentarily
// reads 0 because the panel has just been gutted) and the first
// restoreScroll. That captures y=0 as "the user's scroll position" and the
// second restoreScroll then snaps the panel back to the top on every
// event, which is exactly what the user was seeing on the activity panel.
var refreshActiveInFlight = {};
var refreshActivePending  = {};
async function refreshActive(panelKey){
  if(activeView !== panelKey) return;
  var fn = panelRefreshers[panelKey];
  if(!fn) return;
  if(isUserTyping()) return;
  if(refreshActiveInFlight[panelKey]){
    refreshActivePending[panelKey] = true;
    return;
  }
  refreshActiveInFlight[panelKey] = true;
  try {
    do {
      refreshActivePending[panelKey] = false;
      if(activeView !== panelKey) break;
      // When the activity panel is showing a spawn stream (or global stream),
      // the inner renderers handle their own scroll preservation.  Wrapping
      // them here would capture the pre-render position and overwrite the
      // inner restore, causing the scrollbar to jump back to the top.
      if(panelKey==='activity' && activityStream && activityStream.spawnId){
        var r = fn();
        if(r && typeof r.then === 'function') await r;
      } else if(panelKey==='activity' && globalStream && globalStream.open){
        var r = fn();
        if(r && typeof r.then === 'function') await r;
      } else {
        var s = saveScroll();
        try {
          var r2 = fn();
          if(r2 && typeof r2.then === 'function') await r2;
        } finally {
          restoreScroll(s);
        }
      }
    } while(refreshActivePending[panelKey]);
  } finally {
    refreshActiveInFlight[panelKey] = false;
  }
}

function toggleMore(e){
  e.stopPropagation();
  var dd=document.getElementById('more-dropdown');
  dd.classList.toggle('open');
  function closeHandler(){dd.classList.remove('open');document.removeEventListener('click',closeHandler)}
  if(dd.classList.contains('open')){setTimeout(function(){document.addEventListener('click',closeHandler)},0)}
}
function closeMore(){document.getElementById('more-dropdown').classList.remove('open')}

let evtSource=null;
let lastEvent=null;
// T2 (0.21): the client shows what the transport is actually doing.
// EventSource's onerror is the ONLY signal the browser offers (it never
// says WHY), so the SPA manages its own retry: while onopen never fires,
// reconnect with backoff 5 s → 10 s → 30 s (cap) and show 'reconnecting…'
// (distinct from the flat 5 s loop on 0.20, where the indicator could
// only ever read connecting/disconnected). lastByteAt tracks the last
// moment bytes were observed flowing (open or a named event — heartbeats
// are SSE comments the browser does not surface), so 'live' means
// 'bytes arriving', not 'a socket once opened'.
var SSE_RETRY_INITIAL_MS=5000;
var SSE_RETRY_MAX_MS=30000;
var sseRetryMs=SSE_RETRY_INITIAL_MS;
var sseReconnectScheduled=false;
var sseEstablishedOnce=false;
var lastByteAt=0;

function connectSSE(){
  if(evtSource){try{evtSource.close()}catch{}}
  sseReconnectScheduled=false;
  sseState='connecting';
  evtSource=new EventSource('/api/events');
  evtSource.onopen=function(){
    sseState='connected';
    lastByteAt=Date.now();
    sseRetryMs=SSE_RETRY_INITIAL_MS;
    api('status').then(function(r){hiveStartedAt=r.started_at||0;renderLiveIndicator()});
    renderLiveIndicator();
    // T2 (0.22): SSE (re)established — the push is the live path, but
    // lines written while we were DISCONNECTED were never pushed to us
    // (the server's push cursor moves on regardless of who is listening).
    // Catch up ONCE over the wire from the saved cursor, then follow the
    // pushes. The very first connect does not need it: the viewer's own
    // open fetch already seeded the full history.
    if(sseEstablishedOnce){
      if(activityStream&&activityStream.spawnId)refreshSpawnStream();
      else if(globalStream&&globalStream.open)refreshGlobalStream();
    }
    sseEstablishedOnce=true;
  };

  // ── Ticket lifecycle ────────────────────────────────────────────────
  // Dashboard (all-dept tickets overview) and Board (single dept's columns)
  // are the only views that care. Use refresh() so unchanged data short-
  // circuits and scroll position is preserved synchronously.
  evtSource.addEventListener('ticket-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='dashboard'){
      refresh('dashboard',
        function(){ return api('departments'); },
        function(d){ departments=d; renderDashboard(); });
    } else if(activeView==='board' && currentDept && lastEvent.department===currentDept){
      refresh('board:'+currentDept,
        function(){ return api('departments/'+currentDept+'/tickets'); },
        function(t){ boardTickets=t; renderBoardColumns(); });
    }
    // Sprint / verification panels read ticket state too; refresh if visible.
    if(activeView==='sprint')       refreshActive('sprint');
    if(activeView==='verification') refreshActive('verification');
    // T2 (0.21): the Git view renders worktrees and recent commits —
    // ticket state moves (a ticket going in-progress/done) move them
    // too, and 0.20 never refreshed the panel from a ticket event.
    if(activeView==='git')          refreshActive('git');
  });

  // ── Chat ────────────────────────────────────────────────────────────
  evtSource.addEventListener('chat-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='chat') refreshActive('chat');
  });

  // ── Generic log directory changes (mostly the cycle logs) ───────────
  // Drives the Logs panel + the Activity cycle table.
  evtSource.addEventListener('log-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='logs')     refreshActive('logs');
    if(activeView==='activity') refreshActive('activity');
  });

  // ── Targeted cycle log writes (cycles.jsonl, spawns.log, sprint-lifecycle) ─
  evtSource.addEventListener('cycle-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='activity') refreshActive('activity');
    if(activeView==='logs')     refreshActive('logs');
    if(activeView==='rounds')   refreshActive('rounds');
  });

  // ── Per-spawn stream file writes (Activity drill-in viewer) ─────────
  evtSource.addEventListener('spawn-stream-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    // T2 (0.21): relevant when the Activity panel has drilled into a
    // spawn stream OR the global stream is open — 0.20 only watched
    // the per-spawn stream, so the global view froze mid-typing.
    if(activeView==='activity' &&
       ((typeof activityStream==='object' && activityStream && activityStream.spawnId) ||
        (typeof globalStream==='object' && globalStream && globalStream.open))){
      refreshActive('activity');
    }
  });

  // ── T2 (0.22): the stream event RIDES this channel ─────────────────
  // Per-spawn push: the server appends the DELTA (lines since its last
  // push for the file — a per-file cursor, so nothing is ever resent)
  // to the same connection this event arrives on. The viewer APPENDS;
  // the cursor fetch is demoted to open / reconnect catch-up. The wire
  // cursor stays the source of truth: if the push does not continue
  // exactly where the client is (a gap — a push lost mid-reconnect),
  // the catch-up fetch resyncs and the batch is ignored; if it OVERLAPS
  // (the server's cursor was reset, e.g. a restart), the already-held
  // head is sliced away and only the new tail is appended.
  evtSource.addEventListener('spawn-stream-event',function(e){
    var d;try{d=JSON.parse(e.data)}catch(err){return}
    lastEvent=d;
    lastByteAt=Date.now();
    renderLiveIndicator();
    if(!activityStream||activityStream.spawnId!==d.spawn_id)return;
    var evs=d.events||[];
    if(typeof d.from==='number'&&d.from<activityStream.next){
      var skip=activityStream.next-d.from;
      evs=(skip>=evs.length)?[]:evs.slice(skip);
    }else if(typeof d.from==='number'&&d.from>activityStream.next){
      refreshSpawnStream();  // gap: the wire catch-up is the truth
      return;
    }
    for(var i=0;i<evs.length;i++)activityStream.events.push(evs[i]);
    if(d.next>activityStream.next)activityStream.next=d.next;
    if(typeof d.total==='number'&&d.total>activityStream.total)activityStream.total=d.total;
    if(evs.length)renderSpawnStream();
  });

  // T2 (0.22): global push — the time-ordered merge across spawn files
  // moved SERVER-SIDE; the client's global-stream poll is demoted to
  // open / reconnect catch-up. Dedupe is per (spawn_id, line): a line
  // already held (a restart overlap) is skipped, a gap (a line beyond
  // the held tail) triggers the wire resync.
  evtSource.addEventListener('spawn-stream-global',function(e){
    var d;try{d=JSON.parse(e.data)}catch(err){return}
    lastEvent=d;
    lastByteAt=Date.now();
    renderLiveIndicator();
    if(!globalStream||!globalStream.open)return;
    var evs=d.events||[];
    var appended=0,gap=false;
    for(var i=0;i<evs.length;i++){
      var w=evs[i];
      var exp=globalStream.maxLine[w.spawn_id]||0;
      if(w.line<exp)continue;              // duplicate — dedupe
      if(w.line>exp){gap=true;break;}      // gap — wire resync
      globalStream.events.push(w);
      globalStream.maxLine[w.spawn_id]=w.line+1;
      appended++;
    }
    if(gap){refreshGlobalStream();return}
    var changed=appended>0;
    globalStream.total+=appended;
    if(typeof d.total==='number'&&d.total>globalStream.total)globalStream.total=d.total;
    if(typeof d.active_count==='number'){
      if(d.active_count!==globalStream.active_count)changed=true;
      globalStream.active_count=d.active_count;
    }
    if(d.next)globalStream.cursor=mergeGlobalCursor(globalStream.cursor,d.next);
    if(globalStream.events.length>1500)globalStream.events=globalStream.events.slice(-1500);
    if(changed)renderGlobalStream();
  });

  // T2 (0.21): a spawn STARTING or STOPPING is an event of its own.
  // 0.20's activity list updated only on the next unrelated event, and
  // nothing refreshed the processes or roster views from a spawn
  // start/stop at all.
  evtSource.addEventListener('spawn-registry-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='activity')  refreshActive('activity');
    if(activeView==='processes') refreshActive('processes');
    if(activeView==='roster')    refreshActive('roster');
  });

  // ── Process registry (active agent spawns + daemons) ────────────────
  // Drives the activity panel, the roster (live agent state), the
  // processes view, and the company-status pill.
  evtSource.addEventListener('process-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    refreshCompanyStatus();
    if(activeView==='activity')  refreshActive('activity');
    if(activeView==='roster')    refreshActive('roster');
    if(activeView==='processes') refreshActive('processes');
  });

  // ── Sprint state ────────────────────────────────────────────────────
  evtSource.addEventListener('sprint-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='sprint')    refreshActive('sprint');
    if(activeView==='dashboard') refreshActive('dashboard'); // release banner
    // T2 (0.21): the Releases view renders straight off sprint.json —
    // a stage flip is exactly what it is meant to show.
    if(activeView==='releases') refreshActive('releases');
  });

  // ── Reports ─────────────────────────────────────────────────────────
  evtSource.addEventListener('report-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='reports')   refreshActive('reports');
    if(activeView==='marketing') refreshActive('marketing');
    // T2 (0.21): release notes are report files — a written note
    // must reach the Releases view on the same connection.
    if(activeView==='releases') refreshActive('releases');
  });

  // ── Roster + org chart ──────────────────────────────────────────────
  evtSource.addEventListener('roster-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='org')    refreshActive('org');
    if(activeView==='roster') refreshActive('roster');
  });

  // T2 (0.21): GitHub state is live — the server's sync timer fires
  // gh-change whenever GitHub's side of the mirror actually moves
  // (new PR / merged / closed / synced); the GitHub page then
  // refreshes itself instead of waiting for navigation.
  evtSource.addEventListener('gh-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='github') refreshActive('github');
  });

  // ── T3 (0.22): the Git view sees what the repo is doing ─────────────
  // The server watches what the panel reads — .git/HEAD (branch moves),
  // .git/refs/heads (commit / branch updates) and every
  // .worktrees/<TID> directory (file writes, the ticket worktree
  // convention) — and fires git-change on this channel. The shared
  // server-side emit key coalesces a burst into ONE refresh at the
  // 500 ms slow-class window; ticket-change still refreshes the view
  // too (worktrees come and go with ticket state).
  evtSource.addEventListener('git-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='git') refreshActive('git');
  });

  evtSource.onerror=function(){
    sseState='reconnecting';
    renderLiveIndicator();
    // One scheduled retry per error burst (the browser's own retries
    // keep firing onerror until onopen; without the guard the backoff
    // would double-schedule). Delay doubles per failure, caps at 30 s.
    if(!sseReconnectScheduled){
      sseReconnectScheduled=true;
      var delay=sseRetryMs;
      if(sseRetryMs<SSE_RETRY_MAX_MS)sseRetryMs=Math.min(SSE_RETRY_MAX_MS,sseRetryMs*2);
      setTimeout(function(){sseReconnectScheduled=false;connectSSE();},delay);
    }
  };
}

var lastEventDept='';

function updateLiveIndicator(evt){
  lastEventDept=(evt && evt.department) ? evt.department : '';
  lastByteAt=Date.now();
  renderLiveIndicator();
}

function renderLiveIndicator(){
  var el=document.getElementById('live-indicator');
  if(!el)return;
  if(sseState==='connecting'){
    el.innerHTML='<span style="color:#d29922">connecting...</span>';
    return;
  }
  // T2 (0.21): 'reconnecting…' is a state of its own — the browser's
  // onerror only says onopen isn't firing; the SPA's backoff is working.
  if(sseState==='reconnecting'){
    el.innerHTML='<span style="color:#d29922">reconnecting…</span>';
    return;
  }
  if(sseState==='disconnected'){
    el.innerHTML='<span style="color:#f85149">disconnected</span>';
    return;
  }
  var dept=lastEventDept?' ['+lastEventDept+']':'';
  var ago='';
  if(hiveStartedAt>0){
    var nowSec=Math.floor(Date.now()/1000);
    var diff=Math.max(0,nowSec-hiveStartedAt);
    ago=' · '+fmtElapsed(diff)+' ago';
  }
  // T2 (0.21): 'live' means bytes are arriving — the number is the age
  // of the last RECEIVED byte (heartbeats are SSE comments the browser
  // never surfaces, so the observable bound is the last named event /
  // the open), not a socket once opened.
  var byteAgo=Math.max(0,Math.floor((Date.now()-lastByteAt)/1000));
  el.innerHTML='<span class="dot-active" style="display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:4px;vertical-align:middle"></span>'+
    '<span>live'+dept+ago+' · byte '+byteAgo+'s ago</span>';
}

// 1-second tick: re-render every DOM element marked with data-elapsed-from
// (activity table rows, spawn-stream header) plus the header live indicator.
// Cheap because each tick is just a querySelectorAll + textContent write per
// matching element — no innerHTML rewrites of whole panels, no fetches, no
// scroll disruption. Started once on app load; gates on the connected flag.
function tickAsync(){
  if(typeof connected!=='undefined' && !connected)return;
  // T2 (0.21): the 'connected' branch now carries the last-byte age, so
  // the per-second tick keeps it fresh; 'reconnecting…' is re-rendered
  // too (cheap, and the state can change under the tick).
  if(sseState==='connected'||sseState==='reconnecting') renderLiveIndicator();
  var nowSec=Math.floor(Date.now()/1000);
  var els=document.querySelectorAll('[data-elapsed-from]');
  for(var i=0;i<els.length;i++){
    var startTs=parseInt(els[i].getAttribute('data-elapsed-from'),10);
    if(!startTs||isNaN(startTs))continue;
    els[i].textContent=fmtElapsed(Math.max(0,nowSec-startTs));
  }
}
setInterval(tickAsync,1000);

// ── T4 (0.21): hash routing ─────────────────────────────────
// every showX() writes its route; the hashchange listener routes back, so back/forward work and a deep link gets the user back where they were. A bad hash falls to the dashboard; it never blanks the app.

var VIEW_ROUTES = {
  'dashboard':'dashboard','board':'board','chat':'chat','sprint':'sprint',
  'org':'org','roster':'roster','reports':'reports','marketing':'marketing',
  'logs':'logs','verification':'verification','activity':'activity','rounds':'rounds',
  'github':'github','releases':'releases','config':'config','git':'git','brief':'brief','processes':'processes'
};

function currentRoute(){
  var h=(location.hash||"").replace(/^#\/?/,"");
  var parts=h.split("/").filter(Boolean);
  var view=parts[0]||null;
  var param=parts.slice(1).map(function(p){try{return decodeURIComponent(p)}catch(e){return p}}).join("/");
  return {view:view, param:param};
}

function writeHash(view, param){
  var seg=VIEW_ROUTES[view]; if(!seg) return;
  var h="#/"+seg+(param?"/"+param.split("/").map(encodeURIComponent).join("/"):"");
  if(location.hash===h) return;
  location.hash=h;
}

function routeFromHash(){
  var r=currentRoute();
  if(!connected) return;
  if(!r.view){ showDashboard(); return; }
  switch(r.view){
    case 'dashboard': showDashboard(); break;
    case 'board': r.param ? showBoard(r.param) : showDashboard(); break;
    case 'chat': r.param ? showChannel(r.param) : showChats(); break;
    case 'sprint': showSprint(); break;
    case 'org': showOrgChart(); break;
    case 'roster': showRoster(); break;
    case 'reports': showReports(); break;
    case 'marketing': showMarketing(); break;
    case 'logs': r.param ? showLogFile(r.param) : showLogs(); break;
    case 'verification': showVerification(); break;
    case 'activity': showActivity();
      if(r.param==='global') openGlobalStream();
      else if(r.param){ var m=r.param.match(/^spawn\/(.+)$/); if(m) openSpawnStream(m[1]); }
      break;
    case 'rounds': showRounds(); if(r.param) openRound(r.param); break;
    case 'github': showGitHub(); break;
    case 'releases': showReleases(); break;
    case 'config': showConfig(); break;
    case 'git': showGit(); break;
    case 'brief': showBrief(); break;
    case 'processes': showProcesses(); break;
    default: showDashboard(); break;
  }
}

window.addEventListener('hashchange', function(){ routeFromHash(); });

async function checkStatus(){
  var s=await api('status');
  if(s.connected){
    connected=true;
    document.getElementById('nav').style.display='flex';
    document.getElementById('company-control').style.display='inline-block';
    document.getElementById('company-path').textContent=s.path;
    // T4 (0.17): the footer shows the running hive version (from the
    // tree's VERSION file, served on /api/status).
    var vf=document.getElementById('hive-version-footer');
    if(vf&&s.version)vf.textContent='Hive '+s.version;
    connectSSE();
    await loadDepartments();
    refreshCompanyStatus();
    // No setInterval — process-change SSE handler keeps the company-status
    // pill fresh whenever processes.json is rewritten.
    // T4 (0.21): route from the hash instead of the unconditional dashboard.
    // Empty hash -> #/dashboard (push, so back leaves the app);
    // a deep link routes straight to its view (the hashchange
    // listener is already installed above).
    if (!location.hash) { writeHash("dashboard"); } else { routeFromHash(); }
  } else {
    showConnect();
  }
}

function showConnect(){
  stopAutoRefresh();
  connected=false;
  sseState='disconnected';
  if(evtSource){try{evtSource.close()}catch{}}
  document.getElementById('nav').style.display='none';
  const cc=document.getElementById('company-control'); if(cc) cc.style.display='none';
  document.getElementById('company-path').textContent='';
  var recent=getRecent();
  document.getElementById('content').innerHTML=
    '<div class="connect-screen"><div class="connect-box">'+
    '<h2>Connect to a Project</h2>'+
    '<p>Enter the path to a directory containing a .company folder</p>'+
    '<div class="error" id="connect-error"></div>'+
    '<input id="connect-path" placeholder="/path/to/your/project" onkeydown="if(event.key===\'Enter\')doConnect()">'+
    '<button class="btn btn-primary" style="width:100%" onclick="doConnect()">Connect</button>'+
    (recent.length?'<div class="recent"><h4>Recent Projects</h4>'+recent.map(function(r){
      return '<div class="recent-item" onclick="connectTo(\''+esc(r)+'\')">'+esc(r)+'</div>';
    }).join('')+'</div>':'')+
    '</div></div>';
  document.getElementById('connect-path').focus();
}

async function doConnect(){
  var p=document.getElementById('connect-path').value.trim();
  if(!p)return;
  var err=document.getElementById('connect-error');
  err.style.display='none';
  var r=await api('connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:p})});
  if(r.error){err.textContent=r.error;err.style.display='block';return}
  addRecent(p);
  connected=true;
  document.getElementById('nav').style.display='flex';
  document.getElementById('company-path').textContent=r.path;
  connectSSE();
  await loadDepartments();
  showDashboard();
}

async function connectTo(p){
  document.getElementById('connect-path').value=p;
  doConnect();
}

// ── Hive company control (start / stop / verification) ─────────────────────
async function startCompany(){
  const btn=document.getElementById('btn-start-company');
  btn.disabled=true; btn.textContent='Starting...';
  try {
    const r=await api('company/start',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(r.error){alert('Start failed: '+(r.error.message||r.error));}
    else{document.getElementById('company-status-pill').textContent='status: running';}
  } catch(e){alert('Start failed: '+e.message);}
  finally{btn.disabled=false; btn.textContent='▶ Start Company';}
  refreshCompanyStatus();
}

async function stopCompany(){
  if(!confirm('Stop all company daemons?')) return;
  const btn=document.getElementById('btn-stop-company');
  btn.disabled=true; btn.textContent='Stopping...';
  try {
    const r=await api('company/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    document.getElementById('company-status-pill').textContent='status: stopped';
  } catch(e){alert('Stop failed: '+e.message);}
  finally{btn.disabled=false; btn.textContent='■ Stop Company';}
  refreshCompanyStatus();
}

async function refreshCompanyStatus(){
  try {
    const procs=await api('processes');
    const pill=document.getElementById('company-status-pill');
    if(procs && procs.processes && procs.processes.length){
      const alive=procs.processes.filter(p=>p.alive!==false).length;
      pill.textContent='status: '+alive+'/'+procs.processes.length+' processes alive';
    } else {
      pill.textContent='status: stopped';
    }
  } catch{}
}

async function approveTicket(dept, ticketId){
  const verifier=prompt('Verifier name?');
  if(!verifier) return;
  const rationale=prompt('Approval rationale (>= 30 chars - describe what you actually checked):');
  if(!rationale || rationale.length<30){alert('Rationale must be at least 30 characters'); return;}
  const r=await api('departments/'+dept+'/tickets/'+encodeURIComponent(ticketId)+'/approve',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({verifier, rationale})
  });
  if(r.error){alert('Approve failed: '+(r.error.message||r.error));}
  else{alert('Approved. You can now move this ticket to done.');}
}

async function runGates(dept,ticketId){
  // ticket_id scopes the run to THIS ticket - a bare dept run gates the
  // dept's first in-review ticket, which is not necessarily the one the
  // button sits on.
  const body=JSON.stringify(ticketId?{ticket_id:ticketId}:{});
  const r=await api('gates/'+encodeURIComponent(dept)+'/run',{method:'POST',headers:{'Content-Type':'application/json'},body:body});
  const scope=ticketId?(' '+ticketId):'';
  alert('Gates run for '+dept+scope+': '+(r.error ? 'ERROR '+(r.error.message||r.error) : 'see verification view'));
}

function showVerification(){
  activeView='verification';
  writeHash("verification");
  setActiveNavByLabel('Verification');
  renderVerification();
  startAutoRefresh(renderVerification);
}

function _esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

async function renderVerification(){
  const main=document.getElementById('main');
  if(main) main.innerHTML='<h2>Verification Queue</h2><div id="verif-content">Loading...</div>';
  // Some templates use #content not #main - fall back
  const content=document.getElementById('content');
  if(content && !main) content.innerHTML='<h2>Verification Queue</h2><div id="verif-content">Loading...</div>';

  // Fetch current round to show active verification entries (for status badges)
  var roundData=null;
  try { roundData = await api('rounds/current'); } catch(e){}
  var activeVerif={};  // ticket -> entry
  if(roundData && roundData.entries){
    for(var i=0;i<roundData.entries.length;i++){
      var e=roundData.entries[i];
      if(e.kind==='verification' && (e.status==='queued'||e.status==='running')){
        activeVerif[e.ticket]=e;
      }
    }
  }

  await loadDepartments();
  const rows=[];
  for (const d of (departments||[])){
    try {
      const tickets=await api('departments/'+encodeURIComponent(d.name)+'/tickets');
      for (const t of (tickets||[])) {
        if(t._status_dir!=='in-review') continue;
        const gp=t.gates_passed?'✓':'—';
        const vb=t.verified_by?('✓ '+_esc(t.verified_by)):'—';
        // Live status badge for this ticket's verification run
        var verifStatus='';
        var verifEntry=activeVerif[t.id];
        if(verifEntry){
          if(verifEntry.status==='running'){
            verifStatus='<span style="color:#d29922;font-size:11px;margin-right:6px" title="Verification '+_esc(verifEntry.status)+'">● running</span>';
          } else {
            verifStatus='<span style="color:#8b949e;font-size:11px;margin-right:6px" title="Verification queued">○ queued</span>';
          }
        }
        rows.push(
          '<tr style="border-top:1px solid #30363d">'+
            '<td style="padding:6px">'+_esc(d.name)+'</td>'+
            '<td><code>'+_esc(t.id)+'</code></td>'+
            '<td>'+_esc(t.title||'')+'</td>'+
            '<td>'+gp+'</td>'+
            '<td>'+vb+'</td>'+
            '<td>'+
              verifStatus+
              '<button class="vbtn-gates" data-dept="'+_esc(d.name)+'" data-ticket="'+_esc(t.id)+'" style="margin-right:4px">Run gates</button>'+
              '<button class="vbtn-approve" data-dept="'+_esc(d.name)+'" data-ticket="'+_esc(t.id)+'">Approve</button>'+
              '<button class="vbtn-watch" data-dept="'+_esc(d.name)+'" data-ticket="'+_esc(t.id)+'" style="margin-left:4px">View</button>'+
            '</td>'+
          '</tr>'
        );
      }
    } catch(e){}
  }
  var html='<table style="width:100%;border-collapse:collapse;">'+
    '<thead><tr><th align="left">Dept</th><th align="left">Ticket</th><th align="left">Title</th>'+
    '<th align="left">Gates</th><th align="left">Approved</th><th>Actions</th></tr></thead>'+
    '<tbody>'+(rows.length?rows.join(''):'<tr><td colspan="6" style="padding:12px;color:#8b949e">No tickets in in-review.</td></tr>')+'</tbody></table>';
  var target=document.getElementById('verif-content');
  if(target){
    target.innerHTML=html;
    // Attach handlers via delegation - avoids the nested-quote escaping mess
    target.querySelectorAll('.vbtn-gates').forEach(b=>{
      b.addEventListener('click',()=>runGates(b.dataset.dept,b.dataset.ticket));
    });
    target.querySelectorAll('.vbtn-approve').forEach(b=>{
      b.addEventListener('click',()=>approveTicket(b.dataset.dept,b.dataset.ticket));
    });
    target.querySelectorAll('.vbtn-watch').forEach(b=>{
      b.addEventListener('click',()=>openGateOutput(b.dataset.dept,b.dataset.ticket));
    });
  }
}

function setActiveNavByLabel(label){
  for (const b of document.querySelectorAll('#nav button')){
    if(b.textContent.trim()===label) b.classList.add('active'); else b.classList.remove('active');
  }
}
// ───────────────────────────────────────────────────────────────────────────

async function loadDepartments(){departments=await api('departments')}

function showDashboard(){
  activeView='dashboard';
  writeHash("dashboard");
  currentDept=null;currentChannel=null;
  setActiveNav(0);
  renderDashboard();
  startAutoRefresh(function(){loadDepartments().then(renderDashboard)});
}

function renderDashboard(){
  var c=document.getElementById('content');
  if(!departments.length){c.innerHTML='<p style="color:#8b949e;padding:40px;text-align:center">No departments found. Run <code>hive init</code> then <code>hive company start</code> to create a project.</p>';return}
  c.innerHTML='<div style="margin-bottom:16px;display:flex;align-items:center"><span style="font-size:16px;color:#f0f6fc">All Departments</span><button class="btn btn-primary" style="margin-left:auto" onclick="showCreate()">+ New Ticket</button></div>'+'<div class="dashboard">'+departments.map(function(d){
    var active=(d.counts['in-progress']||0)+(d.counts['in-review']||0);
    return '<div class="dept-card" onclick="showBoard(\''+d.name+'\')">'+
      '<h3>'+esc(d.prefix)+' &middot; '+esc(d.name)+'</h3>'+
      '<div class="counts">'+
        '<span class="badge badge-backlog">backlog '+d.counts.backlog+'</span>'+
        '<span class="badge badge-sprint">sprint '+d.counts['sprint-current']+'</span>'+
        '<span class="badge badge-progress">active '+active+'</span>'+
        '<span class="badge badge-done">done '+d.counts.done+'</span>'+
      '</div></div>';
  }).join('')+'</div>';
}

var boardTickets=[];
var boardDept='';
var boardFilter={search:'',type:'',priority:''};

async function showBoard(dept){
  activeView='board';
  if(dept) writeHash("board", dept);
  currentDept=dept;
  boardDept=dept;
  boardFilter={search:'',type:'',priority:''};
  setActiveNav(-1);
  // C.10: a failed first fetch used to reject silently — the view was left
  // on the previous panel's DOM with no feedback. Show the error instead.
  try {
    boardTickets=await api('departments/'+dept+'/tickets');
  } catch(e) {
    document.getElementById('content').innerHTML='<p style="color:#f85149;padding:24px">Failed to load tickets for <b>'+esc(dept)+'</b>: '+(e&&e.message?esc(e.message):esc(String(e)))+'<br><span style="color:#8b949e">Check that the company is running and the MCP server is reachable.</span></p>';
    return;
  }
  renderBoard();
  startAutoRefresh(async function(){boardTickets=await api('departments/'+boardDept+'/tickets');renderBoardColumns()});
}

function boardFilterChange(field,val){boardFilter[field]=val;renderBoardColumns()}

function renderBoard(){
  var dept=boardDept;
  var allTypes={},allPris={};
  for(var i=0;i<boardTickets.length;i++){allTypes[boardTickets[i].type||'?']=1;allPris[boardTickets[i].priority||'?']=1}

  var c=document.getElementById('content');
  var html='<div style="margin-bottom:12px"><button class="btn" onclick="showDashboard()">&larr; All Departments</button>'+
    ' <span style="font-size:16px;color:#f0f6fc;margin-left:12px">'+esc(dept)+'</span>'+
    ' <button class="btn" style="margin-left:8px" onclick="showCreateFor(\''+dept+'\')">+ Ticket</button></div>';

  html+='<div class="filter-bar">';
  html+='<input id="board-search" placeholder="Search tickets..." value="'+esc(boardFilter.search)+'" oninput="boardFilterChange(\'search\',this.value)">';
  html+='<select onchange="boardFilterChange(\'type\',this.value)"><option value="">All Types</option>';
  Object.keys(allTypes).sort().forEach(function(t){html+='<option'+(boardFilter.type===t?' selected':'')+'>'+esc(t)+'</option>'});
  html+='</select>';
  html+='<select onchange="boardFilterChange(\'priority\',this.value)"><option value="">All Priorities</option>';
  ['critical','high','medium','low'].forEach(function(p){if(allPris[p])html+='<option'+(boardFilter.priority===p?' selected':'')+'>'+esc(p)+'</option>'});
  html+='</select>';
  if(boardFilter.search||boardFilter.type||boardFilter.priority){
    html+='<span class="filter-clear" onclick="boardFilter={search:\'\',type:\'\',priority:\'\'};renderBoardColumns()">Clear</span>';
  }
  html+='<span class="filter-count" id="board-filter-count"></span>';
  html+='</div>';
  html+='<div id="board-columns"></div>';
  c.innerHTML=html;
  renderBoardColumns();
}

function renderBoardColumns(){
  var dept=boardDept;
  var filtered=boardTickets.filter(function(t){
    if(boardFilter.search){
      var s=boardFilter.search.toLowerCase();
      var hay=((t.id||'')+(t.title||'')+(t.description||'')).toLowerCase();
      if(hay.indexOf(s)<0)return false;
    }
    if(boardFilter.type&&t.type!==boardFilter.type)return false;
    if(boardFilter.priority&&t.priority!==boardFilter.priority)return false;
    return true;
  });
  var statuses=['backlog','sprint-current','in-progress','in-review','done'];
  var labels={backlog:'Backlog','sprint-current':'Sprint','in-progress':'In Progress','in-review':'In Review',done:'Done'};
  var el=document.getElementById('board-columns');
  if(!el)return;
  el.innerHTML='<div class="board">'+statuses.map(function(s){
    var col=filtered.filter(function(t){return t._status_dir===s});
    return '<div class="column"><div class="column-header">'+labels[s]+' <span class="count">'+col.length+'</span></div>'+
      '<div class="column-body">'+col.map(function(t){
        // For tickets in done, surface WHY (verified | rejected | merge-failed | etc.)
        // so the operator can distinguish "shipped" from "won't-do" at a glance.
        var doneReason='';
        if(s==='done'){
          var r=(t.resolution&&t.resolution.status)||t.done_reason||'';
          if(r){
            var color=(r==='rejected')?'#f85149':(r==='implemented'||r==='verified')?'#3fb950':'#d29922';
            doneReason='<span style="font-size:10px;color:'+color+';background:#0d1117;border:1px solid '+color+';padding:1px 5px;border-radius:3px">'+esc(r)+'</span>';
          }
        }
        return '<div class="ticket-card" onclick="showTicket(\''+dept+'\',\''+esc(t.id)+'\')">'+
          '<div class="ticket-id">'+esc(t.id)+'</div>'+
          '<div class="ticket-title">'+esc(t.title)+'</div>'+
          '<div class="ticket-meta">'+
            '<span class="type-badge">'+esc(t.type)+'</span>'+
            '<span class="priority-'+esc(t.priority)+'">'+esc(t.priority)+'</span>'+
            doneReason+
            (t.assignee&&t.assignee.name?'<span style="font-size:11px;color:#8b949e">'+esc(t.assignee.name)+'</span>':'')+
          '</div></div>';
      }).join('')+'</div></div>';
  }).join('')+'</div>';
  var fc=document.getElementById('board-filter-count');
  if(fc)fc.textContent=filtered.length+' of '+boardTickets.length+' tickets';
}

async function showTicket(dept,id){
  var t=null;
  try {
    t=await api('departments/'+dept+'/tickets/'+encodeURIComponent(id));
  } catch(e) {
    alert('Failed to load ticket: network or server error.');
    return;
  }
  // C.10: a moved/deleted ticket 404s with {error:...} — rendering it as a
  // ticket used to produce an "undefined: undefined" modal.
  if(!t||t.error){
    alert((t&&t.error)?'Ticket not available: '+(typeof t.error==='string'?t.error:t.error.message):'Ticket not available.');
    return;
  }
  var m=document.getElementById('modal');
  var statusOpts=['backlog','sprint-current','in-progress','in-review','done'].map(function(s){
    return '<option'+(s===t.status?' selected':'')+'>'+s+'</option>';}).join('');
  // Status display includes the resolution reason for done tickets so the
  // operator can tell "verified / implemented" from "rejected" without
  // opening the resolution block below.
  var statusDisplay=t.status;
  if(t.status==='done'){
    var rr=(t.resolution&&t.resolution.status)||t.done_reason||'';
    if(rr){
      var rcolor=(rr==='rejected')?'#f85149':(rr==='implemented'||rr==='verified')?'#3fb950':'#d29922';
      statusDisplay='done <span style="color:'+rcolor+';font-weight:600">('+esc(rr)+')</span>';
    }
  }
  m.innerHTML='<h2>'+esc(t.id)+': '+esc(t.title)+'</h2>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">'+
      df('Type',t.type)+df('Priority','<span class="priority-'+t.priority+'">'+t.priority+'</span>')+
      df('Status',statusDisplay+' <select class="status-select" onchange="moveTicket(\''+dept+'\',\''+esc(t.id)+'\',this.value)">'+statusOpts+'</select>')+
      df('Department',t.target_department)+
      df('Created',localTime(t.created_at))+df('Assignee',t.assignee&&t.assignee.name?t.assignee.name:'Unassigned')+
      df('Sprint',t.sprint||'None')+df('Estimate',t.estimate||'None')+
      df('Filed by',t.filed_by_department||'?')+df('Source',t.source||'?')+
      df('Labels',(t.labels&&t.labels.length)?t.labels.map(esc).join(', '):'None')+
    '</div>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">'+
      df('Bounces','<span class="bounce-count" style="color:'+bounceColor(t.bounce_count||0)+';font-weight:600">'+(t.bounce_count||0)+' / 5</span>')+
    '</div>'+
    (t.description?'<div class="detail-field"><div class="label">Description</div><div class="value md-content">'+renderMarkdown(t.description)+'</div></div>':'')+
    (t.acceptance_criteria&&t.acceptance_criteria.length?'<div class="detail-field"><div class="label">Acceptance Criteria</div><div class="value">'+t.acceptance_criteria.map(function(a){return '<div>- '+esc(a)+'</div>'}).join('')+'</div></div>':'')+
    (t.dependencies&&t.dependencies.length?'<div class="detail-field"><div class="label">Dependencies</div><div class="value">'+t.dependencies.map(function(d){return esc(d)}).join(', ')+'</div></div>':'')+
    (t.resolution&&(t.resolution.git_branch||t.resolution.commit||t.resolution.resolved_by)?'<div class="detail-field"><div class="label">Resolution</div><div class="value">'+(t.resolution.git_branch?'Branch: <a href="#" onclick="closeModal();showBranchHistory(\''+esc(t.resolution.git_branch)+'\');return false" style="color:#58a6ff;cursor:pointer">'+esc(t.resolution.git_branch)+'</a> ':'')+(t.resolution.commit?'Commit: <a href="#" onclick="closeModal();showCommitDiff(\''+esc(t.resolution.commit)+'\');return false" style="color:#58a6ff;cursor:pointer;font-family:monospace">'+esc(t.resolution.commit)+'</a> ':'')+(t.resolution.resolved_by?' by '+esc(t.resolution.resolved_by):'')+(t.resolution.resolved_at?' at '+localTime(t.resolution.resolved_at):'')+'</div></div>':'')+
    '<div class="detail-field"><div class="label">Comments ('+((t.comments||[]).length)+')</div>'+
      (t.comments||[]).map(function(c){return '<div class="comment"><div class="comment-author">'+esc(c.author||'system')+' <span style="color:#8b949e">'+localTime(c.timestamp||c.time)+'</span></div><div class="comment-body md-content">'+renderMarkdown(c.body||c.text||'')+'</div></div>'}).join('')+
    '</div>'+
    '<div style="margin-top:12px"><label>Add Comment</label><textarea id="new-comment" rows="2"></textarea></div>'+
    '<div class="actions">'+
      '<button class="btn" onclick="closeModal()">Close</button>'+
      '<button class="btn btn-primary" onclick="addComment(\''+dept+'\',\''+esc(t.id)+'\')">Comment</button>'+
    '</div>';
  openModal();
}

async function moveTicket(dept,id,newStatus){
  // C.10: the move can be rejected (done requires verification evidence;
  // some transitions are scripted-only). Surface the failure instead of
  // closing the modal and leaving the user with a silent no-op.
  var r=null;
  try {
    r=await api('departments/'+dept+'/tickets/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:newStatus})});
  } catch(e) {
    alert('Move failed: network or server error.');
    showTicket(dept,id);
    return;
  }
  if(r&&r.error){
    alert('Move failed:\n\n'+(typeof r.error==='string'?r.error:(r.error.message||JSON.stringify(r.error))));
    showTicket(dept,id);  // re-render: resets the status select to the real value
    return;
  }
  closeModal();
  showBoard(dept);
}

async function addComment(dept,id){
  var body=document.getElementById('new-comment').value.trim();
  if(!body)return;
  var r=null;
  try {
    r=await api('departments/'+dept+'/tickets/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({comment:body})});
  } catch(e) {
    alert('Comment failed: network or server error.');
    return;
  }
  if(r&&r.error){
    alert('Comment failed:\n\n'+(typeof r.error==='string'?r.error:(r.error.message||JSON.stringify(r.error))));
    return;
  }
  showTicket(dept,id);
}

function showCreate(){showCreateFor(departments[0]?departments[0].name:'')}
async function showCreateFor(defaultDept){
  var m=document.getElementById('modal');
  var types=await api('ticket-types');
  var deptOpts=departments.map(function(d){return '<option value="'+d.name+'"'+(d.name===defaultDept?' selected':'')+'>'+d.name+'</option>'}).join('');
  var typeOpts=types.map(function(t){return '<option value="'+esc(t)+'">'}).join('');
  m.innerHTML='<h2>Create Ticket</h2>'+
    '<label>Department</label><select id="c-dept">'+deptOpts+'</select>'+
    '<label>Type</label><input id="c-type" list="type-list" value="feature" placeholder="Type or select..." style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:8px 12px;border-radius:6px;font-family:inherit;font-size:13px"><datalist id="type-list">'+typeOpts+'</datalist>'+
    '<label>Priority</label><select id="c-priority"><option>medium</option><option>critical</option><option>high</option><option>low</option></select>'+
    '<label>Title</label><input id="c-title" placeholder="Short descriptive title">'+
    '<label>Description</label><textarea id="c-desc" placeholder="Detailed description..."></textarea>'+
    '<label>Acceptance Criteria (one per line)</label><textarea id="c-ac" rows="3" placeholder="Criterion 1"></textarea>'+
    '<label>Labels (comma-separated)</label><input id="c-labels" placeholder="auth, api, urgent">'+
    '<div class="actions"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="createTicket()">Create Ticket</button></div>';
  openModal();
  setTimeout(function(){document.getElementById('c-title').focus()},100);
}

async function createTicket(){
  var dept=document.getElementById('c-dept').value;
  var acText=document.getElementById('c-ac').value;
  var labelsText=document.getElementById('c-labels').value;
  var data={
    type:document.getElementById('c-type').value,
    priority:document.getElementById('c-priority').value,
    title:document.getElementById('c-title').value,
    description:document.getElementById('c-desc').value,
    acceptance_criteria:acText.split('\n').map(function(s){return s.trim()}).filter(Boolean),
    labels:labelsText.split(',').map(function(s){return s.trim()}).filter(Boolean),
  };
  if(!data.title){alert('Title is required');return}
  // Surface failures instead of silently closing the modal. Old behavior
  // assumed the call always succeeded; the script could reject (bad type,
  // missing dept, MCP server down, etc.) and the user would see nothing.
  var t;
  try {
    t=await api('departments/'+dept+'/tickets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  } catch(e) {
    alert('Create failed: network or server error.\n\n'+(e&&e.message?e.message:e));
    return;
  }
  if(t&&(t.error||t.ok===false)){
    var msg=t.error||t.stderr||t.stdout||'unknown error';
    alert('Create failed:\n\n'+msg);
    return;
  }
  closeModal();
  if(currentDept===dept)showBoard(dept);
  else{await loadDepartments();renderDashboard()}
}

// ===== CHAT =====
var currentChannel=null;

async function showChats(){
  activeView='chat';
  writeHash("chat");
  stopAutoRefresh();
  currentDept=null;
  setActiveNav(1);
  var channels=await api('chat');
  renderChatLayout(channels);
  if(channels.length)showChannel(channels[0].name);
  startAutoRefresh(async function(){if(currentChannel)showChannel(currentChannel)});
}

function renderChatLayout(channels){
  var c=document.getElementById('content');
  c.innerHTML='<div class="chat-layout">'+
    '<div class="channel-list" id="channel-list">'+
      channels.map(function(ch){
        return '<div class="channel-item" data-ch="'+esc(ch.name)+'" onclick="showChannel(\''+esc(ch.name)+'\')">'+
          '<span class="ch-name"># '+esc(ch.name)+'</span>'+
          '<span class="ch-count">'+ch.messageCount+'</span>'+
        '</div>';
      }).join('')+
      (channels.length?'':'<div class="chat-empty">No chat channels yet</div>')+
    '</div>'+
    '<div class="chat-view" id="chat-view"><div class="chat-empty">Select a channel</div></div>'+
  '</div>';
}

// T3 (0.22): the transcript-level "N new — jump to latest" pill. The
// channel-list badge (messageCount) is the LIST-level signal and stays;
// the pill is what the OPEN channel owes a reader who scrolled up: when
// messages arrive while you are reading history, you stay where you are
// (the refresh wrapper restores scroll), the pill COUNTS what you missed
// (a count, not a bare "new"), and the jump snaps to the tail and clears
// it. atBottom — the same 16px threshold the scroll machinery (saveScroll)
// uses — decides follow vs count: at the tail you follow (the 0.21
// behavior, preserved); above the tail you count. 0.21's refetch scrolled
// to the bottom unconditionally — a scrolled-up reader was yanked to the
// tail on every chat-change.
var chatSeen={};      // channel -> message count the reader is tracking
var chatNewCount=0;   // current channel: messages arrived while scrolled up
var chatMsgCount=0;   // current channel: total messages in the last fetch

function chatAtBottom(){
  var el=document.getElementById('chat-msgs');
  if(!el)return true;   // no messages rendered — nothing to be above
  return (el.scrollHeight-el.clientHeight-el.scrollTop)<=16;
}

function chatJumpToLatest(){
  var msgs=document.getElementById('chat-msgs');
  if(msgs)msgs.scrollTop=msgs.scrollHeight;
  chatNewCount=0;
  if(currentChannel)chatSeen[currentChannel]=chatMsgCount;
  var p=document.getElementById('chat-new-pill');
  if(p)p.remove();
}

function chatScrollHandler(){
  // The user scrolled to the tail on their own — everything is seen.
  if(chatNewCount>0&&chatAtBottom()){
    chatNewCount=0;
    if(currentChannel)chatSeen[currentChannel]=chatMsgCount;
    var p=document.getElementById('chat-new-pill');
    if(p)p.remove();
  }
}

async function showChannel(name){
  currentChannel=name;
  writeHash("chat", name);
  document.querySelectorAll('.channel-item').forEach(function(el){
    el.classList.toggle('active',el.dataset.ch===name);
  });
  // T3 (0.22): measure the reader's position BEFORE the re-render —
  // after innerHTML the new element starts at scrollTop 0, where
  // atBottom would read true for a scrolled-up reader.
  var preMsgs=document.getElementById('chat-msgs');
  var wasAtBottom=preMsgs
    ? (preMsgs.scrollHeight-preMsgs.clientHeight-preMsgs.scrollTop)<=16
    : true;
  var data=await api('chat/'+encodeURIComponent(name));
  var view=document.getElementById('chat-view');
  if(!data.messages||!data.messages.length){
    chatSeen[name]=0;
    chatNewCount=0;
    chatMsgCount=0;
    view.innerHTML='<div class="chat-header"># '+esc(name)+'</div><div class="chat-messages"><div class="chat-empty">No messages in this channel yet</div></div>';
    return;
  }
  chatMsgCount=data.messages.length;
  // T3 (0.22): follow vs count. First open of the channel, or the reader
  // at the tail -> follow the tail. Scrolled up -> stay put; the pill
  // counts the messages that arrived while out of view.
  if(chatSeen[name]==null||wasAtBottom){
    chatSeen[name]=data.messages.length;
    chatNewCount=0;
  } else if(data.messages.length>chatSeen[name]){
    chatNewCount=data.messages.length-chatSeen[name];
  } else {
    chatNewCount=0;
  }
  view.innerHTML='<div class="chat-header"># '+esc(name)+' <span style="font-weight:normal;font-size:12px;color:#8b949e;margin-left:8px">'+data.messages.length+' messages</span></div>'+
    '<div class="chat-messages" id="chat-msgs">'+
      data.messages.map(function(m){
        return '<div class="chat-msg">'+
          '<div class="msg-header"><span class="msg-author">'+esc(m.author||'system')+'</span><span class="msg-time">'+localShort(m.timestamp)+'</span></div>'+
          '<div class="msg-body md-content">'+renderMarkdown(m.body||'')+'</div>'+
        '</div>';
      }).join('')+
    '</div>'+
    (chatNewCount>0
      ? '<button type="button" class="chat-new-pill" id="chat-new-pill" onclick="chatJumpToLatest()">'+chatNewCount+' new &mdash; jump to latest</button>'
      : '');
  var msgs=document.getElementById('chat-msgs');
  if(msgs){
    // A fresh element per render — the handler is stable, one listener each.
    msgs.addEventListener('scroll',chatScrollHandler);
    if(chatNewCount===0)msgs.scrollTop=msgs.scrollHeight;
  }
}



function renderMarkdown(md){
  if(!md)return '';
  var BT=String.fromCharCode(96);
  var fencedRe=new RegExp('^'+BT+BT+BT+'([a-zA-Z0-9_+-]*)\\n([\\s\\S]*?)\\n'+BT+BT+BT+'$','gm');
  var codeStash=[];
  md=String(md).replace(fencedRe,function(_,lang,code){
    var idx=codeStash.length;
    codeStash.push('<pre style="background:#21262d;padding:12px;border-radius:6px;overflow-x:auto;font-size:12px;margin:8px 0"><code>'+escHtml(code)+'</code></pre>');
    return '\u0000CODEBLOCK'+idx+'\u0000';
  });

  function escHtml(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
  function inline(s){
    var out=escHtml(s);
    // inline code first so the contents aren't bolded
    out=out.replace(new RegExp(BT+'([^'+BT+']+)'+BT,'g'),'<code>$1</code>');
    out=out.replace(/\*\*([^*]+)\*\*/g,'<strong style="color:#f0f6fc">$1</strong>');
    out=out.replace(/(?:^|[^*])\*([^*\n]+)\*/g,function(m,t){return m.charAt(0)==='*'?m:m.charAt(0)+'<em>'+t+'</em>'});
    // [text](url)
    out=out.replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" style="color:#58a6ff" target="_blank" rel="noopener">$1</a>');
    return out;
  }

  var lines=String(md).split(/\r?\n/);
  var html='';
  var i=0;
  function isTableSep(s){return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(s)}
  function splitRow(s){
    var t=s.trim().replace(/^\|/,'').replace(/\|$/,'');
    return t.split('|').map(function(c){return c.trim()});
  }
  while(i<lines.length){
    var line=lines[i];
    // Blank
    if(/^\s*$/.test(line)){i++;continue}
    // Heading
    var hm=/^(#{1,6})\s+(.*)$/.exec(line);
    if(hm){var lvl=hm[1].length;html+='<h'+lvl+' style="margin:14px 0 6px;color:#f0f6fc">'+inline(hm[2])+'</h'+lvl+'>';i++;continue}
    // HR
    if(/^\s*-{3,}\s*$/.test(line)||/^\s*\*{3,}\s*$/.test(line)){html+='<hr style="border:none;border-top:1px solid #30363d;margin:10px 0">';i++;continue}
    // Blockquote
    if(/^\s*>\s?/.test(line)){
      var bq=[];
      while(i<lines.length&&/^\s*>\s?/.test(lines[i])){bq.push(lines[i].replace(/^\s*>\s?/,''));i++}
      html+='<blockquote style="border-left:3px solid #30363d;padding:4px 12px;margin:8px 0;color:#8b949e">'+inline(bq.join(' '))+'</blockquote>';
      continue;
    }
    // Table — current line has |, next is separator
    if(/\|/.test(line)&&i+1<lines.length&&isTableSep(lines[i+1])){
      var header=splitRow(line);
      var bodyRows=[];
      i+=2;
      while(i<lines.length&&/\|/.test(lines[i])&&!/^\s*$/.test(lines[i])){
        bodyRows.push(splitRow(lines[i]));i++;
      }
      var t='<table style="border-collapse:collapse;margin:8px 0;font-size:12px">';
      t+='<thead><tr>'+header.map(function(c){return '<th style="border:1px solid #30363d;padding:5px 9px;background:#161b22;color:#f0f6fc;text-align:left">'+inline(c)+'</th>'}).join('')+'</tr></thead>';
      t+='<tbody>';
      for(var br=0;br<bodyRows.length;br++){
        t+='<tr>'+bodyRows[br].map(function(c){return '<td style="border:1px solid #30363d;padding:5px 9px;vertical-align:top">'+inline(c)+'</td>'}).join('')+'</tr>';
      }
      t+='</tbody></table>';
      html+=t;
      continue;
    }
    // Unordered list
    if(/^\s*[-*+]\s+/.test(line)){
      var ul='<ul style="margin:6px 0;padding-left:22px">';
      while(i<lines.length&&/^\s*[-*+]\s+/.test(lines[i])){
        ul+='<li>'+inline(lines[i].replace(/^\s*[-*+]\s+/,''))+'</li>';i++;
      }
      ul+='</ul>';html+=ul;continue;
    }
    // Ordered list
    if(/^\s*\d+\.\s+/.test(line)){
      var ol='<ol style="margin:6px 0;padding-left:22px">';
      while(i<lines.length&&/^\s*\d+\.\s+/.test(lines[i])){
        ol+='<li>'+inline(lines[i].replace(/^\s*\d+\.\s+/,''))+'</li>';i++;
      }
      ol+='</ol>';html+=ol;continue;
    }
    // Paragraph — gather consecutive non-blank lines
    var p=[line];i++;
    while(i<lines.length&&!/^\s*$/.test(lines[i])
        &&!/^(#{1,6})\s+/.test(lines[i])
        &&!/^\s*[-*+]\s+/.test(lines[i])
        &&!/^\s*\d+\.\s+/.test(lines[i])
        &&!/^\s*>\s?/.test(lines[i])
        &&!(/\|/.test(lines[i])&&i+1<lines.length&&isTableSep(lines[i+1]))){
      p.push(lines[i]);i++;
    }
    html+='<p style="margin:6px 0">'+inline(p.join(' '))+'</p>';
  }

  // Reinsert code blocks
  html=html.replace(/\u0000CODEBLOCK(\d+)\u0000/g,function(_,n){return codeStash[Number(n)]||''});
  return html;
}

// ===== 1. ORG CHART =====
var orgShowActiveOnly=false;

function toggleOrgActive(){orgShowActiveOnly=!orgShowActiveOnly;showOrgChart()}

function renderRosterAgents(agents){
  if(!agents||!agents.length)return '<div class="org-no-agents">No agents spawned yet</div>';
  if(orgShowActiveOnly)agents=agents.filter(function(a){return a.status==='active'});
  if(!agents.length)return '<div class="org-no-agents">No active agents</div>';
  var html='<div class="org-agents-list">';
  for(var a=0;a<agents.length;a++){
    var ag=agents[a];
    // dot-active = live process; dot-claimed = paper assignment, no process yet
    // (assignee written but the lifecycle hasn't launched it / failed to launch)
    // dot-retired = finished; dot-stale = idle/inactive
    var dotClass='dot-stale';
    if(ag.status==='active')   dotClass='dot-active';
    else if(ag.status==='claimed') dotClass='dot-claimed';
    else if(ag.status==='retired') dotClass='dot-retired';
    var statusBadge='';
    if(ag.status==='claimed') statusBadge='<span style="font-size:9px;color:#d29922;background:#382b00;padding:1px 5px;border-radius:3px;margin-left:4px" title="Ticket assignee written but no agent process yet — the lifecycle launches it on the next stage pass, or the agent is queued at the local LLM">claimed</span>';
    var roleIcon='';
    var role=(ag.role||'').toLowerCase();
    if(role.indexOf('vp')===0||role.indexOf('vice')>=0)roleIcon='VP';
    else if(role.indexOf('manager')>=0||role.indexOf('mgr')>=0)roleIcon='MGR';
    else if(role.indexOf('ic')>=0||role.indexOf('engineer')>=0)roleIcon='IC';
    else if(role.indexOf('tester')>=0||role.indexOf('qa')>=0||role.indexOf('verif')>=0)roleIcon='QA';
    else roleIcon='AG';
    html+='<div class="org-agent">';
    html+='<span class="org-agent-icon">'+roleIcon+'</span>';
    html+='<span class="dot '+dotClass+'"></span>';
    html+='<span class="org-agent-role">'+esc(ag.role||'agent')+'</span>';
    html+=statusBadge;
    if(ag.generation&&ag.generation>1)html+='<span class="org-agent-gen">gen '+ag.generation+'</span>';
    if(ag.notes)html+='<span class="org-agent-notes">'+esc(ag.notes.substring(0,60))+'</span>';
    html+='</div>';
  }
  html+='</div>';
  return html;
}
async function showOrgChart(){
  activeView='org';
  writeHash("org");
  stopAutoRefresh();
  setActiveNav(4);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading org chart...</p>';
  var data=await api('org-chart');
  if(data.error){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}

  var html='<div style="margin-bottom:16px;display:flex;align-items:center;gap:16px"><span style="font-size:18px;color:#f0f6fc">'+esc(data.company_name||data.project_name||'Company')+'</span>'+
    '<span style="font-size:11px;color:#8b949e">'+
    '<span class="dot" style="background:#58a6ff"></span> fixed '+
    '<span class="dot" style="background:#3fb950;margin-left:8px"></span> dynamic</span>'+
    '<button class="btn'+(orgShowActiveOnly?' btn-primary':'')+'" style="margin-left:auto;font-size:11px;padding:4px 10px" onclick="toggleOrgActive()">'+(orgShowActiveOnly?'Showing Active Only':'Show Active Only')+'</button>'+
    '</div>';

  // CEO node
  html+='<div class="org-tree">';
  html+='<div class="org-node org-ceo"><div class="org-node-title">CEO</div><div class="org-node-sub">Chief Executive Officer</div></div>';
  html+='<div class="org-children">';

  // Departments
  var depts=data.departments||{};
  var deptNames=Object.keys(depts);
  // Fixed first, then dynamic sorted by wave
  var fixed=deptNames.filter(function(d){return depts[d].type==='fixed'});
  var dynamic=deptNames.filter(function(d){return depts[d].type==='dynamic'}).sort(function(a,b){return (depts[a].wave||0)-(depts[b].wave||0)});

  var roster=data._roster||{};

  for(var i=0;i<fixed.length;i++){
    var name=fixed[i];var d=depts[name];
    html+='<div class="org-node org-fixed">';
    html+='<div class="org-node-title">'+esc(d.vp||name)+'</div>';
    html+='<div class="org-node-sub">'+esc(d.purpose||'')+'</div>';
    html+='<div class="org-node-badge org-badge-fixed">fixed</div>';
    html+=renderRosterAgents(roster[name]||[]);
    html+='</div>';
  }

  for(var i=0;i<dynamic.length;i++){
    var name=dynamic[i];var d=depts[name];
    html+='<div class="org-node org-dynamic">';
    html+='<div class="org-node-title">'+esc(d.vp||name)+'</div>';
    html+='<div class="org-node-sub">'+esc(d.purpose||'')+'</div>';
    html+='<div class="org-node-meta">';
    html+='<span class="org-node-badge org-badge-dynamic">wave '+esc(d.wave)+'</span>';
    html+='<span class="org-node-badge" style="background:#21262d;color:#8b949e">'+esc(d.depth||'')+'</span>';
    if(d.managers)html+='<span class="org-node-badge" style="background:#21262d;color:#8b949e">'+d.managers+' mgr</span>';
    html+='</div>';
    if(d.components&&d.components.length){
      html+='<div class="org-components">';
      for(var j=0;j<d.components.length;j++){
        html+='<span class="org-component">'+esc(d.components[j])+'</span>';
      }
      html+='</div>';
    }
    if(d.dependencies&&d.dependencies.length){
      html+='<div class="org-deps">depends on: '+d.dependencies.map(esc).join(', ')+'</div>';
    }
    // Roster agents for this department
    html+=renderRosterAgents(roster[name]||[]);
    // Teams if present
    if(d.teams){
      html+='<div class="org-teams">';
      var teamNames=Object.keys(d.teams);
      for(var t=0;t<teamNames.length;t++){
        var tn=teamNames[t];var team=d.teams[tn];
        html+='<div class="org-team"><span class="org-team-name">'+esc(team.manager||tn)+'</span>';
        if(team.components)html+=' <span class="org-team-detail">'+team.components.length+' components</span>';
        if(team.ics)html+=' <span class="org-team-detail">'+team.ics+' ICs</span>';
        if(team.testers)html+=' <span class="org-team-detail">'+team.testers+' testers</span>';
        if(team.scope)html+=' <span class="org-team-detail">'+esc(team.scope)+'</span>';
        html+='</div>';
      }
      html+='</div>';
    }
    html+='</div>';
  }

  html+='</div></div>'; // close org-children and org-tree

  // Waves summary
  if(data.waves&&data.waves.length){
    html+='<div style="margin-top:24px"><span style="font-size:14px;color:#f0f6fc">Wave Plan</span>';
    html+='<div style="display:flex;gap:12px;margin-top:8px;flex-wrap:wrap">';
    for(var w=0;w<data.waves.length;w++){
      var wave=data.waves[w];
      html+='<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 14px;min-width:140px">';
      html+='<div style="font-size:12px;color:#58a6ff;font-weight:600">Wave '+esc(wave.wave)+'</div>';
      if(wave.departments){
        for(var wd=0;wd<wave.departments.length;wd++){
          html+='<div style="font-size:12px;color:#c9d1d9;margin-top:2px">'+esc(wave.departments[wd])+'</div>';
        }
      }
      html+='</div>';
    }
    html+='</div></div>';
  }

  // Totals
  if(data.total_components||data.total_agents_estimate){
    html+='<div style="margin-top:16px;display:flex;gap:16px">';
    if(data.total_components)html+='<div class="summary-card"><div class="summary-val">'+data.total_components+'</div><div class="summary-label">Components</div></div>';
    if(data.total_agents_estimate)html+='<div class="summary-card"><div class="summary-val">'+data.total_agents_estimate+'</div><div class="summary-label">Est. Agents</div></div>';
    html+='<div class="summary-card"><div class="summary-val">'+fixed.length+'</div><div class="summary-label">Fixed Depts</div></div>';
    html+='<div class="summary-card"><div class="summary-val">'+dynamic.length+'</div><div class="summary-label">Dynamic Depts</div></div>';
    html+='</div>';
  }

  c.innerHTML=html;
}


// ===== 2. ROSTER =====
var rosterData=[];
var rosterSort={col:'role',dir:1};
var rosterFilters={search:'',department:'',status:'',role:''};

var cycleData={};

async function showRoster(){
  activeView='roster';
  writeHash("roster");
  stopAutoRefresh();
  setActiveNav(5);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading roster...</p>';
  var data=await api('roster');
  if(data.error){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}
  rosterData=Array.isArray(data)?data:(data.agents||data.roster||[]);
  cycleData=await api('agent-cycles');
  renderRosterTable();
  startAutoRefresh(async function(){var f=saveFocus();var d=await api('roster');rosterData=Array.isArray(d)?d:(d.agents||d.roster||[]);cycleData=await api('agent-cycles');renderRosterTable();restoreFocus(f)});
}

function sortRosterBy(col){
  if(rosterSort.col===col)rosterSort.dir*=-1;
  else{rosterSort.col=col;rosterSort.dir=1}
  renderRosterRows();
}

function rosterFilterChange(field,val){rosterFilters[field]=val;renderRosterRows()}

function renderRosterTable(){
  var allDepts={},allStatuses={},allRoles={};
  for(var i=0;i<rosterData.length;i++){
    var a=rosterData[i];
    allDepts[a.department||a.dept||'-']=1;
    allStatuses[a.status||'unknown']=1;
    allRoles[a.role||'?']=1;
  }
  var active=0,retired=0;
  for(var i=0;i<rosterData.length;i++){
    var st=rosterData[i].status||'';
    if(st==='active')active++;
    else if(st==='retired'||st==='terminated'||st==='completed')retired++;
  }
  var total=rosterData.length;
  var cols=[{k:'role',l:'Role'},{k:'department',l:'Department'},{k:'status',l:'Status'},{k:'cycles',l:'Cycles'},{k:'generation',l:'Gen'},{k:'messages',l:'Messages'},{k:'last_active',l:'Last Active'},{k:'ticket',l:'Ticket'}];

  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Agent Roster</span></div>';
  html+='<div class="summary-row">'+
    '<div class="summary-card"><div class="num">'+total+'</div><div class="lbl">Total</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+active+'</div><div class="lbl">Active</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#484f58">'+retired+'</div><div class="lbl">Completed/Retired</div></div>'+
  '</div>';
  html+='<div class="filter-bar">';
  html+='<input id="roster-search" placeholder="Search..." value="'+esc(rosterFilters.search)+'" oninput="rosterFilterChange(\'search\',this.value)">';
  html+='<select onchange="rosterFilterChange(\'role\',this.value)"><option value="">All Roles</option>';
  Object.keys(allRoles).sort().forEach(function(r){html+='<option'+(rosterFilters.role===r?' selected':'')+'>'+esc(r)+'</option>'});
  html+='</select>';
  html+='<select onchange="rosterFilterChange(\'department\',this.value)"><option value="">All Departments</option>';
  Object.keys(allDepts).sort().forEach(function(d){html+='<option'+(rosterFilters.department===d?' selected':'')+'>'+esc(d)+'</option>'});
  html+='</select>';
  html+='<select onchange="rosterFilterChange(\'status\',this.value)"><option value="">All Statuses</option>';
  Object.keys(allStatuses).sort().forEach(function(s){html+='<option'+(rosterFilters.status===s?' selected':'')+'>'+esc(s)+'</option>'});
  html+='</select>';
  if(rosterFilters.search||rosterFilters.department||rosterFilters.status||rosterFilters.role){
    html+='<span class="filter-clear" onclick="rosterFilters={search:\'\',department:\'\',status:\'\',role:\'\'};renderRosterRows()">Clear filters</span>';
  }
  html+='<span class="filter-count" id="roster-filter-count"></span>';
  html+='</div>';
  html+='<table class="data-table"><thead><tr>';
  for(var c2=0;c2<cols.length;c2++){
    var cls=rosterSort.col===cols[c2].k?'sorted':'';
    var arrow=rosterSort.col===cols[c2].k?(rosterSort.dir>0?'&#9650;':'&#9660;'):'&#9650;';
    html+='<th class="'+cls+'" onclick="sortRosterBy(\''+cols[c2].k+'\')">'+cols[c2].l+'<span class="sort-arrow">'+arrow+'</span></th>';
  }
  html+='</tr></thead><tbody id="roster-tbody"></tbody></table>';
  document.getElementById('content').innerHTML=html;
  renderRosterRows();
}

function renderRosterRows(){
  var agents=rosterData.filter(function(a){
    if(rosterFilters.search){
      var s=rosterFilters.search.toLowerCase();
      var hay=((a.role||'')+(a.department||'')+(a.assigned_ticket||'')+(a.name||'')).toLowerCase();
      if(hay.indexOf(s)<0)return false;
    }
    if(rosterFilters.department&&(a.department||a.dept||'-')!==rosterFilters.department)return false;
    if(rosterFilters.status&&(a.status||'unknown')!==rosterFilters.status)return false;
    if(rosterFilters.role&&(a.role||'?')!==rosterFilters.role)return false;
    return true;
  });
  var col=rosterSort.col,dir=rosterSort.dir;
  agents.sort(function(a,b){
    var va=getRosterVal(a,col),vb=getRosterVal(b,col);
    if(typeof va==='number'&&typeof vb==='number')return (va-vb)*dir;
    va=String(va).toLowerCase();vb=String(vb).toLowerCase();
    return va<vb?-1*dir:va>vb?1*dir:0;
  });
  var html='';
  for(var i=0;i<agents.length;i++){
    var a=agents[i];
    var st=a.status||'unknown';
    var dotClass='dot-stale';
    if(st==='active') dotClass='dot-active';
    else if(st==='claimed') dotClass='dot-claimed';
    else if(st==='retired'||st==='terminated'||st==='completed') dotClass='dot-retired';
    var rowStyle=(st==='retired'||st==='terminated'||st==='completed')?'opacity:0.5':'';
    html+='<tr style="'+rowStyle+'">'+
      '<td>'+esc(a.role||a.name||a.agent_id||'?')+'</td>'+
      '<td>'+esc(a.department||a.dept||'-')+'</td>'+
      '<td><span class="dot '+dotClass+'"></span>'+esc(st)+'</td>'+
      '<td>'+getAgentCycles(a)+'</td>'+
      '<td>'+esc(a.generation||a.gen||'-')+'</td>'+
      '<td>'+esc(a.message_count!=null?a.message_count:a.messages!=null?a.messages:'-')+'</td>'+
      '<td>'+(a.last_active?localTime(a.last_active):a.last_seen?localTime(a.last_seen):'-')+'</td>'+
      '<td>'+esc(a.assigned_ticket||a.ticket||'-')+'</td>'+
    '</tr>';
  }
  var el=document.getElementById('roster-tbody');
  if(el)el.innerHTML=html;
  var fc=document.getElementById('roster-filter-count');
  if(fc)fc.textContent='Showing '+agents.length+' of '+rosterData.length;
}

function getAgentCycles(a){
  var role=a.role||'';
  var dept=a.department||a.dept||'';
  var key=role+'_'+dept;
  var entry=cycleData[key];
  if(entry)return String(entry.cycles);
  // Try partial matches
  for(var k in cycleData){
    if(k.indexOf(role)>=0||k.indexOf(dept)>=0)return String(cycleData[k].cycles);
  }
  return '-';
}

function getRosterVal(a,col){
  if(col==='role')return a.role||a.name||a.agent_id||'';
  if(col==='department')return a.department||a.dept||'';
  if(col==='status')return a.status||'';
  if(col==='cycles'){var c=getAgentCycles(a);return c==='-'?0:parseInt(c)||0}
  if(col==='generation')return parseInt(a.generation||a.gen||0)||0;
  if(col==='messages')return parseInt(a.message_count||a.messages||0)||0;
  if(col==='last_active')return a.last_active||a.last_seen||'';
  if(col==='ticket')return a.assigned_ticket||a.ticket||'';
  return '';
}

// ===== 3. SPRINT =====
async function showSprint(){
  activeView='sprint';
  writeHash("sprint");
  stopAutoRefresh();
  setActiveNav(3);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading sprint data...</p>';
  var data=await api('sprint');
  if(data.error){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}
  var t=data.totals||{};
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Sprint Dashboard</span></div>';
  html+='<div class="summary-row">'+
    '<div class="summary-card"><div class="num">'+esc(t.total||0)+'</div><div class="lbl">Total Tickets</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+esc(t.done||0)+'</div><div class="lbl">Done</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#bc8cff">'+esc((t['in-progress']||0)+(t['in-review']||0))+'</div><div class="lbl">In Progress</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#58a6ff">'+esc(t['sprint-current']||0)+'</div><div class="lbl">Sprint Queue</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#8b949e">'+esc(t.backlog||0)+'</div><div class="lbl">Backlog</div></div>'+
  '</div>';
  var deptKeys=Object.keys(data.departments||{});
  for(var i=0;i<deptKeys.length;i++){
    var dk=deptKeys[i];
    var dd=data.departments[dk];
    var cnt=dd.counts||{};
    var total=cnt.total||0;
    var done=cnt.done||0;
    var inProg=(cnt['in-progress']||0)+(cnt['in-review']||0);
    var pctDone=total?Math.round(done/total*100):0;
    var pctProg=total?Math.round(inProg/total*100):0;
    var sprintId=dd.sprint&&dd.sprint.sprint_id?dd.sprint.sprint_id:'';
    var velocity=dd.sprint&&dd.sprint.velocity?dd.sprint.velocity:'';
    html+='<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:12px">';
    html+='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">';
    html+='<span style="font-size:14px;color:#f0f6fc;font-weight:600">'+esc(dk)+'</span>';
    html+='<span style="font-size:11px;color:#8b949e">';
    if(sprintId)html+='Sprint: '+esc(sprintId)+' ';
    if(velocity)html+='Velocity: '+esc(velocity)+' ';
    html+=esc(done)+'/'+esc(total)+' done</span></div>';
    html+='<div class="progress-bar">';
    if(pctDone>0)html+='<div class="progress-fill fill-done" style="width:'+pctDone+'%;display:inline-block;vertical-align:top">'+pctDone+'%</div>';
    if(pctProg>0)html+='<div class="progress-fill fill-progress" style="width:'+pctProg+'%;display:inline-block;vertical-align:top">'+pctProg+'%</div>';
    html+='</div>';
    html+='<div style="display:flex;gap:12px;margin-top:6px;font-size:11px;color:#8b949e">';
    html+='<span>Backlog: '+(cnt.backlog||0)+'</span>';
    html+='<span>Sprint: '+(cnt['sprint-current']||0)+'</span>';
    html+='<span>Active: '+inProg+'</span>';
    html+='<span style="color:#3fb950">Done: '+done+'</span>';
    html+='</div></div>';
  }
  c.innerHTML=html;
  startAutoRefresh(showSprint);
}

// ===== 4. RESEARCH =====
var rdData=null;
var rdFilter='';
var rdCurrentFile=null;

// ===== REPORTS (formerly R&D + Marketing) =====
// Aggregates every report-style document the company produces. Each entry
// shows structured metadata tags (dept, type, author, date) up top so the
// reader can scan provenance without opening the file.
var reportsData={reports:[],recommendations:''};
var reportsCurrentPath=null;
var reportsFilter='';
var reportsDeptFilter='';
var reportsTypeFilter='';

async function showReports(){
  activeView='reports';
  writeHash("reports");
  stopAutoRefresh();
  setActiveNav(6);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading reports...</p>';
  reportsData=await api('reports');
  reportsCurrentPath=null;
  renderReportsLayout();
}

function reportsFilterChange(val){reportsFilter=val;renderReportsList()}
function reportsDeptChange(val){reportsDeptFilter=val;renderReportsList()}
function reportsTypeChange(val){reportsTypeFilter=val;renderReportsList()}

function uniq(arr){var s={};var out=[];for(var i=0;i<arr.length;i++){if(arr[i]&&!s[arr[i]]){s[arr[i]]=1;out.push(arr[i])}}return out}

function renderReportsLayout(){
  var d=reportsData;
  var reports=d.reports||[];
  var depts=uniq(reports.map(function(r){return r.dept||'(none)'})).sort();
  var types=uniq(reports.map(function(r){return r.type||'report'})).sort();
  var deptOpts='<option value="">All depts</option>'+depts.map(function(x){return '<option value="'+esc(x)+'"'+(reportsDeptFilter===x?' selected':'')+'>'+esc(x)+'</option>'}).join('');
  var typeOpts='<option value="">All types</option>'+types.map(function(x){return '<option value="'+esc(x)+'"'+(reportsTypeFilter===x?' selected':'')+'>'+esc(x)+'</option>'}).join('');
  var html='<div style="margin-bottom:12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">'+
    '<span style="font-size:16px;color:#f0f6fc">Reports</span>'+
    '<span style="font-size:12px;color:#8b949e">'+reports.length+' total</span>'+
    (d.recommendations?'<button class="btn" style="margin-left:auto;font-size:11px" onclick="showReportsRecommendations()">R&amp;D Recommendations</button>':'')+
  '</div>';
  html+='<div class="filter-bar" style="margin-bottom:8px;display:flex;gap:6px;flex-wrap:wrap">'+
    '<input id="reports-search" placeholder="Search title / content..." style="flex:1;min-width:200px" oninput="reportsFilterChange(this.value)" value="'+esc(reportsFilter)+'">'+
    '<select onchange="reportsDeptChange(this.value)">'+deptOpts+'</select>'+
    '<select onchange="reportsTypeChange(this.value)">'+typeOpts+'</select>'+
  '</div>';
  html+='<div class="panel-layout" style="min-height:calc(100vh - 200px)">';
  html+='<div class="panel-left" id="reports-list" style="max-height:calc(100vh - 200px);overflow-y:auto"></div>';
  html+='<div class="panel-right" id="reports-detail" style="max-height:calc(100vh - 200px);overflow-y:auto"><div class="chat-empty">Select a report to read</div></div>';
  html+='</div>';
  document.getElementById('content').innerHTML=html;
  renderReportsList();
}

function reportTag(label,value,color){
  if(!value)return '';
  return '<span style="display:inline-block;background:#21262d;border:1px solid #30363d;color:'+(color||'#c9d1d9')+';font-size:10px;padding:1px 6px;border-radius:3px;margin-right:4px"><span style="color:#6e7681">'+esc(label)+':</span> '+esc(value)+'</span>';
}

function renderReportsList(){
  var reports=(reportsData.reports||[]).filter(function(r){
    if(reportsDeptFilter&&(r.dept||'(none)')!==reportsDeptFilter)return false;
    if(reportsTypeFilter&&(r.type||'report')!==reportsTypeFilter)return false;
    if(reportsFilter){
      var s=reportsFilter.toLowerCase();
      var hay=((r.title||'')+(r.file||'')+(r.author||'')+(r.dept||'')+(r.content||'')).toLowerCase();
      if(hay.indexOf(s)<0)return false;
    }
    return true;
  });
  var el=document.getElementById('reports-list');
  if(!el)return;
  var html='';
  for(var i=0;i<reports.length;i++){
    var r=reports[i];
    var active=reportsCurrentPath===r.path?' style="background:#21262d;border-left:2px solid #58a6ff"':'';
    var tags=reportTag('dept',r.dept||'')+reportTag('type',r.type||'')+(r.author?reportTag('by',r.author,'#3fb950'):'');
    html+='<div class="panel-item"'+active+' onclick="showReportDetail(\''+esc(r.path)+'\')">'+
      '<div style="font-size:12px;color:#f0f6fc;line-height:1.3">'+esc((r.title||r.file).substring(0,70))+'</div>'+
      '<div style="margin-top:3px">'+tags+'</div>'+
      '<div style="font-size:10px;color:#484f58;margin-top:3px">'+localDate(r.date)+' &middot; '+esc(r.file)+'</div>'+
    '</div>';
  }
  if(!reports.length)html='<div class="chat-empty">No reports match the filters</div>';
  el.innerHTML=html;
}

function showReportDetail(rpath){
  reportsCurrentPath=rpath;
  var r=(reportsData.reports||[]).find(function(x){return x.path===rpath});
  var el=document.getElementById('reports-detail');
  if(!el||!r)return;
  var meta='<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid #30363d">'+
    reportTag('dept',r.dept||'(none)')+
    reportTag('type',r.type||'report')+
    (r.author?reportTag('author',r.author,'#3fb950'):'')+
    reportTag('date',localTime(r.date))+
    reportTag('path',r.path,'#8b949e')+
  '</div>';
  el.innerHTML='<div style="padding:16px"><div style="font-size:14px;color:#f0f6fc;margin-bottom:6px">'+esc(r.title||r.file)+'</div>'+meta+'<div class="md-content">'+renderMarkdown(r.content)+'</div></div>';
  renderReportsList();
}

function showReportsRecommendations(){
  reportsCurrentPath='__recommendations__';
  var el=document.getElementById('reports-detail');
  if(!el)return;
  el.innerHTML='<div style="padding:16px"><div style="font-size:11px;color:#58a6ff;margin-bottom:8px">R&amp;D Technology Recommendations</div><div class="md-content">'+renderMarkdown(reportsData.recommendations||'')+'</div></div>';
  renderReportsList();
}

// ===== 5. LOGS =====
var currentLogFile=null;
async function showLogs(){
  activeView='logs';
  writeHash("logs");
  stopAutoRefresh();
  setActiveNav(7);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading logs...</p>';
  var files=await api('logs');
  if(!Array.isArray(files))files=[];
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">System Logs</span></div>';
  html+='<div class="panel-layout">';
  html+='<div class="panel-left" id="log-list">';
  if(files.length){
    for(var i=0;i<files.length;i++){
      var f=files[i];
      var sizeStr=f.size>1024*1024?(f.size/1024/1024).toFixed(1)+'M':f.size>1024?(f.size/1024).toFixed(1)+'K':f.size+'B';
      html+='<div class="panel-item" data-log="'+esc(f.file)+'" onclick="showLogFile(\''+esc(f.file)+'\')">'+
        '<span>'+esc(f.file)+'</span>'+
        '<span class="item-meta">'+sizeStr+'</span>'+
      '</div>';
    }
  } else {
    html+='<div class="chat-empty">No log files found</div>';
  }
  html+='</div>';
  html+='<div class="panel-right" id="log-view" style="padding:0"><div class="chat-empty">Select a log file</div></div>';
  html+='</div>';
  c.innerHTML=html;
  if(files.length)showLogFile(files[0].file);
  startAutoRefresh(function(){if(currentLogFile)showLogFile(currentLogFile)});
}

async function showLogFile(name){
  currentLogFile=name;
  writeHash("logs", name);
  document.querySelectorAll('#log-list .panel-item').forEach(function(el){
    el.classList.toggle('active',el.dataset.log===name);
  });
  var data=await api('logs/'+encodeURIComponent(name));
  var view=document.getElementById('log-view');
  view.innerHTML='<div style="padding:12px 16px;border-bottom:1px solid #30363d;font-size:13px;color:#f0f6fc">'+esc(name)+
    ' <span style="font-size:11px;color:#8b949e;margin-left:8px">'+esc(data.totalLines||0)+' total lines (showing last 200)</span></div>'+
    '<div class="log-viewer" id="log-content">'+esc(data.content||'')+'</div>';
  var lc=document.getElementById('log-content');
  if(lc)lc.scrollTop=lc.scrollHeight;
}

// ===== 6. PLANS =====
var currentPlanDept=null;
var currentPlanTab='plan';
// (Plans panel removed in 0.4.45 — under the sprint-staged lifecycle the
// VPs file backlog tickets and the CEO writes the per-sprint direction
// doc; nobody updates per-dept plan.md anymore. The panel was showing
// stale placeholders. The /api/departments/:dept/plan and /contracts
// endpoints stay for back-compat in case external clients hit them.)

// ===== 7. PROCESSES =====
var procData=[];
var procSort={col:'role',dir:1};
var procFilters={search:'',status:''};

async function showConfig(){
  // C2: Config & LLM panel — the resolved model the agents are talking to,
  // the last saved doctor dump (hive doctor writes .company/state/
  // doctor.json), and the last hive-llm-test result.
  activeView='config';
  writeHash("config");
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading config...</p>';
  var d=await api('config');
  // T3 (0.22): the endpoint groups each snapshot with its file mtime /
  // age ({data, mtime_ms, age_ms}) — the data is the dump, the age is
  // the file's freshness, and they travel together.
  var doc=d.doctor||{}, lt=d.llm_test||{};
  var docd=doc.data||{}, ltd=lt.data||{};
  var llm=docd.llm||{};
  // T2 (0.21): a read-only snapshot — the resolved config plus the last
  // doctor / llm-test results, refreshed on navigation. No config-change
  // SSE event exists (0.20), so the view says exactly that.
  var html='<div class="section-hdr" id="cfg-panel-hdr">Config &amp; LLM</div>';
  html+='<p style="color:#6e7681;font-size:12px;margin:-8px 0 12px">Read-only — refreshed when you open this view (there is no live config-change event).</p>';
  html+='<div class="summary-row">';
  html+='<div class="summary-card"><div class="num" style="font-size:15px">'+esc(llm.provider||'\u2014')+'</div><div class="lbl">provider</div></div>';
  html+='<div class="summary-card"><div class="num" style="font-size:15px">'+esc(llm.model||'\u2014')+'</div><div class="lbl">model</div></div>';
  html+='<div class="summary-card"><div class="num" style="font-size:15px">'+esc(llm.base_url||'\u2014')+'</div><div class="lbl">base_url</div></div>';
  html+='<div class="summary-card"><div class="num" style="font-size:15px">'+esc(llm.key_source||'\u2014')+'</div><div class="lbl">key source</div></div>';
  html+='</div>';
  if (llm.configured===false){
    html+='<p style="color:#f85149">LLM is NOT configured \u2014 run <code>hive config edit</code>, then <code>hive doctor</code>.</p>';
  }
  var okDoc=docd.ok;
  var col=okDoc?'#3fb950':'#f85149';
  var badge=okDoc?'PASS':'FAIL';
  // T3 (0.22): the snapshot's age renders next to the badge ('last run
  // 12:04:31, 41m ago') — 0.21 rendered the instant but not the age, so
  // a dump from an hour ago read as a current one.
  var docAge=(doc.age_ms!=null?'<span style="font-size:11px;color:#484f58;margin-left:8px">('+ageAgo(doc.age_ms)+')</span>':'');
  html+='<div class="section-hdr">Doctor \u2014 last run: <span style="color:'+col+';font-weight:700">'+badge+'</span>'+
        (docd.ts?'<span style="font-size:11px;color:#484f58;margin-left:8px">'+esc(localTime(docd.ts))+'</span>':'')+docAge+'</div>';
  var fails=(docd.checks||[]).filter(function(ch){return !ch.ok;});
  if (fails.length){
    html+='<table class="data-table"><tr><th>check</th><th>detail</th></tr>';
    for (var i=0;i<fails.length;i++){
      html+='<tr><td>'+esc(fails[i].name)+'</td><td>'+esc(fails[i].detail||'')+'</td></tr>';
    }
    html+='</table>';
    html+='<p style="font-size:11px;color:#8b949e">'+((docd.checks||[]).length-fails.length)+' of '+
          (docd.checks||[]).length+' checks passed</p>';
  } else {
    html+='<p style="color:#8b949e">all checks passed'+(docd.checks?' ('+docd.checks.length+')':'')+'</p>';
  }
  if (lt){
    var lok=ltd.ok?'\u2713':'\u2717';
    var ltAge=(lt.age_ms!=null?'<span style="font-size:11px;color:#484f58;margin-left:8px">('+ageAgo(lt.age_ms)+')</span>':'');
    html+='<div class="section-hdr">Last LLM test (hive llm test): <span style="color:'+(ltd.ok?'#3fb950':'#f85149')+';font-weight:700">'+lok+'</span>'+
          (ltd.ts?'<span style="font-size:11px;color:#484f58;margin-left:8px">'+esc(localTime(ltd.ts))+'</span>':'')+ltAge+'</div>';
    html+='<div class="detail-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px">';
    html+=df('provider',ltd.provider)+df('model',ltd.model)+df('base_url',ltd.base_url)+df('key source',ltd.key_source);
    html+=df('latency',(ltd.latency_ms!=null?ltd.latency_ms+' ms':''))+df('reply',ltd.reply||ltd.error||'');
    html+='</div>';
  } else {
    html+='<div class="section-hdr">Last LLM test</div><p style="color:#8b949e">never run \u2014 <code>hive llm test</code> round-trips the endpoint before a sprint can burn turns on it.</p>';
  }
  c.innerHTML=html;
}
async function showProcesses(){
  activeView='processes';
  writeHash("processes");
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading processes...</p>';
  var data=await api('processes');
  procData=data.processes||[];
  renderProcTable();
  startAutoRefresh(async function(){var f=saveFocus();var d=await api('processes');procData=d.processes||[];renderProcTable();restoreFocus(f)});
}

function sortProcBy(col){
  if(procSort.col===col)procSort.dir*=-1;
  else{procSort.col=col;procSort.dir=1}
  renderProcTable();
}

function procFilterChange(field,val){procFilters[field]=val;var f=saveFocus();renderProcTable();restoreFocus(f)}

function renderProcTable(){
  var procs=procData.filter(function(p){
    if(procFilters.search){
      var s=procFilters.search.toLowerCase();
      var hay=((p.role||'')+(p.department||'')).toLowerCase();
      if(hay.indexOf(s)<0)return false;
    }
    if(procFilters.status==='alive'&&!p.alive)return false;
    if(procFilters.status==='dead'&&p.alive)return false;
    return true;
  });
  var col=procSort.col,dir=procSort.dir;
  procs.sort(function(a,b){
    var va=getProcVal(a,col),vb=getProcVal(b,col);
    if(typeof va==='number'&&typeof vb==='number')return (va-vb)*dir;
    va=String(va).toLowerCase();vb=String(vb).toLowerCase();
    return va<vb?-1*dir:va>vb?1*dir:0;
  });
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Running Processes</span></div>';
  if(!procs.length){
    html+='<p style="color:#8b949e;text-align:center;padding:40px">No processes found in processes.json</p>';
    document.getElementById('content').innerHTML=html;return;
  }
  var aliveCount=0;
  for(var i=0;i<procs.length;i++){if(procs[i].alive)aliveCount++}
  html+='<div class="summary-row">'+
    '<div class="summary-card"><div class="num">'+procs.length+'</div><div class="lbl">Total</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+aliveCount+'</div><div class="lbl">Alive</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#f85149">'+(procData.length-aliveCount)+'</div><div class="lbl">Dead</div></div>'+
  '</div>';
  html+='<div class="filter-bar">';
  html+='<input placeholder="Search..." value="'+esc(procFilters.search)+'" oninput="procFilterChange(\'search\',this.value)">';
  html+='<select onchange="procFilterChange(\'status\',this.value)"><option value="">All</option><option value="alive"'+(procFilters.status==='alive'?' selected':'')+'>Alive</option><option value="dead"'+(procFilters.status==='dead'?' selected':'')+'>Dead</option></select>';
  if(procFilters.search||procFilters.status){html+='<span class="filter-clear" onclick="procFilters={search:\'\',status:\'\'};renderProcTable()">Clear</span>'}
  html+='<span class="filter-count">Showing '+procs.length+' of '+procData.length+'</span>';
  html+='</div>';
  var cols=[{k:'role',l:'Role'},{k:'department',l:'Department'},{k:'interval',l:'Interval'},{k:'pid',l:'PID'},{k:'status',l:'Status'},{k:'restarts',l:'Restarts'},{k:'started_at',l:'Started'}];
  html+='<table class="data-table"><thead><tr>';
  for(var c2=0;c2<cols.length;c2++){
    var cls=procSort.col===cols[c2].k?'sorted':'';
    var arrow=procSort.col===cols[c2].k?(procSort.dir>0?'&#9650;':'&#9660;'):'&#9650;';
    html+='<th class="'+cls+'" onclick="sortProcBy(\''+cols[c2].k+'\')">'+cols[c2].l+'<span class="sort-arrow">'+arrow+'</span></th>';
  }
  html+='</tr></thead><tbody>';
  for(var i=0;i<procs.length;i++){
    var p=procs[i];
    var alive=p.alive;
    var restarts=(typeof p.restart_count==='number')?p.restart_count:0;
    // dead=true is the supervisor's terminal state (restart budget
    // exhausted, company-health.sh) — distinct from a transient
    // not-alive that the supervisor will restart within ~30s.
    var statusCell='<span class="dot '+(alive?'dot-alive':'dot-dead')+'"></span>'+
      (alive?'Alive':(p.dead?'<span style="color:#f85149;font-weight:600">Dead (budget)</span>':'Dead'));
    html+='<tr>'+
      '<td>'+esc(p.role||p.name||'?')+'</td>'+
      '<td>'+esc(p.department||p.dept||'-')+'</td>'+
      '<td>'+esc(p.interval||p.schedule||'-')+'</td>'+
      '<td>'+esc(p.pid||p.PID||'-')+'</td>'+
      '<td>'+statusCell+'</td>'+
      '<td>'+(restarts>0?'<span style="color:'+(p.dead?'#f85149':'#d29922')+'">'+restarts+'</span>':'0')+'</td>'+
      '<td>'+(p.started_at?localTime(p.started_at):p.start_time?localTime(p.start_time):'-')+'</td>'+
    '</tr>';
  }
  html+='</tbody></table>';
  document.getElementById('content').innerHTML=html;
}

function getProcVal(p,col){
  if(col==='role')return p.role||p.name||'';
  if(col==='department')return p.department||p.dept||'';
  if(col==='interval')return p.interval||p.schedule||'';
  if(col==='pid')return parseInt(p.pid||p.PID||0)||0;
  if(col==='status')return p.alive?'alive':(p.dead?'dead-budget':'dead');
  if(col==='restarts')return (typeof p.restart_count==='number')?p.restart_count:0;
  if(col==='started_at')return p.started_at||p.start_time||'';
  return '';
}

// ===== 7b. AGENT ACTIVITY (live spawns + cycle history + per-spawn stream) =====
var activityData={active:[],cycles:[]};
var activityStream={spawnId:null,events:[],next:0,total:0,timer:null};
// T2 (0.22): maxLine tracks, per spawn_id, how many stream lines the
// client ALREADY HOLDS — the (spawn_id, line) dedupe key for both the
// pushed events and the catch-up fetches. The wire cursor (cursor) is
// the per-spawn fetch offset; the two must never disagree backwards.
var globalStream={open:false,events:[],cursor:'',total:0,active_count:0,maxLine:{}};
var actSort={col:'started_ts',dir:-1};       // for currently-running table
var actFilters={search:'',role:'',dept:'',source:'',status:''};
var cycSort={col:'ended_at',dir:-1};         // for recent cycles table
var cycFilters={search:'',role:'',dept:'',status:''};

async function showActivity(){
  activeView='activity';
  writeHash("activity");
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading agent activity...</p>';
  await refreshActivity();
  startAutoRefresh(refreshActivity);
}

async function refreshActivity(){
  // T2 (0.22): the stream viewers' DATA rides the SSE push channel — a
  // refresh here fetches only the TABLE's data (spawns/active + cycles:
  // the Events column, the live total, the metadata the viewer header
  // renders) and renders whatever is open. The stream cursor fetch is
  // demoted: it runs on viewer open and on SSE reconnect catch-up.
  var f=saveFocus();
  var a=await api('spawns/active').catch(function(){return{active:[]}});
  var h=await api('spawns/cycles?limit=200').catch(function(){return{cycles:[]}});
  activityData={active:a.active||[],cycles:h.cycles||[]};
  if(activityStream.spawnId){renderSpawnStream();}
  else if(globalStream.open){renderGlobalStream();}
  else{renderActivity();}
  restoreFocus(f);
}

function fmtElapsed(s){
  s=Math.max(0,Math.floor(s||0));
  if(s<60)return s+'s';
  if(s<3600)return Math.floor(s/60)+'m '+(s%60)+'s';
  return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
}

// B3: per-round cost formatting. 6 decimals — per-call costs are in the
// sub-cent range, so 2 decimals would render every round as $0.00.
function fmtCost(c){var v=parseFloat(c);if(isNaN(v))v=0;return v.toFixed(6)}

// Sort helpers shared with both tables
function actSortBy(col){if(actSort.col===col)actSort.dir*=-1;else{actSort.col=col;actSort.dir=1}var f=saveFocus();renderActivity();restoreFocus(f)}
function cycSortBy(col){if(cycSort.col===col)cycSort.dir*=-1;else{cycSort.col=col;cycSort.dir=1}var f=saveFocus();renderActivity();restoreFocus(f)}
function actFilterChange(field,val){actFilters[field]=val;var f=saveFocus();renderActivity();restoreFocus(f)}
function cycFilterChange(field,val){cycFilters[field]=val;var f=saveFocus();renderActivity();restoreFocus(f)}
function actClearFilters(){actFilters={search:'',role:'',dept:'',source:'',status:''};renderActivity()}
function cycClearFilters(){cycFilters={search:'',role:'',dept:'',status:''};renderActivity()}

// Get value for sorting / extracting unique sets
function getActVal(s,col){
  if(col==='started_ts'||col==='elapsed_s'||col==='stream_lines'||col==='pid')return Number(s[col]||0);
  if(col==='alive')return s.alive?1:0;
  return String(s[col]==null?'':s[col]).toLowerCase();
}
function getCycVal(c,col){
  if(col==='duration_s')return Number(c.duration_s||0);
  if(col==='num_turns')return Number((c.result||{}).num_turns||0);
  if(col==='status'){var r=c.result||{};return c.exit_code===0&&!r.is_error?'ok':(c.exit_code===0?'error':'exit')}
  if(col==='ended_at')return c.ended_at||'';
  if(col==='result')return ((c.result||{}).result_text||'').toLowerCase();
  return String(c[col]==null?'':c[col]).toLowerCase();
}

function thBtn(label,col,sortState,sorter){
  var arrow=sortState.col===col?(sortState.dir>0?'&#9650;':'&#9660;'):'&#9651;';
  var cls=sortState.col===col?' class="sorted"':'';
  return '<th'+cls+' onclick="'+sorter+'(\''+col+'\')">'+label+' <span class="sort-arrow">'+arrow+'</span></th>';
}

function uniqueVals(arr,key){var s={};for(var i=0;i<arr.length;i++){var v=arr[i][key];if(v!=null&&v!=='')s[v]=1}return Object.keys(s).sort()}

function selectFilter(field,val,opts,onchange){
  var html='<select onchange="'+onchange+'(\''+field+'\',this.value)"><option value="">All '+field+'</option>';
  for(var i=0;i<opts.length;i++){
    var o=opts[i],sel=val===o?' selected':'';
    html+='<option value="'+esc(o)+'"'+sel+'>'+esc(o)+'</option>';
  }
  return html+'</select>';
}

function renderActivity(){
  // Hide dead/orphaned spawns from "Currently running" — they're noise. Cycles
  // history is unaffected (it's the auditable record of what actually ran).
  var a=(activityData.active||[]).filter(function(s){return s.alive});
  var h=activityData.cycles;
  var totalEvents=0;
  for(var i=0;i<a.length;i++){totalEvents+=Number(a[i].stream_lines||0)}

  var html='<div style="margin-bottom:16px;display:flex;align-items:center;gap:12px">'+
    '<span style="font-size:16px;color:#f0f6fc">Agent Activity</span>'+
    '<button class="btn" style="margin-left:auto" onclick="openGlobalStream()" title="Combined live stream of every active spawn, color-coded by source">⊕ Watch Global</button>'+
  '</div>';
  html+='<div class="summary-row">'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+a.length+'</div><div class="lbl">Active spawns</div></div>'+
    '<div class="summary-card"><div class="num">'+totalEvents+'</div><div class="lbl">Total events (live)</div></div>'+
    '<div class="summary-card"><div class="num">'+h.length+'</div><div class="lbl">Recent cycles</div></div>'+
  '</div>';

  // ----- Live (active) -----
  html+='<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">Currently running</h3>';
  if(!a.length){
    html+='<p style="color:#8b949e;padding:12px">Nothing in flight. (agent-spawn.sh registers active spawns at start; if VPs are mid-turn but nothing shows here, the spawn pre-dates this hive version.)</p>';
  }else{
    // Filter
    var fa=a.filter(function(s){
      if(actFilters.search){var q=actFilters.search.toLowerCase();var hay=((s.role||'')+(s.agent||'')+(s.dept||'')+(s.ticket||'')+(s.spawn_id||'')).toLowerCase();if(hay.indexOf(q)<0)return false}
      if(actFilters.role&&(s.role||'')!==actFilters.role)return false;
      if(actFilters.dept&&(s.dept||'')!==actFilters.dept)return false;
      if(actFilters.source&&(s.source||'')!==actFilters.source)return false;
      if(actFilters.status==='alive'&&!s.alive)return false;
      if(actFilters.status==='dead'&&s.alive)return false;
      return true;
    });
    // Sort
    var col=actSort.col,dir=actSort.dir;
    fa.sort(function(x,y){var vx=getActVal(x,col),vy=getActVal(y,col);if(typeof vx==='number'&&typeof vy==='number')return (vx-vy)*dir;return vx<vy?-1*dir:vx>vy?1*dir:0});

    html+='<div class="filter-bar">';
    html+='<input placeholder="Search role/agent/dept/ticket/spawn_id" value="'+esc(actFilters.search)+'" oninput="actFilterChange(\'search\',this.value)">';
    html+=selectFilter('role',actFilters.role,uniqueVals(a,'role'),'actFilterChange');
    html+=selectFilter('dept',actFilters.dept,uniqueVals(a,'dept'),'actFilterChange');
    html+=selectFilter('source',actFilters.source,uniqueVals(a,'source'),'actFilterChange');
    html+='<select onchange="actFilterChange(\'status\',this.value)"><option value="">All status</option><option value="alive"'+(actFilters.status==='alive'?' selected':'')+'>Alive</option><option value="dead"'+(actFilters.status==='dead'?' selected':'')+'>Dead</option></select>';
    if(actFilters.search||actFilters.role||actFilters.dept||actFilters.source||actFilters.status)html+='<span class="filter-clear" onclick="actClearFilters()">Clear</span>';
    html+='<span class="filter-count">Showing '+fa.length+' of '+a.length+'</span>';
    html+='</div>';

    html+='<table class="data-table"><thead><tr>';
    html+=thBtn('Role','role',actSort,'actSortBy');
    html+=thBtn('Agent','agent',actSort,'actSortBy');
    html+=thBtn('Dept','dept',actSort,'actSortBy');
    html+=thBtn('Ticket','ticket',actSort,'actSortBy');
    html+=thBtn('Started','started_ts',actSort,'actSortBy');
    html+=thBtn('Elapsed','elapsed_s',actSort,'actSortBy');
    html+=thBtn('Events','stream_lines',actSort,'actSortBy');
    html+=thBtn('PID','pid',actSort,'actSortBy');
    html+=thBtn('Source','source',actSort,'actSortBy');
    html+='<th></th></tr></thead><tbody>';
    for(var i=0;i<fa.length;i++){
      var s=fa[i];
      var deadStyle=s.alive?'':'opacity:0.5;text-decoration:line-through';
      var srcBadge='<span style="color:#3fb950">tagged</span>';
      // T1 (0.21): address the spawn by the id the record can actually
      // name — spawn_id || id (0.20 orphan records carry only id). If
      // both are absent a button for "" would open a viewer that can
      // never address a stream, so it renders disabled with a title
      // naming the defect instead.
      var watchSid=s.spawn_id||s.id;
      var watchBtn=watchSid
        ? '<button class="btn" data-spawn-id="'+esc(watchSid)+'" onclick="openSpawnStream(this.dataset.spawnId)">Watch</button>'
        : '<button class="btn" disabled title="No spawn id on this record (id and spawn_id both missing) — the stream cannot be addressed">Watch</button>';
      html+='<tr style="'+deadStyle+'">'+
        '<td>'+esc(s.role||'?')+'</td>'+
        '<td>'+esc(s.agent||'?')+'</td>'+
        '<td>'+esc(s.dept||'?')+'</td>'+
        '<td>'+esc(s.ticket||'-')+'</td>'+
        '<td title="'+esc(s.started_at||'')+'">'+esc(localShort(s.started_at))+'</td>'+
        '<td data-elapsed-from="'+(s.started_ts||0)+'">'+fmtElapsed(s.elapsed_s)+'</td>'+
        '<td>'+(s.stream_lines||0)+'</td>'+
        '<td style="color:#8b949e" title="ppid='+(s.ppid||'?')+'">'+(s.pid||'-')+(s.alive?'':' ✗')+'</td>'+
        '<td>'+srcBadge+'</td>'+
        '<td>'+watchBtn+'</td>'+
      '</tr>';
    }
    html+='</tbody></table>';
  }

  // ----- History (cycles) -----
  html+='<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">Recent cycles (last '+h.length+')</h3>';
  if(!h.length){
    html+='<p style="color:#8b949e;padding:12px">No completed cycles yet (logs/cycles.jsonl is empty).</p>';
  }else{
    var fh=h.filter(function(c){
      if(cycFilters.search){var q=cycFilters.search.toLowerCase();var hay=((c.role||'')+(c.agent||'')+(c.dept||'')+(c.ticket||'')+((c.result||{}).result_text||'')).toLowerCase();if(hay.indexOf(q)<0)return false}
      if(cycFilters.role&&(c.role||'')!==cycFilters.role)return false;
      if(cycFilters.dept&&(c.dept||'')!==cycFilters.dept)return false;
      if(cycFilters.status){var r=c.result||{};var st=c.exit_code===0&&!r.is_error?'ok':(c.exit_code===0?'error':'exit');if(st!==cycFilters.status)return false}
      return true;
    });
    fh.sort(function(x,y){var vx=getCycVal(x,cycSort.col),vy=getCycVal(y,cycSort.col);if(typeof vx==='number'&&typeof vy==='number')return (vx-vy)*cycSort.dir;return vx<vy?-1*cycSort.dir:vx>vy?1*cycSort.dir:0});

    html+='<div class="filter-bar">';
    html+='<input placeholder="Search role/agent/dept/ticket/result" value="'+esc(cycFilters.search)+'" oninput="cycFilterChange(\'search\',this.value)">';
    html+=selectFilter('role',cycFilters.role,uniqueVals(h,'role'),'cycFilterChange');
    html+=selectFilter('dept',cycFilters.dept,uniqueVals(h,'dept'),'cycFilterChange');
    html+='<select onchange="cycFilterChange(\'status\',this.value)"><option value="">All status</option><option value="ok"'+(cycFilters.status==='ok'?' selected':'')+'>ok</option><option value="error"'+(cycFilters.status==='error'?' selected':'')+'>error</option><option value="exit"'+(cycFilters.status==='exit'?' selected':'')+'>exit !=0</option></select>';
    if(cycFilters.search||cycFilters.role||cycFilters.dept||cycFilters.status)html+='<span class="filter-clear" onclick="cycClearFilters()">Clear</span>';
    html+='<span class="filter-count">Showing '+fh.length+' of '+h.length+'</span>';
    html+='</div>';

    html+='<table class="data-table"><thead><tr>';
    html+=thBtn('Ended','ended_at',cycSort,'cycSortBy');
    html+=thBtn('Role','role',cycSort,'cycSortBy');
    html+=thBtn('Agent','agent',cycSort,'cycSortBy');
    html+=thBtn('Dept','dept',cycSort,'cycSortBy');
    html+=thBtn('Duration','duration_s',cycSort,'cycSortBy');
    html+=thBtn('Turns','num_turns',cycSort,'cycSortBy');
    html+=thBtn('Status','status',cycSort,'cycSortBy');
    html+=thBtn('Result','result',cycSort,'cycSortBy');
    html+='<th></th></tr></thead><tbody>';
    for(var i=0;i<fh.length;i++){
      var c=fh[i];
      var r=c.result||{};
      var statusColor=c.exit_code===0&&!r.is_error?'#3fb950':'#f85149';
      var statusLabel=c.exit_code===0?(r.is_error?'error':'ok'):'exit '+c.exit_code;
      var preview=esc((r.result_text||'').slice(0,120));
      html+='<tr>'+
        '<td title="'+esc(c.ended_at||'')+'">'+esc(localShort(c.ended_at))+'</td>'+
        '<td>'+esc(c.role||'?')+'</td>'+
        '<td>'+esc(c.agent||'?')+'</td>'+
        '<td>'+esc(c.dept||'?')+'</td>'+
        '<td>'+fmtElapsed(c.duration_s)+'</td>'+
        '<td>'+(r.num_turns||'-')+'</td>'+
        '<td style="color:'+statusColor+'">'+esc(statusLabel)+'</td>'+
        '<td style="color:#8b949e;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+preview+'">'+preview+'</td>'+
        '<td><button class="btn" data-spawn-id="'+esc(c.spawn_id||c.id)+'" onclick="openSpawnStream(this.dataset.spawnId)">Replay</button></td>'+
      '</tr>';
    }
    html+='</tbody></table>';
  }

  document.getElementById('content').innerHTML=html;
}

function openSpawnStream(spawnId){
  if(!spawnId)return;  // a spawn the record cannot name is not addressable
  activityStream={spawnId:spawnId,events:[],next:0,total:0,notFound:false};
  writeHash("activity", "spawn/"+spawnId);
  refreshSpawnStream();
}

function closeSpawnStream(){
  activityStream={spawnId:null,events:[],next:0,total:0,notFound:false};
  writeHash("activity");
  renderActivity();
}

// Serialize stream fetches. Without this, concurrent SSE-driven refreshes
// (process-change + cycle-change + spawn-stream-change can all fire within
// ~500 ms during an active spawn) call refreshSpawnStream in parallel. Each
// in-flight call sees the SAME activityStream.next cursor — the first
// hasn't returned to advance it — so all of them fetch the same window of
// events and APPEND them to activityStream.events. The render then shows
// every event N times. Guard with an in-flight flag + a pending-bit so
// late events still get one follow-up pass once the in-flight call lands.
var spawnStreamBusy=false, spawnStreamPending=false;
async function refreshSpawnStream(){
  if(!activityStream.spawnId)return;
  if(spawnStreamBusy){ spawnStreamPending=true; return; }
  spawnStreamBusy=true;
  try {
    do {
      spawnStreamPending=false;
      if(!activityStream.spawnId)break;
      // T2 (0.22): this fetch is the demoted catch-up path (open /
      // reconnect). Record the from it reads at; if a PUSH advances the
      // cursor while the fetch is in flight, the response's head overlaps
      // what the push already appended — slice the overlap away.
      var from=activityStream.next;
      var url='spawns/'+encodeURIComponent(activityStream.spawnId)+'/stream?from='+from+'&limit=500';
      var data=await api(url).catch(function(){return{events:[],next:from,total:activityStream.total}});
      // T1 (0.21): a missing stream file is a real state — the endpoint
      // answers 200 with error:'stream-not-found'; record it so the empty
      // state can say what is actually true.
      activityStream.notFound=!!(data&&data.error==='stream-not-found');
      if(data.events&&data.events.length){
        var skip=activityStream.next-from;
        var evs=data.events;
        if(skip>0)evs=(skip>=evs.length)?[]:evs.slice(skip);
        for(var i=0;i<evs.length;i++)activityStream.events.push(evs[i]);
      }
      if(data.next>activityStream.next)activityStream.next=data.next;
      if(data.total>activityStream.total)activityStream.total=data.total;
      renderSpawnStream();
    } while(spawnStreamPending);
  } finally {
    spawnStreamBusy=false;
  }
}

function renderSpawnStream(){
  // Look up the spawn's metadata so the header can show useful info instead
  // of just the opaque spawn id + event count. Live spawns live in
  // activityData.active; finished ones in activityData.cycles. Active wins
  // when both are present (race during transition).
  var meta=null, alive=false;
  var act=(activityData&&activityData.active)||[];
  for(var i=0;i<act.length;i++){
    if(act[i].spawn_id===activityStream.spawnId){ meta=act[i]; alive=!!act[i].alive; break; }
  }
  if(!meta){
    var cyc=(activityData&&activityData.cycles)||[];
    for(var j=0;j<cyc.length;j++){
      if(cyc[j].spawn_id===activityStream.spawnId){ meta=cyc[j]; break; }
    }
  }

  // ── Row 1: nav + identity ────────────────────────────────────────────
  var roleAgent = meta
    ? '<span style="color:#79c0ff">'+esc(meta.role||'?')+'</span> · <span style="color:#c9d1d9">'+esc(meta.agent||'?')+'</span>'
    : '<span style="color:#8b949e">unknown spawn</span>';
  var deptTicket = meta
    ? '<span style="color:#8b949e">'+esc(meta.dept||meta.department||'-')+'</span>'+
      (meta.ticket?' · <span style="color:#d2a8ff">'+esc(meta.ticket)+'</span>':'')
    : '';

  // ── Row 2: timing, status, counts ────────────────────────────────────
  var statusPill;
  if(alive){
    statusPill='<span style="color:#3fb950">● alive</span>';
  } else if(meta && typeof meta.exit_code==='number'){
    var ok=meta.exit_code===0 && !(meta.result&&meta.result.is_error);
    statusPill=ok
      ? '<span style="color:#3fb950">✓ done</span>'
      : '<span style="color:#f85149">✗ exit '+meta.exit_code+(meta.result&&meta.result.is_error?' (error)':'')+'</span>';
  } else {
    statusPill='<span style="color:#8b949e">○ no longer registered</span>';
  }

  var timing='';
  if(meta){
    if(alive){
      // Live: launched at + elapsed. The elapsed value gets a
      // data-elapsed-from attribute so tickAsync rewrites it every second
      // — the rest of the panel stays put.
      timing='launched '+esc(localTime(meta.started_at||''))+
             ' · elapsed <span data-elapsed-from="'+(meta.started_ts||0)+'">'+
               esc(fmtElapsed(meta.elapsed_s))+'</span>';
    } else if(meta.ended_at){
      // Done: ended at + duration (fixed, no live tick needed)
      timing='ended '+esc(localTime(meta.ended_at))+
             ' · ran '+esc(fmtElapsed(meta.duration_s));
    } else if(meta.started_at){
      timing='launched '+esc(localTime(meta.started_at));
    }
  }

  var turnsBadge=(meta && meta.result && meta.result.num_turns)
    ? ' · turns '+esc(String(meta.result.num_turns))
    : (meta && meta.num_turns ? ' · turns '+esc(String(meta.num_turns)) : '');

  var pidBadge=(meta && meta.pid)
    ? ' · <span style="color:#6e7681;font-family:monospace" title="ppid='+esc(String(meta.ppid||'?'))+'">pid '+esc(String(meta.pid))+'</span>'
    : '';

  var html='<div style="margin-bottom:12px">'+
    '<div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">'+
      '<button class="btn" onclick="closeSpawnStream()">← Back to activity</button>'+
      '<span style="font-size:14px">'+roleAgent+
        (deptTicket?' &nbsp; '+deptTicket:'')+'</span>'+
      '<span style="margin-left:auto;font-size:12px">'+statusPill+'</span>'+
    '</div>'+
    '<div style="font-size:12px;color:#8b949e">'+
      timing+
      ' · <span style="color:#c9d1d9">'+activityStream.total+' events</span>'+
      turnsBadge+
      pidBadge+
      ' · <span style="color:#6e7681;font-family:monospace" title="spawn id">'+esc(activityStream.spawnId)+'</span>'+
    '</div>'+
  '</div>';
  if(!activityStream.events.length){
    // T1 (0.21): when the fetch answered stream-not-found, the honest
    // state is "no stream file", not "the agent may still be generating
    // its first message" — a missing file for a live pid is worth one
    // honest sentence.
    var emptyMsg=activityStream.notFound
      ? 'No stream file for this spawn (logs/streams/'+esc(activityStream.spawnId)+'.jsonl is missing) — the spawn is registered but its stream has not been written, or the file was removed.'
      : 'No events yet (the agent may still be generating its first message).';
    html+='<p style="color:#8b949e;padding:12px">'+emptyMsg+'</p>';
    var _ss0=saveScroll();
    try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_ss0);}
    return;
  }
  html+='<div id="spawn-stream" style="display:flex;flex-direction:column;gap:6px;max-height:78vh;overflow:auto;padding:6px;background:#0d1117;border:1px solid #30363d;border-radius:6px">';
  // Render newest first: walk events in reverse but keep the original index
  // as the visible #N so a given event keeps its identifier across refreshes.
  for(var i=activityStream.events.length-1;i>=0;i--){
    var frag=renderStreamEvent(activityStream.events[i],i);
    if(frag) html+=frag;
  }
  html+='</div>';
  // Preserve scroll position: the innerHTML write wipes the DOM and the
  // browser resets scroll to the top.  Save state, restore (snapping to
  // bottom if the user was following the tail).
  var _ss=saveScroll();
  try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_ss);}
}

// ── Watch Global: combined stream of every active spawn ─────────────────────
function openGlobalStream(){
  globalStream={open:true,events:[],cursor:'',total:0,active_count:0,maxLine:{}};
  writeHash("activity", "global");
  refreshGlobalStream();
}
function closeGlobalStream(){
  globalStream={open:false,events:[],cursor:'',total:0,active_count:0,maxLine:{}};
  writeHash("activity");
  renderActivity();
}

// ── Gate Output: stdout/stderr of the deterministic gate run ──────────
var gateOutput={ticket:null,output:''};
async function openGateOutput(dept,ticket){
  gateOutput={dept:dept,ticket:ticket,gates:null,output:''};
  try {
    var gd=await api('gates/'+encodeURIComponent(dept)+'/'+encodeURIComponent(ticket));
    if(gateOutput.ticket!==ticket)return;  // stale response guard
    gateOutput.gates=(gd&&gd.data&&gd.data.gates)?gd.data.gates:null;
  } catch(e) {
    gateOutput.gates=null;
  }
  try {
    var data=await api('logs/gate-'+encodeURIComponent(ticket)+'.log');
    if(gateOutput.ticket!==ticket)return;  // stale response guard
    gateOutput.output=data.content||'';
  } catch(e) {
    gateOutput.output='(failed to load gate output)';
  }
  renderGateOutput();
}
function closeGateOutput(){
  gateOutput={dept:null,ticket:null,gates:null,output:''};
  renderVerification();
}
function renderGateOutput(){
  var tid=gateOutput.ticket;
  var html='<div style="margin-bottom:12px;display:flex;align-items:center;gap:12px">'+
    '<button class="btn" onclick="closeGateOutput()">← Back to verification</button>'+
    '<span style="font-size:14px;color:#7ee787">Gate run: <code>'+esc(tid)+'</code></span>'+
  '</div>';
  if(!gateOutput.gates && !gateOutput.output){
    html+='<p style="color:#8b949e;padding:12px">No gate report or output yet — gates may not have run for this ticket.</p>';
    var _sgo=saveScroll();
    try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_sgo);}
    return;
  }
  if(gateOutput.gates){
    var _order=['has-commits','tdd-order','tests-exist','tests-pass','lint-pass','security-clean'];
    var _names=Object.keys(gateOutput.gates);
    _order.forEach(function(g){ if(_names.indexOf(g)<0) _names.push(g); });
    var _chips='';
    _names.forEach(function(g){
      var _e=gateOutput.gates[g], _st=_e.status||'?';
      var _col=_st==='pass'?'#7ee787':(_st==='skip'?'#f0b429':'#ff7b72');
      var _icon=_st==='pass'?'✓':(_st==='skip'?'⚠':'✗');
      _chips+='<div style="margin:4px 0"><span style="color:'+_col+';font-weight:600">'+_icon+' '+esc(_st)+'</span>'+
        '<span style="color:#c9d1d9"> '+esc(g)+'</span>'+
        '<div style="color:#8b949e;font-size:11px;white-space:pre-wrap;word-break:break-word">'+esc(_e.detail||'')+'</div></div>';
    });
    html+='<div style="background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:8px 10px;margin-bottom:8px;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace">'+
      '<strong style="color:#7ee787;font-size:12px">Gates: <code>'+esc(tid)+'</code></strong>'+_chips+'</div>';
  }
  // Skips are not passes: a gate logged as SKIP (e.g. security-clean with
  // no audit tool installed) must stand out from the run's PASS summary,
  // and failures likewise - otherwise "never scanned" reads like "scanned
  // clean".
  var _skipLines=gateOutput.output.split('\n').filter(function(l){return /SKIP/.test(l);});
  var _failLines=gateOutput.output.split('\n').filter(function(l){return /FAIL|BOUNCE/.test(l);});
  if(_skipLines.length){
    html+='<div style="background:#3d2e00;border:1px solid #9e6a00;border-radius:6px;padding:8px 10px;margin-bottom:8px;color:#f0b429">'+
      '<strong style="font-size:12px">'+_skipLines.length+' gate(s) SKIPPED - not scanned</strong>'+
      '<pre style="margin:6px 0 0;padding:0;white-space:pre-wrap;word-break:break-word;font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:#f0b429">'+esc(_skipLines.join('\n'))+'</pre>'+
    '</div>';
  }
  if(_failLines.length){
    html+='<div style="background:#3d0d12;border:1px solid #b62324;border-radius:6px;padding:8px 10px;margin-bottom:8px;color:#ff7b72">'+
      '<strong style="font-size:12px">gate failure(s)</strong>'+
      '<pre style="margin:6px 0 0;padding:0;white-space:pre-wrap;word-break:break-word;font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:#ff7b72">'+esc(_failLines.join('\n'))+'</pre>'+
    '</div>';
  }
  if(gateOutput.output){
  // Parse the gate log line-by-line and highlight outcomes: PASS green,
  // SKIP amber, FAIL/BOUNCE red - the raw dump used to be one uncolored
  // <pre> where every outcome read the same.
  var _pre='';
  gateOutput.output.split('\n').forEach(function(line){
    var col='#c9d1d9';
    if(/BOUNCE|FAIL/.test(line))col='#ff7b72';
    else if(/SKIP/.test(line))col='#f0b429';
    else if(/PASS/.test(line))col='#7ee787';
    _pre+='<span style="display:block;color:'+col+'">'+esc(line)+'</span>';
  });
  html+='<pre style="margin:0;padding:8px 10px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;word-break:break-word;max-height:75vh;overflow-y:auto;overflow-x:auto">'+(_pre||esc(gateOutput.output))+'</pre>';
  }
  var _sgo2=saveScroll();
  try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_sgo2);}
}

// T2 (0.22): merge two base64-JSON cursors ({spawn_id: next_line}) by
// per-spawn max — the client's wire cursor must never go backwards (a
// server restart resets the push cursor; fetching from the HIGHER cursor
// is still correct because the (spawn_id, line) dedupe drops overlap).
function mergeGlobalCursor(a,b){
  var m={};
  function upd(c){
    if(!c)return;
    try{
      var o=JSON.parse(atob(c));
      if(o&&typeof o==='object'){
        for(var k in o){
          if(typeof o[k]==='number'&&o[k]>0&&(m[k]==null||o[k]>m[k]))m[k]=o[k];
        }
      }
    }catch(e){}
  }
  upd(a);upd(b);
  if(!Object.keys(m).length)return a||b||'';
  return btoa(JSON.stringify(m));
}

// Same concurrent-fetch hazard as refreshSpawnStream — guard identically.
var globalStreamBusy=false, globalStreamPending=false;
async function refreshGlobalStream(){
  if(!globalStream.open)return;
  if(globalStreamBusy){ globalStreamPending=true; return; }
  globalStreamBusy=true;
  try {
    do {
      globalStreamPending=false;
      if(!globalStream.open)break;
      var url='spawns/global-stream?limit=400'+(globalStream.cursor?'&cursor='+encodeURIComponent(globalStream.cursor):'');
      var data=await api(url).catch(function(){return{events:[],cursor:globalStream.cursor,total:globalStream.total,active_count:globalStream.active_count}});
      if(data.events&&data.events.length){
        // T2 (0.22): dedupe by (spawn_id, line) — a push landing while
        // this catch-up fetch is in flight overlaps the response tail.
        for(var i=0;i<data.events.length;i++){
          var w=data.events[i];
          var exp=globalStream.maxLine[w.spawn_id]||0;
          if(w.line>=exp){
            globalStream.events.push(w);
            globalStream.maxLine[w.spawn_id]=w.line+1;
            globalStream.total++;
          }
        }
      }
      if(data.cursor)globalStream.cursor=mergeGlobalCursor(globalStream.cursor,data.cursor);
      if(data.total>globalStream.total)globalStream.total=data.total;
      if(data.active_count!=null)globalStream.active_count=data.active_count;
      // cap retained events so the page doesn't grow without bound
      if(globalStream.events.length>1500)globalStream.events=globalStream.events.slice(-1500);
      renderGlobalStream();
    } while(globalStreamPending);
  } finally {
    globalStreamBusy=false;
  }
}
// Stable color hash per spawn_id so each agent gets a consistent stripe
function spawnColor(id){
  var palette=['#79c0ff','#d2a8ff','#7ee787','#ffa657','#ff7b72','#a5d6ff','#f0883e','#bc8cff','#3fb950','#d29922'];
  var h=0;for(var i=0;i<id.length;i++){h=(h*31+id.charCodeAt(i))|0}
  return palette[Math.abs(h)%palette.length];
}
function renderGlobalStream(){
  var html='<div style="margin-bottom:12px;display:flex;align-items:center;gap:12px">'+
    '<button class="btn" onclick="closeGlobalStream()">← Back to activity</button>'+
    '<span style="color:#8b949e">Global stream · '+globalStream.active_count+' active spawns · '+globalStream.events.length+' rendered events ('+globalStream.total+' total)</span>'+
  '</div>';
  if(!globalStream.events.length){
    html+='<p style="color:#8b949e;padding:12px">No events yet — waiting for spawns to emit data.</p>';
    var _gs=saveScroll();
    try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_gs);}
    return;
  }
  html+='<div id="global-stream" style="display:flex;flex-direction:column;gap:4px;max-height:80vh;overflow:auto;padding:6px;background:#0d1117;border:1px solid #30363d;border-radius:6px">';
  // Newest first — walk in reverse so the most recent emit is at the top.
  for(var i=globalStream.events.length-1;i>=0;i--){
    var w=globalStream.events[i];
    var ev=w.event||{type:'?'};
    // Skip thinking-token-count system events (subtype contains 'thinking').
    if((ev.type==='system') && (ev.subtype||'').indexOf('thinking')>=0) continue;
    var color=spawnColor(w.spawn_id||'');
    var label=esc((w.role||'?')+' · '+(w.agent||'?')+(w.dept&&w.dept!=='?'? ' / '+esc(w.dept):'')+(w.ticket?' · '+esc(w.ticket):'')+' · #'+w.line);
    html+='<div style="border-left:3px solid '+color+';padding:6px 8px;background:#0d1117">'+
      '<div style="font-size:10px;color:'+color+';margin-bottom:4px;font-family:ui-monospace,monospace">'+label+'</div>'+
      renderStreamEvent(ev,w.line)+
    '</div>';
  }
  html+='</div>';
  var _gs2=saveScroll();
  try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_gs2);}
}

// Render a string in a <pre> with proper wrapping, optionally collapsed by
// default if it's huge. textColor is applied to the <pre>.
function preBlock(text,textColor,collapseAbove){
  text=String(text==null?'':text);
  var color=textColor||'#c9d1d9';
  var preStyle='margin:0;padding:6px 8px;background:#010409;border:1px solid #21262d;border-radius:4px;color:'+color+';font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;word-break:break-word;overflow-x:auto;max-height:520px;overflow-y:auto';
  collapseAbove=collapseAbove||4000;
  if(text.length<=collapseAbove){
    return '<pre style="'+preStyle+'">'+esc(text)+'</pre>';
  }
  var head=text.slice(0,collapseAbove);
  return '<details><summary style="cursor:pointer;color:#8b949e;font-size:11px;margin-bottom:4px">show full ('+text.length.toLocaleString()+' chars)</summary><pre style="'+preStyle+'">'+esc(text)+'</pre></details><pre style="'+preStyle+'">'+esc(head)+(text.length>collapseAbove?'\n…':'')+'</pre>';
}

// Pretty-print JSON; on stringify failure return the raw value.
function prettyJson(obj){
  try { return JSON.stringify(obj,null,2); }
  catch(e) { try { return String(obj); } catch(_) { return '[unserialisable]'; } }
}

function renderStreamEvent(ev,idx){
  // agent-harness stream-json events: system/init, assistant
  // (with content blocks: text, tool_use), user (with tool_result), result.
  var t=ev.type||'?';
  var bg='#161b22',headerColor='#c9d1d9',label=t,body='';

  if(t==='system'){
    // Skip thinking-token-count events (SYSTEM:THINKING_TOKENS from the harness).
    // Keep the final thinking text block — that's an assistant message with a
    // thinking content block, handled above.
    if((ev.subtype||'').indexOf('thinking')>=0) return '';
    bg='#1c2128'; headerColor='#8b949e'; label='system:'+(ev.subtype||'');
    body=preBlock(prettyJson(ev),'#8b949e');
  }
  else if(t==='assistant'){
    bg='#0d1f2d'; headerColor='#79c0ff'; label='assistant';
    var msg=ev.message||{};
    var blocks=msg.content||[];
    var parts=[];
    for(var b=0;b<blocks.length;b++){
      var blk=blocks[b];
      if(blk.type==='text'){
        parts.push('<div style="font-size:11px;color:#79c0ff;margin:6px 0 4px">text</div>'+preBlock(blk.text||'','#c9d1d9'));
      }
      else if(blk.type==='tool_use'){
        var argsJson=prettyJson(blk.input==null?{}:blk.input);
        parts.push(
          '<div style="font-size:11px;color:#d2a8ff;margin:6px 0 4px"><b>tool_use</b> · '+esc(blk.name||'(unnamed)')+(blk.id?' <span style="color:#6e7681">'+esc(blk.id)+'</span>':'')+'</div>'+
          preBlock(argsJson,'#d2a8ff')
        );
      }
      else if(blk.type==='thinking'){
        parts.push('<div style="font-size:11px;color:#8b949e;margin:6px 0 4px;font-style:italic">thinking</div>'+preBlock(blk.thinking||'','#8b949e'));
      }
      else {
        parts.push('<div style="font-size:11px;color:#8b949e;margin:6px 0 4px">'+esc(blk.type||'unknown block')+'</div>'+preBlock(prettyJson(blk),'#8b949e'));
      }
    }
    body=parts.length ? parts.join('') : preBlock(prettyJson(ev),'#79c0ff');
  }
  else if(t==='user'){
    bg='#0d1f1c'; headerColor='#7ee787'; label='tool_result';
    var msg2=ev.message||{}; var blks2=msg2.content||[];
    var rparts=[];
    for(var b2=0;b2<blks2.length;b2++){
      var blk2=blks2[b2];
      if(blk2.type==='tool_result'){
        var content=blk2.content;
        var rendered='';
        if(typeof content==='string'){
          rendered=content;
        } else if(Array.isArray(content)){
          var pieces=[];
          for(var ci=0;ci<content.length;ci++){
            var c=content[ci];
            if(c && typeof c==='object' && typeof c.text==='string') pieces.push(c.text);
            else pieces.push(prettyJson(c));
          }
          rendered=pieces.join('\n---\n');
        } else {
          rendered=prettyJson(content);
        }
        var errMark=blk2.is_error?'<span style="color:#f85149;font-weight:bold">[error]</span> ':'';
        var idLine=blk2.tool_use_id?'<span style="color:#6e7681">id: '+esc(blk2.tool_use_id)+'</span>':'';
        rparts.push(
          '<div style="font-size:11px;margin:6px 0 4px">'+errMark+idLine+'</div>'+
          preBlock(rendered, blk2.is_error?'#f85149':'#7ee787')
        );
      } else {
        rparts.push('<div style="font-size:11px;color:#8b949e;margin:6px 0 4px">'+esc(blk2.type||'block')+'</div>'+preBlock(prettyJson(blk2),'#8b949e'));
      }
    }
    body=rparts.length ? rparts.join('') : preBlock(prettyJson(ev),'#7ee787');
  }
  else if(t==='result'){
    bg='#0d2818'; headerColor='#3fb950'; label='result';
    var summary='turns='+(ev.num_turns||'-')+' · duration='+Math.round((ev.duration_ms||0)/1000)+'s · cost=$'+((ev.total_cost_usd||0).toFixed(4))+(ev.is_error?' · <span style="color:#f85149">ERROR</span>':'');
    body='<div style="font-size:12px;color:#c9d1d9;margin-bottom:6px">'+summary+'</div>';
    if(ev.result) body+='<div style="font-size:11px;color:#3fb950;margin:6px 0 4px">final assistant message</div>'+preBlock(ev.result,'#c9d1d9');
    body+='<div style="font-size:11px;color:#8b949e;margin:8px 0 4px">raw event</div>'+preBlock(prettyJson(ev),'#8b949e');
  }
  else {
    body=preBlock(prettyJson(ev),'#8b949e');
  }

  return '<div style="background:'+bg+';border-left:3px solid '+headerColor+';padding:10px 12px;border-radius:4px;margin-bottom:6px">'+
    '<div style="font-size:11px;color:'+headerColor+';text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">#'+idx+' · '+esc(label)+'</div>'+
    body+
  '</div>';
}

// ===== 7c. ROUNDS (queue runner: current round, next plan, history) =========
var roundsData={current:null,next:null,history:[],totals:{calls:0,tokens:0,cost_usd:0},opened:null};

async function showRounds(){
  activeView='rounds';
  writeHash("rounds");
  stopAutoRefresh();
  setActiveNav(-1);
  document.getElementById('content').innerHTML='<p style="color:#8b949e">Loading rounds...</p>';
  await refreshRounds();
  startAutoRefresh(refreshRounds);
}

async function refreshRounds(){
  if(roundsData.opened){await refreshRoundDetail();return}
  var f=saveFocus();
  var cur  = await api('rounds/current').catch(function(){return{}});
  var nxt  = await api('rounds/next').catch(function(){return{}});
  var hist = await api('rounds/history?limit=30').catch(function(){return{rounds:[]}});
  roundsData={current:cur||null, next:nxt||null, history:(hist&&hist.rounds)||[], totals:(hist&&hist.totals)||{calls:0,tokens:0,cost_usd:0}, opened:null};
  renderRounds();
  restoreFocus(f);
}

function statusDot(st){
  var c='#8b949e';
  if(st==='running') c='#d29922';
  else if(st==='done') c='#3fb950';
  else if(st==='failed') c='#f85149';
  else if(st==='skipped') c='#6e7681';
  return '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+c+';margin-right:6px"></span>';
}

function renderRounds(){
  var c = roundsData.current || {};
  var n = roundsData.next || {};
  // If the current round is an empty shell (a fresh activity log with
  // sprint-lifecycle hasn't enqueued anything yet), display the previous
  // round's entries so the panel doesn't appear to wipe.
  var liveEntries = c.entries || [];
  var displayRound = c;
  var fellBack = false;
  if (!liveEntries.length && c.previous_round && (c.previous_round.entries||[]).length) {
    displayRound = c.previous_round;
    fellBack = true;
  }
  var entries = displayRound.entries || [];
  var cur = displayRound.cursor || 0;
  var counts = {queued:0, running:0, done:0, failed:0, skipped:0};
  for (var i=0;i<entries.length;i++){var s=entries[i].status||'queued';counts[s]=(counts[s]||0)+1}
  var nextEntries = (n && n.entries) || [];
  var stage = c.stage || '';
  // A1: 'idle' is a first-class stage (zero-ticket sprint waiting for
  // work) — label it as a state, not a hang.
  var stageLabel = (stage==='idle') ? 'idle — no work' : stage;
  var loopIter = (c.loop_iteration!=null) ? c.loop_iteration : '';

  var html='<div style="margin-bottom:14px"><span style="font-size:16px;color:#f0f6fc">Round Runner</span>'+
    (stageLabel?' <span style="color:#58a6ff;font-size:13px;margin-left:10px">stage: '+esc(stageLabel)+'</span>':'')+
    (loopIter!==''?' <span style="color:#8b949e;font-size:12px;margin-left:6px">(loop '+esc(String(loopIter))+')</span>':'')+
    '</div>';
  html+='<div class="summary-row">'+
    '<div class="summary-card"><div class="num">'+entries.length+'</div><div class="lbl">Entries '+(fellBack?'(prev round)':'this round')+'</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#d29922">'+counts.running+'</div><div class="lbl">Running</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+counts.done+'</div><div class="lbl">Done</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#f85149">'+(counts.failed||0)+'</div><div class="lbl">Failed</div></div>'+
    '<div class="summary-card"><div class="num">'+roundsData.history.length+'</div><div class="lbl">Recent rounds</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#d29922">$'+fmtCost(roundsData.totals?roundsData.totals.cost_usd:0)+'</div><div class="lbl">Sprint cost (history)</div></div>'+
  '</div>';

  // Current round (or previous, if the current shell is empty)
  var heading = fellBack ? 'Most recent round' : 'Current round';
  html+='<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">'+heading+
    (displayRound.round_id?(' · '+esc(displayRound.round_id)):'')+
    (displayRound.source?(' <span style="color:#8b949e;font-size:12px">['+esc(displayRound.source)+']</span>'):'')+
    (fellBack?' <span style="color:#8b949e;font-size:12px;margin-left:6px">(complete; awaiting next stage enqueue)</span>':'')+
    '</h3>';
  if(!entries.length){
    html+='<p style="color:#8b949e;padding:12px">No round in flight. Round-runner will bootstrap one on next iteration.</p>';
  } else {
    html+='<table class="data-table"><thead><tr><th></th><th>#</th><th>Kind</th><th>Agent</th><th>Dept</th><th>Role</th><th>Ticket</th><th>Status</th><th>Started</th><th>Duration</th><th>Queued by</th></tr></thead><tbody>';
    for (var i=0;i<entries.length;i++){
      var e=entries[i];
      var pointer = (!fellBack && i===cur) ? '<span style="color:#d29922">▶</span>' : ((!fellBack && i<cur) ? '<span style="color:#6e7681">·</span>' : '');
      var dur = '-';
      if (e.duration_s) dur = fmtElapsed(e.duration_s);
      else if (e.status==='running' && e.started_ts) dur = fmtElapsed(Math.floor(Date.now()/1000) - e.started_ts);
      html+='<tr style="'+((!fellBack && i<cur)?'opacity:0.55':'')+'">'+
        '<td>'+pointer+'</td>'+
        '<td style="color:#6e7681">'+i+'</td>'+
        '<td>'+esc(e.kind||'?')+'</td>'+
        '<td>'+esc(e.agent||'?')+'</td>'+
        '<td>'+esc(e.dept||'-')+'</td>'+
        '<td>'+esc(e.role||'-')+'</td>'+
        '<td>'+esc(e.ticket||'-')+'</td>'+
        '<td>'+statusDot(e.status)+esc(e.status||'queued')+'</td>'+
        '<td title="'+esc(e.started_at||'')+'">'+(e.started_at?esc(localShort(e.started_at)):'-')+'</td>'+
        '<td>'+dur+'</td>'+
        '<td style="color:#8b949e">'+esc(e.queued_by||'')+'</td>'+
      '</tr>';
    }
    html+='</tbody></table>';
  }

  // (The "Next round (CEO's pending plan)" section was removed — under the
  // sprint-staged lifecycle the CEO no longer plans agent turns. Stage-advance
  // enqueues each stage's entries directly.)

  // History
  html+='<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">History (last '+roundsData.history.length+')</h3>';
  if(!roundsData.history.length){
    html+='<p style="color:#8b949e;padding:12px">No completed rounds yet.</p>';
  } else {
    html+='<table class="data-table"><thead><tr><th>Round</th><th>Source</th><th>Started</th><th>Duration</th><th>Entries</th><th>Done</th><th>Skipped</th><th>Failed</th><th>Tokens</th><th>Cost</th><th></th></tr></thead><tbody>';
    for (var i=0;i<roundsData.history.length;i++){
      var r=roundsData.history[i];
      var sc=r.status_counts||{};
      html+='<tr>'+
        '<td>'+esc(r.round_id||'?')+'</td>'+
        '<td>'+esc(r.source||'-')+'</td>'+
        '<td title="'+esc(r.started_at||'')+'">'+esc(localShort(r.started_at))+'</td>'+
        '<td>'+(r.duration_s?fmtElapsed(r.duration_s):'-')+'</td>'+
        '<td>'+(r.entry_count||0)+'</td>'+
        '<td style="color:#3fb950">'+(sc.done||0)+'</td>'+
        '<td style="color:#6e7681">'+(sc.skipped||0)+'</td>'+
        '<td style="color:#f85149">'+(sc.failed||0)+'</td>'+
        '<td>'+(r.tokens!=null?r.tokens:'-')+'</td>'+
        '<td title="'+'cost from LLM usage log window (B3)'+'">'+(r.cost_usd!=null?('$'+fmtCost(r.cost_usd)):'-')+'</td>'+
        '<td><button class="btn" data-rid="'+esc(r.round_id)+'" onclick="openRound(this.dataset.rid)">View</button></td>'+
      '</tr>';
    }
    html+='</tbody></table>';
  }

  document.getElementById('content').innerHTML=html;
}

async function openRound(rid){
  roundsData.opened=rid;
  writeHash("rounds", rid);
  await refreshRoundDetail();
}
function closeRound(){
  roundsData.opened=null;
  writeHash("rounds");
  renderRounds();
}
async function refreshRoundDetail(){
  if(!roundsData.opened)return;
  var d = await api('rounds/'+encodeURIComponent(roundsData.opened)).catch(function(){return{error:'fetch failed'}});
  var html='<div style="margin-bottom:12px"><button class="btn" onclick="closeRound()">← Back to rounds</button> <span style="color:#8b949e;margin-left:12px">'+esc(roundsData.opened)+'</span></div>';
  if(d.error){html+='<p style="color:#f85149">'+esc(d.error)+'</p>';document.getElementById('content').innerHTML=html;return}
  if(d.rationale)html+='<p style="color:#c9d1d9;margin:6px 0 12px;font-style:italic">'+esc(d.rationale)+'</p>';
  var entries=d.entries||[];
  html+='<table class="data-table"><thead><tr><th>#</th><th>Kind</th><th>Agent</th><th>Dept</th><th>Role</th><th>Ticket</th><th>Status</th><th>Started</th><th>Duration</th><th>Exit</th></tr></thead><tbody>';
  for(var i=0;i<entries.length;i++){
    var e=entries[i];
    html+='<tr>'+
      '<td>'+i+'</td>'+
      '<td>'+esc(e.kind||'?')+'</td>'+
      '<td>'+esc(e.agent||'?')+'</td>'+
      '<td>'+esc(e.dept||'-')+'</td>'+
      '<td>'+esc(e.role||'-')+'</td>'+
      '<td>'+esc(e.ticket||'-')+'</td>'+
      '<td>'+statusDot(e.status)+esc(e.status||'?')+'</td>'+
      '<td>'+(e.started_at?esc(localShort(e.started_at)):'-')+'</td>'+
      '<td>'+(e.duration_s?fmtElapsed(e.duration_s):'-')+'</td>'+
      '<td>'+(e.exit_code==null?'-':e.exit_code)+'</td>'+
    '</tr>';
  }
  html+='</tbody></table>';
  document.getElementById('content').innerHTML=html;
}

// ===== 7d. GITHUB (mirrored issues + PRs) ===================================
var ghData = { issues: {}, prs: {} };

async function showGitHub(){
  activeView = 'github';
  writeHash("github");
  stopAutoRefresh();
  setActiveNav(-1);
  document.getElementById('content').innerHTML = '<p style="color:#8b949e">Loading GitHub...</p>';
  await refreshGitHub();
  startAutoRefresh(refreshGitHub);
}

async function refreshGitHub(){
  var f = saveFocus();
  var d = await api('gh/mirror').catch(function(){return {issues:{},prs:{}}});
  ghData = d || {issues:{},prs:{}};
  renderGitHub();
  restoreFocus(f);
}

function renderGitHub(){
  var issues = ghData.issues || {};
  var prs    = ghData.prs    || {};
  var repo   = ghData.repo || null;
  var diag   = ghData._diagnostics || {};
  var iKeys = Object.keys(issues).sort(function(a,b){return Number(b)-Number(a)});
  var pKeys = Object.keys(prs).sort(function(a,b){return Number(b)-Number(a)});
  // Discriminator: prefer the GH-truth state; fall back to merged_at /
  // closed_at for legacy sidecar entries.
  function prState(p){
    var s = (p.state || '').toLowerCase();
    if (s === 'merged') return 'merged';
    if (s === 'closed') return 'closed';
    if (s === 'open')   return 'open';
    return p.merged_at ? 'merged' : (p.closed_at ? 'closed' : 'open');
  }
  var openIssues   = iKeys.filter(function(k){return (issues[k].state||'').toLowerCase()==='open'}).length;
  var closedIssues = iKeys.length - openIssues;
  var openPrs   = pKeys.filter(function(k){return prState(prs[k])==='open'}).length;
  var mergedPrs = pKeys.filter(function(k){return prState(prs[k])==='merged'}).length;
  var closedPrs = pKeys.filter(function(k){return prState(prs[k])==='closed'}).length;
  var repoName = repo && repo.name;
  var failed = !!ghData._sync_error;

  // T3 (0.21): deep links are REAL (repo URL + /pull/N or /issues/N), or a
  // plain number when no remote is configured — 0.20 rendered a dead
  // anchor on every row instead.
  function linkNum(n, url){
    if (url) return '<a href="'+esc(url)+'" target="_blank" rel="noopener" style="color:#58a6ff">#'+esc(n)+'</a>';
    return '<span style="color:#8b949e">#'+esc(n)+'</span>';
  }
  function diagRow(label, ok, fix){
    return '<tr><td>'+esc(label)+'</td><td style="color:'+(ok?'#3fb950':'#f85149')+'">'+(ok?'ok':'FAILED')+'</td><td style="color:#8b949e">'+(ok?'':esc(fix))+'</td></tr>';
  }

  var html = '<div style="margin-bottom:14px;display:flex;align-items:center;gap:10px">'+
    '<span style="font-size:16px;color:#f0f6fc">GitHub'+(repoName?' — '+esc(repoName):'')+'</span>';
  if (failed) {
    html += '<span style="font-size:11px;color:#f85149" title="'+esc(ghData._sync_error)+'">⚠ '+esc(diag.error || ghData._sync_error)+'</span>';
  } else if (ghData._last_gh_sync_ms) {
    html += '<span style="font-size:11px;color:#6e7681">synced '+localShort(new Date(Number(ghData._last_gh_sync_ms)).toISOString())+'</span>';
  }
  html += '</div>';

  // T3 (0.21): the diagnostic block names WHICH check failed and the
  // actionable fix — 0.20 showed a warning badge over two empty tables.
  if (failed) {
    html += '<div style="border:1px solid rgba(248,81,73,0.25);background:rgba(248,81,73,0.05);border-radius:6px;padding:12px;margin-bottom:16px">'+
      '<div style="color:#f85149;font-weight:600;margin-bottom:8px;font-size:13px">GitHub sync is not working</div>'+
      '<table class="data-table"><thead><tr><th>Check</th><th>Status</th><th>Fix</th></tr></thead><tbody>'+
      diagRow('gh on PATH', !!diag.gh_on_path, 'install: https://cli.github.com') +
      diagRow('gh authenticated', !!diag.gh_authed, 'gh auth login') +
      diagRow('origin remote configured', !!diag.remote_configured, 'git remote add origin <url>') +
      '</tbody></table>'+
      (diag.stale ? '<div style="color:#8b949e;font-size:12px;margin-top:8px">Showing the last good sync (cached state).</div>' : '') +
      '</div>';
  }

  html += '<div class="summary-row">'+
    '<div class="summary-card"><div class="num">'+iKeys.length+'</div><div class="lbl">Issues</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+openIssues+'</div><div class="lbl">Issues open</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#6e7681">'+closedIssues+'</div><div class="lbl">Issues closed</div></div>'+
    '<div class="summary-card"><div class="num">'+pKeys.length+'</div><div class="lbl">PRs</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#d29922">'+openPrs+'</div><div class="lbl">PRs open</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#a371f7">'+mergedPrs+'</div><div class="lbl">PRs merged</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#6e7681">'+closedPrs+'</div><div class="lbl">PRs closed</div></div>'+
    '</div>';

  html += '<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">Issues</h3>';
  if (!iKeys.length) {
    // T3 (0.21): "0 issues on <owner>/<repo>" is a REAL FACT (the repo has
    // no issues); the diagnostic block above is the real-defect face.
    html += failed
      ? '<p style="color:#8b949e;padding:12px">No issue state available — see the diagnostic block above.</p>'
      : '<p style="color:#8b949e;padding:12px">0 issues on '+(repoName?esc(repoName):'this repo')+'.</p>';
  } else {
    html += '<table class="data-table"><thead><tr><th>#</th><th>Title</th><th>Hive ticket</th><th>State</th><th>Last synced</th></tr></thead><tbody>';
    for (var i=0;i<iKeys.length;i++){
      var k = iKeys[i], it = issues[k];
      var isOpen = (it.state||'').toLowerCase()==='open';
      html += '<tr>'+
        '<td>'+linkNum(k, it.url)+'</td>'+
        '<td>'+esc(it.title||'')+'</td>'+
        '<td>'+ (it.ticket ? esc(it.ticket) : '<span style="color:#6e7681">—</span>') +'</td>'+
        '<td style="color:'+(isOpen?'#3fb950':'#6e7681')+'">'+esc((it.state||'?').toLowerCase())+'</td>'+
        '<td title="'+esc(it.last_synced_at||'')+'">'+(it.last_synced_at?esc(localShort(it.last_synced_at)):'-')+'</td>'+
      '</tr>';
    }
    html += '</tbody></table>';
  }

  html += '<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">Pull Requests</h3>';
  if (!pKeys.length) {
    html += failed
      ? '<p style="color:#8b949e;padding:12px">No PR state available — see the diagnostic block above.</p>'
      : '<p style="color:#8b949e;padding:12px">0 PRs on '+(repoName?esc(repoName):'this repo')+' — PRs appear when gh-pr-open.sh opens one for a ticket in review.</p>';
  } else {
    html += '<table class="data-table"><thead><tr><th>PR</th><th>Title</th><th>Hive ticket</th><th>Branch</th><th>State</th><th>Opened</th><th>Closed/Merged</th></tr></thead><tbody>';
    for (var j=0;j<pKeys.length;j++){
      var pk = pKeys[j], pr = prs[pk];
      var st = prState(pr);
      var stColor = st==='merged' ? '#a371f7' : (st==='closed' ? '#6e7681' : '#d29922');
      var endTime = pr.merged_at || pr.closed_at;
      html += '<tr>'+
        '<td>'+linkNum(pk, pr.url)+'</td>'+
        '<td>'+esc(pr.title||'')+'</td>'+
        '<td>'+ (pr.ticket ? esc(pr.ticket) : '<span style="color:#6e7681">—</span>') +'</td>'+
        '<td style="color:#8b949e">'+esc(pr.branch||'-')+'</td>'+
        '<td style="color:'+stColor+'">'+esc(st)+'</td>'+
        '<td title="'+esc(pr.opened_at||'')+'">'+(pr.opened_at?esc(localShort(pr.opened_at)):'-')+'</td>'+
        '<td title="'+esc(endTime||'')+'">'+(endTime?esc(localShort(endTime)):'-')+'</td>'+
      '</tr>';
    }
    html += '</tbody></table>';
  }

  document.getElementById('content').innerHTML = html;
}


// ===== 7e. RELEASES (release records + history) =============================
var releaseData = { current: null, history: [] };

async function showReleases(){
  activeView = 'releases';
  writeHash("releases");
  stopAutoRefresh();
  setActiveNav(-1);
  document.getElementById('content').innerHTML = '<p style="color:#8b949e">Loading releases...</p>';
  await refreshReleases();
  startAutoRefresh(refreshReleases);
}

async function refreshReleases(){
  var f = saveFocus();
  var c = await api('releases/current').catch(function(){return null});
  var h = await api('releases/history?limit=30').catch(function(){return {releases:[]}});
  releaseData = { current: c, history: (h && h.releases) || [] };
  renderReleases();
  restoreFocus(f);
}

function verdictColor(v){
  if (v === 'GO') return '#3fb950';
  if (v === 'NO-GO') return '#f85149';
  return '#8b949e';
}
function phaseColor(p){
  if (p === 'done') return '#3fb950';
  if (p === 'failed') return '#f85149';
  if (p === 'adversarial') return '#d29922';
  return '#a371f7';
}

function renderReleases(){
  var c = releaseData.current;
  var html = '<div style="margin-bottom:14px"><span style="font-size:16px;color:#f0f6fc">Releases</span></div>';

  html += '<h3 style="color:#f0f6fc;margin:6px 0 8px;font-size:14px">Current</h3>';
  if (!c || c.error) {
    html += '<p style="color:#8b949e;padding:12px">No release in flight. CEO can request one during standup-synth by including a <code>release-cycle vX.Y.Z</code> entry in the next plan.</p>';
  } else {
    var pc = phaseColor(c.phase || '');
    var vc = verdictColor(c.adversarial_verdict || '');
    html += '<div style="background:#161b22;border-left:3px solid '+pc+';padding:10px 14px;border-radius:4px">'+
      '<div style="font-size:14px;color:#f0f6fc;margin-bottom:6px"><b>'+esc(c.version||'?')+'</b> '+
        '<span style="color:#8b949e">phase=</span><span style="color:'+pc+'">'+esc(c.phase||'?')+'</span> '+
        '<span style="color:#8b949e">verdict=</span><span style="color:'+vc+'">'+esc(c.adversarial_verdict||'pending')+'</span>'+
      '</div>'+
      '<div style="font-size:12px;color:#c9d1d9">started '+esc(localShort(c.started_at||''))+
        (c.ended_at?' · ended '+esc(localShort(c.ended_at)):'')+'</div>'+
      (c.release_url ? '<div style="margin-top:6px"><a href="'+esc(c.release_url)+'" target="_blank" style="color:#58a6ff">'+esc(c.release_url)+'</a></div>' : '')+
      (c.tarball_sha256 ? '<div style="font-size:11px;color:#6e7681;margin-top:4px;font-family:ui-monospace,monospace">sha256: '+esc(c.tarball_sha256)+'</div>' : '')+
      (c.blockers && c.blockers.length ? '<div style="margin-top:8px;color:#f85149"><b>Blockers:</b><ul>'+c.blockers.map(function(b){return '<li>'+esc(b)+'</li>'}).join('')+'</ul></div>' : '')+
      (c.go_rationale ? '<div style="margin-top:8px;color:#3fb950"><b>GO rationale:</b> '+esc(c.go_rationale)+'</div>' : '')+
      (c.nogo_rationale ? '<div style="margin-top:8px;color:#f85149"><b>NO-GO rationale:</b> '+esc(c.nogo_rationale)+'</div>' : '')+
    '</div>';
  }

  // History — sourced from sprint archive AND gh release list (merged on
  // version tag). Each row shows where the data came from so it's obvious
  // when GitHub has a release that the sprint archive missed (or vice versa).
  html += '<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">History (last '+releaseData.history.length+')</h3>';
  if (!releaseData.history.length) {
    html += '<p style="color:#8b949e;padding:12px">No releases recorded. (If you expected GitHub releases here, check that <code>gh auth status</code> works in this project — the dashboard pulls from <code>gh release list</code>.)</p>';
  } else {
    function statusColor(s){
      if(s==='published')return '#3fb950';
      if(s==='failed')return '#f85149';
      if(s==='draft')return '#d29922';
      if(s==='prerelease')return '#a371f7';
      return '#8b949e';
    }
    function sourceBadge(src){
      if(!src)return '';
      var tip='';
      if(src.indexOf('gh')>=0&&src.indexOf('sprint')>=0) tip='Both sources agree: sprint archive recorded the release AND GitHub has it published.';
      else if(src==='gh') tip='Found on GitHub but NOT in the sprint archive (release happened outside the sprint-staged pipeline, or the sprint state was lost).';
      else if(src==='sprint-archive') tip='Recorded by sprint archive but NOT on GitHub yet (publish step may have failed, or no remote configured).';
      var color=src==='gh'?'#d29922':(src==='sprint-archive'?'#8b949e':'#3fb950');
      return '<span title="'+esc(tip)+'" style="color:'+color+';font-size:10px;border:1px solid '+color+';padding:1px 5px;border-radius:3px;margin-left:6px">'+esc(src)+'</span>';
    }
    html += '<table class="data-table"><thead><tr><th>Version</th><th>Status</th><th>Sprint</th><th>Published</th><th>Tarball</th><th>Source</th></tr></thead><tbody>';
    for (var i=0;i<releaseData.history.length;i++){
      var r = releaseData.history[i];
      var sc = statusColor(r.status||'');
      var when = r.gh_published_at || r.ended || '';
      html += '<tr>'+
        '<td><b>'+esc(r.version||'?')+'</b></td>'+
        '<td style="color:'+sc+'">'+esc(r.status||'unknown')+'</td>'+
        '<td style="color:#8b949e;font-size:11px">'+esc(r.sprint_id||'-')+'</td>'+
        '<td title="'+esc(when)+'">'+(when?esc(localShort(when)):'-')+'</td>'+
        '<td style="color:#6e7681;font-family:ui-monospace,monospace;font-size:10px">'+esc(r.tarball||'-')+'</td>'+
        '<td>'+sourceBadge(r.source)+'</td>'+
      '</tr>';
    }
    html += '</tbody></table>';
  }

  document.getElementById('content').innerHTML = html;
}


// ===== 9. MARKETING =====
var currentMarketingTab='reports';
async function showMarketing(){
  activeView='marketing';
  writeHash("marketing");
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading marketing data...</p>';
  var data=await api('marketing');
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Marketing</span></div>';
  html+='<div class="tab-bar">'+
    '<button class="tab-btn'+(currentMarketingTab==='reports'?' active':'')+'" onclick="currentMarketingTab=\'reports\';showMarketing()">Reports ('+((data.reports||[]).length)+')</button>'+
    '<button class="tab-btn'+(currentMarketingTab==='output'?' active':'')+'" onclick="currentMarketingTab=\'output\';showMarketing()">Output ('+((data.output||[]).length)+')</button>'+
  '</div>';
  var files=currentMarketingTab==='reports'?(data.reports||[]):(data.output||[]);
  if(!files.length){
    html+='<p style="color:#8b949e;text-align:center;padding:40px">No '+currentMarketingTab+' files found</p>';
  } else {
    for(var i=0;i<files.length;i++){
      var f=files[i];
      var sizeStr=f.size>1024?(f.size/1024).toFixed(1)+'K':f.size+'B';
      html+='<div class="panel-item" style="background:#161b22;border:1px solid #30363d;border-radius:6px;margin:6px 0;cursor:pointer" onclick="showMarketingFile(\''+esc(currentMarketingTab)+'\',\''+esc(f.file)+'\')">'+
        '<span style="color:#f0f6fc">'+esc(f.file)+'</span>'+
        '<span class="item-meta">'+localDate(f.modified)+' &middot; '+sizeStr+'</span>'+
      '</div>';
    }
  }
  c.innerHTML=html;
}

async function showMarketingFile(type,file){
  var data=await api('marketing/'+encodeURIComponent(type)+'/'+encodeURIComponent(file));
  var m=document.getElementById('modal');
  m.innerHTML='<h2>'+esc(file)+'</h2>'+
    '<div class="md-content" style="max-height:60vh;overflow-y:auto">'+renderMarkdown(data.content||'')+'</div>'+
    '<div class="actions"><button class="btn" onclick="closeModal()">Close</button></div>';
  openModal();
}

// ===== 10. GIT =====
async function showGit(){
  activeView='git';
  writeHash("git");
  stopAutoRefresh();
  setActiveNav(-1);
  document.getElementById('content').innerHTML='<p style="color:#8b949e">Loading git info...</p>';
  await refreshGit();
  // T3 (0.22): the git panel is LIVE — the refresher is registered so
  // the SSE events can re-render it (ticket-change: worktrees come and
  // go with ticket state; git-change: an agent commit or a worktree file
  // write, from the server's .git/HEAD + refs + .worktrees watchers).
  // 0.21 named the ticket-change wiring but registered no refresher —
  // refreshActive('git') no-oped and the panel froze until navigation.
  startAutoRefresh(refreshGit);
}

async function refreshGit(){
  var f=saveFocus();
  var data=await api('git').catch(function(){return {error:'git unavailable',log:[]}});
  if(data.error&&!data.log){
    document.getElementById('content').innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';
  } else {
    renderGitPanel(data);
  }
  restoreFocus(f);
}

function renderGitPanel(data){
  var c=document.getElementById('content');
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Git Repository</span></div>';

  html+='<div style="margin-bottom:16px">';
  html+='<span style="font-size:13px;color:#8b949e">Current branch: </span>';
  html+='<span class="branch-badge current">'+esc(data.current_branch||'unknown')+'</span>';
  html+='</div>';

  if(data.branches&&data.branches.length>1){
    html+='<details style="margin-bottom:16px"><summary class="section-hdr" style="cursor:pointer;user-select:none">Branches ('+data.branches.length+')</summary>';
    html+='<div style="margin-top:8px;display:flex;flex-direction:column;gap:2px">';
    for(var i=0;i<data.branches.length;i++){
      var b=data.branches[i];
      var isCurrent=b===data.current_branch;
      html+='<a href="#" onclick="showBranchHistory(\''+esc(b)+'\');return false" class="branch-badge'+(isCurrent?' current':'')+'" style="text-decoration:none;width:fit-content">'+esc(b)+'</a>';
    }
    html+='</div></details>';
  }

  // Worktrees
  var wts=data.worktrees||[];
  if(wts.length){
    html+='<h3 class="section-hdr">Worktrees ('+wts.length+')</h3>';
    html+='<table class="data-table"><thead><tr><th>Branch</th><th>Ticket</th><th>Path</th><th>Status</th><th title="Files changed vs main, plus uncommitted edits">Files</th><th title="Commits the branch has that main does not">Commits</th><th>HEAD</th></tr></thead><tbody>';
    for(var i=0;i<wts.length;i++){
      var w=wts[i];
      var statusBadge='';
      if(w.isMain)statusBadge='<span class="branch-badge current">main</span>';
      else if(w.detached)statusBadge='<span style="color:#f0883e;font-size:11px">detached</span>';
      else if(w.dirty)statusBadge='<span style="color:#d29922;font-size:11px">dirty</span>';
      else statusBadge='<span style="color:#3fb950;font-size:11px">clean</span>';
      var wtClickName=esc(w.branch||(w.path||'').split('/').pop()||'');
      // Files cell: total + breakdown tooltip (e.g. "5 (3 committed + 2 unstaged)")
      var filesCell='-';
      if(w.changedFiles>0){
        var breakdown=[];
        if(w.committedFiles>0) breakdown.push(w.committedFiles+' committed');
        if(w.uncommittedFiles>0) breakdown.push(w.uncommittedFiles+' unstaged');
        var tip=breakdown.join(' + ');
        var color=w.uncommittedFiles>0?'#d29922':'#58a6ff';
        filesCell='<span style="color:'+color+'" title="'+tip+'">'+w.changedFiles+'</span>';
      }
      var commitsCell=w.aheadCommits>0
        ? '<span style="color:#58a6ff" title="Commits ahead of main">'+w.aheadCommits+'</span>'
        : '-';
      html+='<tr class="wt-clickable" onclick="showWorktree(\''+wtClickName+'\',\''+esc(w.path||'')+'\',\''+esc(w.branch||'')+'\',\''+esc(w.ticket||'')+'\',\''+esc((w.head||'').slice(0,8))+'\','+!!w.isMain+')" style="'+(w.isMain?'background:#161b22':'')+'" title="Click to browse this worktree">';
      html+='<td><span class="branch-badge'+(w.isMain?' current':'')+'">'+esc(w.branch||'?')+'</span></td>';
      html+='<td>'+(w.ticket?'<span style="color:#58a6ff">'+esc(w.ticket)+'</span>':'-')+'</td>';
      html+='<td style="font-size:11px;color:#8b949e;max-width:300px;overflow:hidden;text-overflow:ellipsis">'+esc(w.path||'')+'</td>';
      html+='<td>'+statusBadge+'</td>';
      html+='<td>'+filesCell+'</td>';
      html+='<td>'+commitsCell+'</td>';
      html+='<td style="font-size:10px;color:#484f58">'+esc((w.head||'').slice(0,8))+'</td>';
      html+='</tr>';
    }
    html+='</tbody></table>';
  }

  html+='<h3 class="section-hdr">Recent Commits</h3>';
  var log=data.log||[];
  if(log.length){
    html+='<div class="timeline">';
    for(var i=0;i<log.length;i++){
      var cm=log[i];
      html+='<div class="timeline-item" style="cursor:pointer" onclick="showCommitDiff(\''+esc(cm.hash)+'\')">'+
        '<span class="commit-hash">'+esc(cm.hash)+'</span>'+
        '<span class="commit-msg">'+esc(cm.message)+'</span>'+
        '<span class="commit-date">'+localDate(cm.date)+'</span>'+
      '</div>';
    }
    html+='</div>';
  } else {
    html+='<p style="color:#8b949e;font-size:13px">No commits found</p>';
  }
  c.innerHTML=html;
}

// ===== WORKTREE BROWSER =====
var wtCurrentName='';
var wtCurrentPath='';
var wtCurrentBranch='';
var wtCurrentTicket='';
var wtCurrentHead='';
var wtCurrentTab='files';
var wtBrowsePath='';
var wtDiffData=null;
var wtExpandedDiffs={};

function getFileIcon(name) {
  var ext = (name || '').split('.').pop().toLowerCase();
  if (ext === 'ts' || ext === 'tsx') return '<span style="color:#3178c6">TS</span>';
  if (ext === 'js' || ext === 'jsx') return '<span style="color:#f7df1e">JS</span>';
  if (ext === 'json') return '<span style="color:#8b949e">{}</span>';
  if (ext === 'md') return '<span style="color:#58a6ff">MD</span>';
  if (ext === 'css' || ext === 'scss') return '<span style="color:#563d7c">CS</span>';
  if (ext === 'html') return '<span style="color:#e44d26">HT</span>';
  if (ext === 'py') return '<span style="color:#3572a5">PY</span>';
  if (ext === 'rs') return '<span style="color:#dea584">RS</span>';
  if (ext === 'go') return '<span style="color:#00add8">GO</span>';
  if (ext === 'sh' || ext === 'bash') return '<span style="color:#89e051">SH</span>';
  if (ext === 'yml' || ext === 'yaml') return '<span style="color:#cb171e">YM</span>';
  if (ext === 'toml') return '<span style="color:#9c4221">TM</span>';
  if (ext === 'svg' || ext === 'png' || ext === 'jpg' || ext === 'gif') return '<span style="color:#a371f7">IM</span>';
  return '<span style="color:#8b949e">--</span>';
}

function formatFileSize(bytes) {
  if (bytes === 0) return '';
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return bytes + ' B';
}

async function showWorktree(name, wtpath, branch, ticket, head, isMain) {
  activeView='worktree';
  stopAutoRefresh();
  setActiveNav(-1);
  wtCurrentName = name;
  wtCurrentPath = wtpath || '';
  wtCurrentBranch = branch || name;
  wtCurrentTicket = ticket || '';
  wtCurrentHead = head || '';
  wtCurrentTab = 'files';
  wtBrowsePath = '';
  wtDiffData = null;
  wtExpandedDiffs = {};
  renderWorktreeView();
  loadWorktreeFiles('');
}

function renderWorktreeView() {
  var c = document.getElementById('content');
  var html = '';
  // Header bar
  html += '<div class="wt-header">';
  html += '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">';
  html += '<button class="btn" onclick="showGit()">&larr; Back to Git</button>';
  html += '<span class="branch-badge">'+esc(wtCurrentBranch)+'</span>';
  if (wtCurrentTicket) html += '<span style="color:#58a6ff;font-size:13px">'+esc(wtCurrentTicket)+'</span>';
  if (wtCurrentHead) html += '<span style="font-size:11px;color:#484f58">'+esc(wtCurrentHead)+'</span>';
  html += '</div>';
  if (wtCurrentPath) html += '<div style="font-size:11px;color:#484f58;margin-top:6px">'+esc(wtCurrentPath)+'</div>';
  html += '</div>';
  // Tab bar
  html += '<div class="wt-tabs">';
  html += '<button class="wt-tab'+(wtCurrentTab==='files'?' active':'')+'" onclick="switchWtTab(\'files\')">Files</button>';
  html += '<button class="wt-tab'+(wtCurrentTab==='changes'?' active':'')+'" onclick="switchWtTab(\'changes\')">Changes</button>';
  html += '</div>';
  // Content area
  html += '<div id="wt-content"><p style="color:#8b949e">Loading...</p></div>';
  c.innerHTML = html;
}

function switchWtTab(tab) {
  wtCurrentTab = tab;
  // Update tab highlight without full re-render
  document.querySelectorAll('.wt-tab').forEach(function(t) {
    t.classList.toggle('active', t.textContent.toLowerCase().indexOf(tab) === 0);
  });
  if (tab === 'files') {
    loadWorktreeFiles(wtBrowsePath);
  } else {
    loadWorktreeDiff();
  }
}

function buildBreadcrumb(browsePath) {
  var html = '<div class="breadcrumb">';
  html += '<a onclick="browseWorktreeDir(\'\')">root</a>';
  if (browsePath) {
    var parts = browsePath.split('/');
    var accumulated = '';
    for (var i = 0; i < parts.length; i++) {
      accumulated += (i > 0 ? '/' : '') + parts[i];
      html += ' / ';
      if (i === parts.length - 1) {
        html += '<span>' + esc(parts[i]) + '</span>';
      } else {
        html += '<a onclick="browseWorktreeDir(\'' + esc(accumulated) + '\')">' + esc(parts[i]) + '</a>';
      }
    }
  }
  html += '</div>';
  return html;
}

async function loadWorktreeFiles(browsePath) {
  wtBrowsePath = browsePath;
  var content = document.getElementById('wt-content');
  if (!content) return;
  content.innerHTML = '<p style="color:#8b949e">Loading files...</p>';
  var data = await api('git/worktrees/' + encodeURIComponent(wtCurrentName) + '/files?path=' + encodeURIComponent(browsePath));
  if (data.error) {
    content.innerHTML = '<p style="color:#f85149">' + esc(data.error) + '</p>';
    return;
  }
  if (data.type === 'directory') {
    var html = buildBreadcrumb(browsePath);
    html += '<div class="file-list">';
    // Show parent dir link if not at root
    if (browsePath) {
      var parentPath = browsePath.split('/').slice(0, -1).join('/');
      html += '<div class="file-entry" onclick="browseWorktreeDir(\'' + esc(parentPath) + '\')">';
      html += '<span class="file-icon">..</span>';
      html += '<span class="file-name dir">..</span>';
      html += '</div>';
    }
    if (!data.entries || !data.entries.length) {
      html += '<div style="padding:16px;color:#484f58;font-size:13px;text-align:center">Empty directory</div>';
    }
    for (var i = 0; i < (data.entries || []).length; i++) {
      var entry = data.entries[i];
      if (entry.type === 'dir') {
        html += '<div class="file-entry" onclick="browseWorktreeDir(\'' + esc(entry.path) + '\')">';
        html += '<span class="file-icon" style="color:#58a6ff">&#128193;</span>';
        html += '<span class="file-name dir">' + esc(entry.name) + '</span>';
        html += '</div>';
      } else {
        html += '<div class="file-entry" onclick="viewWorktreeFile(\'' + esc(entry.path) + '\')">';
        html += '<span class="file-icon">' + getFileIcon(entry.name) + '</span>';
        html += '<span class="file-name">' + esc(entry.name) + '</span>';
        html += '<span class="file-size">' + formatFileSize(entry.size) + '</span>';
        html += '</div>';
      }
    }
    html += '</div>';
    content.innerHTML = html;
  } else if (data.type === 'file') {
    renderFileContent(data.path, data.content);
  }
}

function browseWorktreeDir(dirPath) {
  loadWorktreeFiles(dirPath);
}

function viewWorktreeFile(filePath) {
  wtBrowsePath = filePath;
  var content = document.getElementById('wt-content');
  if (!content) return;
  content.innerHTML = '<p style="color:#8b949e">Loading file...</p>';
  api('git/worktrees/' + encodeURIComponent(wtCurrentName) + '/files?path=' + encodeURIComponent(filePath)).then(function(data) {
    if (data.error) {
      content.innerHTML = '<p style="color:#f85149">' + esc(data.error) + '</p>';
      return;
    }
    renderFileContent(data.path, data.content);
  });
}

function renderFileContent(filePath, text) {
  var content = document.getElementById('wt-content');
  if (!content) return;
  var dirPath = filePath.split('/').slice(0, -1).join('/');
  var fileName = filePath.split('/').pop();
  var html = buildBreadcrumb(filePath);
  html += '<div class="file-viewer">';
  html += '<div class="file-viewer-header">';
  html += '<span>' + esc(fileName) + '</span>';
  html += '<button class="btn" style="font-size:11px;padding:2px 8px" onclick="browseWorktreeDir(\'' + esc(dirPath) + '\')">Back to folder</button>';
  html += '</div>';
  var lines = (text || '').split('\n');
  for (var i = 0; i < lines.length; i++) {
    html += '<div class="file-line">';
    html += '<span class="file-line-num">' + (i + 1) + '</span>';
    html += '<span class="file-line-content">' + esc(lines[i]) + '</span>';
    html += '</div>';
  }
  html += '</div>';
  content.innerHTML = html;
}

async function loadWorktreeDiff() {
  var content = document.getElementById('wt-content');
  if (!content) return;
  if (wtDiffData) {
    renderDiffView(wtDiffData);
    return;
  }
  content.innerHTML = '<p style="color:#8b949e">Loading diff...</p>';
  var data = await api('git/worktrees/' + encodeURIComponent(wtCurrentName) + '/diff');
  if (data.error) {
    content.innerHTML = '<p style="color:#f85149">' + esc(data.error) + '</p>';
    return;
  }
  wtDiffData = data.files || [];
  renderDiffView(wtDiffData);
}

function renderDiffView(files) {
  var content = document.getElementById('wt-content');
  if (!content) return;
  if (!files || !files.length) {
    content.innerHTML = '<p style="color:#8b949e;text-align:center;padding:40px">No changes detected between this worktree and main branch.</p>';
    return;
  }
  var totalAdds = 0, totalDels = 0;
  for (var i = 0; i < files.length; i++) {
    totalAdds += files[i].additions || 0;
    totalDels += files[i].deletions || 0;
  }
  var html = '<div class="diff-summary">';
  html += '<span>' + files.length + ' files changed</span>';
  html += '<span class="total-adds">+' + totalAdds + '</span>';
  html += '<span class="total-dels">-' + totalDels + '</span>';
  html += '</div>';
  html += '<div class="diff-file-list">';
  for (var i = 0; i < files.length; i++) {
    var f = files[i];
    var expanded = wtExpandedDiffs[f.path] || false;
    html += '<div class="diff-file-header" onclick="toggleDiffFile(\'' + esc(f.path) + '\')">';
    html += '<span style="font-size:10px;color:#484f58">' + (expanded ? '&#9660;' : '&#9654;') + '</span>';
    html += '<span class="diff-adds">+' + (f.additions || 0) + '</span>';
    html += '<span class="diff-dels">-' + (f.deletions || 0) + '</span>';
    html += '<span class="diff-file-path">' + esc(f.path) + '</span>';
    html += '</div>';
    if (expanded) {
      html += '<div class="diff-content">';
      for (var h = 0; h < (f.hunks || []).length; h++) {
        var hunk = f.hunks[h];
        html += '<div class="diff-hunk-header">' + esc(hunk.header) + '</div>';
        for (var l = 0; l < (hunk.lines || []).length; l++) {
          var line = hunk.lines[l];
          var lineClass = line.type === 'add' ? 'add' : line.type === 'del' ? 'del' : 'context';
          var prefix = line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' ';
          html += '<div class="diff-line ' + lineClass + '">';
          html += '<span class="diff-line-num">' + (line.oldNum || '') + '</span>';
          html += '<span class="diff-line-num">' + (line.newNum || '') + '</span>';
          html += '<span class="diff-line-content">' + prefix + esc(line.content) + '</span>';
          html += '</div>';
        }
      }
      html += '</div>';
    }
  }
  html += '</div>';
  content.innerHTML = html;
}

function toggleDiffFile(filePath) {
  wtExpandedDiffs[filePath] = !wtExpandedDiffs[filePath];
  renderDiffView(wtDiffData);
}

// ===== 10b. COMMIT/BRANCH HISTORY =====
var gitNavStack=[];

function gitBack(){
  if(gitNavStack.length>0){
    var prev=gitNavStack.pop();
    if(prev.type==='branch')showBranchHistory(prev.branch,true);
    else if(prev.type==='git')showGit();
    else showGit();
  } else {
    showGit();
  }
}

async function showCommitDiff(hash,skipPush){
  if(!skipPush)gitNavStack.push(activeView==='branch'?{type:'branch',branch:gitCurrentBranch}:{type:'git'});
  activeView='commit';
  stopAutoRefresh();
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading commit '+esc(hash)+'...</p>';
  var data=await api('git/commit/'+encodeURIComponent(hash));
  if(data.error&&!data.commit){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}
  var cm=data.commit||{};
  var backLabel=gitNavStack.length>0&&gitNavStack[gitNavStack.length-1].type==='branch'?'Branch':'Git';
  var html='<div style="margin-bottom:12px"><button class="btn" onclick="gitBack()">&larr; '+backLabel+'</button></div>';
  html+='<div class="wt-header"><div style="display:flex;align-items:center;gap:12px">'+
    '<span style="font-family:monospace;color:#58a6ff;font-size:14px">'+esc(cm.hash?cm.hash.slice(0,8):'?')+'</span>'+
    '<span style="font-size:16px;color:#f0f6fc">'+esc(cm.message||'')+'</span></div>'+
    '<div style="font-size:12px;color:#8b949e;margin-top:4px">by '+esc(cm.author||'?')+' on '+localTime(cm.date)+'</div></div>';
  var files=data.files||[];
  if(files.length){
    html+=renderDiffHtml(files);
  } else {
    html+='<p style="color:#8b949e;text-align:center;padding:40px">No diff available for this commit</p>';
  }
  c.innerHTML=html;
}

var gitCurrentBranch='';

async function showBranchHistory(branch,skipPush){
  if(!skipPush)gitNavStack.push({type:'git'});
  activeView='branch';
  gitCurrentBranch=branch;
  stopAutoRefresh();
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading branch '+esc(branch)+'...</p>';
  var data=await api('git/branch-history/'+encodeURIComponent(branch));
  if(data.error&&!data.commits){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}
  var html='<div style="margin-bottom:12px"><button class="btn" onclick="gitBack()">&larr; Git</button></div>';
  html+='<div class="wt-header"><div style="display:flex;align-items:center;gap:12px">'+
    '<span class="branch-badge">'+esc(branch)+'</span>';
  if(data.ticketId)html+='<span style="color:#58a6ff">'+esc(data.ticketId)+'</span>';
  html+='</div></div>';
  // Commits
  var commits=data.commits||[];
  if(commits.length){
    html+='<h3 class="section-hdr">Commits ('+commits.length+')</h3>';
    html+='<div class="timeline">';
    for(var i=0;i<commits.length;i++){
      var cm=commits[i];
      html+='<div class="timeline-item" style="cursor:pointer" onclick="showCommitDiff(\''+esc(cm.hash)+'\')">'+
        '<span class="commit-hash">'+esc(cm.hash?cm.hash.slice(0,8):'')+'</span>'+
        '<span class="commit-msg">'+esc(cm.message)+'</span>'+
        '<span class="commit-date">'+localTime(cm.date)+'</span>'+
      '</div>';
    }
    html+='</div>';
  }
  // Merge diff
  var mergeDiff=data.mergeDiff||[];
  if(data.mergeCommit){
    html+='<h3 class="section-hdr">Merge Commit</h3>';
    html+='<div style="margin-bottom:12px;font-size:13px;color:#8b949e">'+
      '<span style="font-family:monospace;color:#58a6ff">'+esc(data.mergeCommit.hash?data.mergeCommit.hash.slice(0,8):'')+'</span> '+
      esc(data.mergeCommit.message||'')+' ('+localTime(data.mergeCommit.date)+')</div>';
  }
  if(mergeDiff.length){
    html+='<h3 class="section-hdr">Changes ('+mergeDiff.length+' files)</h3>';
    html+=renderDiffHtml(mergeDiff);
  } else if(!commits.length){
    html+='<p style="color:#8b949e;text-align:center;padding:40px">No history found for this branch</p>';
  }
  c.innerHTML=html;
}

// Shared diff HTML generator (used by commit diff, branch diff - returns HTML string)
function renderDiffHtml(files){
  var html='<div class="diff-file-list">';
  for(var i=0;i<files.length;i++){
    var f=files[i];
    var fid='cdiff-'+i;
    html+='<div class="diff-file-header" onclick="var el=document.getElementById(\''+fid+'\');if(el)el.style.display=el.style.display===\'none\'?\'\':\'none\'">'+
      '<span class="diff-adds">+'+esc(f.additions||0)+'</span>'+
      '<span class="diff-dels">-'+esc(f.deletions||0)+'</span>'+
      '<span class="diff-file-path">'+esc(f.path||f.file||'')+'</span></div>';
    html+='<div class="diff-content" id="'+fid+'" style="display:none">';
    var hunks=f.hunks||[];
    for(var h=0;h<hunks.length;h++){
      var hunk=hunks[h];
      if(hunk.header)html+='<div class="diff-hunk-header">'+esc(hunk.header)+'</div>';
      var lines=hunk.lines||[];
      for(var li=0;li<lines.length;li++){
        var ln=lines[li];
        var cls=ln.type==='add'?'add':ln.type==='del'?'del':'context';
        var prefix=ln.type==='add'?'+':ln.type==='del'?'-':' ';
        html+='<div class="diff-line '+cls+'">'+
          '<span class="diff-line-num">'+(ln.oldNum||'')+'</span>'+
          '<span class="diff-line-num">'+(ln.newNum||'')+'</span>'+
          '<span class="diff-line-content">'+prefix+' '+esc(ln.content||ln.text||'')+'</span></div>';
      }
    }
    html+='</div>';
  }
  html+='</div>';
  return html;
}

// ===== 11. BRIEF =====
async function showBrief(){
  activeView='brief';
  writeHash("brief");
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading brief...</p>';
  var data=await api('brief');
  // T2 (0.21): read-only snapshot, refreshed on navigation.
  var html='<div style="margin-bottom:4px"><span style="font-size:16px;color:#f0f6fc">Project Brief</span></div>';
  html+='<p style="color:#6e7681;font-size:12px;margin-bottom:12px">Read-only — refreshed when you open this view.</p>';

  if(data.manifest){
    html+='<h3 class="section-hdr">Manifest</h3>';
    html+='<div class="manifest-grid">';
    var mf=data.manifest;
    var keys=['project_name','status','mode','sprint','cycle_count','version','created_at','last_updated','company_dir'];
    for(var i=0;i<keys.length;i++){
      var k=keys[i];
      if(mf[k]!=null){
        html+='<div class="manifest-field"><div class="mf-label">'+esc(k.replace(/_/g,' '))+'</div><div class="mf-value">'+esc(typeof mf[k]==='object'?JSON.stringify(mf[k]):String(mf[k]))+'</div></div>';
      }
    }
    var allKeys=Object.keys(mf);
    for(var i=0;i<allKeys.length;i++){
      if(keys.indexOf(allKeys[i])<0){
        var v=mf[allKeys[i]];
        if(v!=null&&typeof v!=='object'){
          html+='<div class="manifest-field"><div class="mf-label">'+esc(allKeys[i].replace(/_/g,' '))+'</div><div class="mf-value">'+esc(String(v))+'</div></div>';
        }
      }
    }
    html+='</div>';
  }

  if(data.brief){
    html+='<h3 class="section-hdr">Brief</h3>';
    html+='<div class="finding-card"><div class="finding-body md-content">'+renderMarkdown(data.brief)+'</div></div>';
  } else {
    html+='<p style="color:#8b949e;font-size:13px">No brief.md found</p>';
  }
  c.innerHTML=html;
}

function setActiveNav(idx){
  document.querySelectorAll('#nav > button, #nav > .dropdown-wrap > button:first-child').forEach(function(b,i){b.classList.toggle('active',i===idx)});
}

checkStatus();
