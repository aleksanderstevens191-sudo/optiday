"""The live tracker page: a calm, glanceable dashboard for a side monitor.

Self-contained (no external files). It renders from JSON and long-polls /api/live, so it
updates the moment you switch apps and does nothing while nothing changes. No CSS animations:
a page that's on screen all day shouldn't keep the GPU busy.
"""
from __future__ import annotations

import json

CSS = r"""
:root{--bg:#07090d;--bg2:#0b0e14;--panel:#10141b;--panel2:#151b24;--line:#1e2631;--fg:#eef2f7;--muted:#8a96a8;
--dim:#5b6676;--good:#34d399;--focus:#7c8cff;--mid:#7d8ba1;--bad:#fb7185;--warn:#fbbf24;--away:#2a3340;
--accent:#7c8cff;--accent2:#22d3ee;--glow:rgba(124,140,255,.16);--unsure:#fbbf24}
:root[data-theme="light"]{--bg:#f3f5f8;--bg2:#eef1f5;--panel:#ffffff;--panel2:#f6f8fb;--line:#e3e7ee;--fg:#111827;
--muted:#5f6b7d;--dim:#9aa5b5;--good:#059669;--focus:#4f46e5;--mid:#64748b;--bad:#e11d48;--warn:#d97706;
--away:#e5e9ef;--accent:#4f46e5;--accent2:#0891b2;--glow:rgba(79,70,229,.10);--unsure:#d97706}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",
Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1680px;margin:0 auto;padding:18px 22px 14px;min-height:100%;display:flex;flex-direction:column}
.num,.clock,.timer,.big{font-variant-numeric:tabular-nums}
header.top{display:flex;align-items:center;gap:16px;margin-bottom:16px;flex-wrap:wrap}
.brand{font-weight:700;letter-spacing:.08em;font-size:13px;text-transform:uppercase;display:flex;align-items:center;gap:8px}
.brand .logo{color:var(--accent);font-size:16px}
.chip{font-size:11px;letter-spacing:.08em;padding:3px 8px;border-radius:999px;border:1px solid var(--line);
color:var(--muted);display:inline-flex;align-items:center;gap:6px;font-weight:600}
.chip i{width:7px;height:7px;border-radius:50%;background:var(--good);display:inline-block}
.chip.off i{background:var(--dim)}
.date{color:var(--muted);font-size:14px}.spacer{flex:1}
.clock{font-size:30px;font-weight:600;letter-spacing:-.02em}.clock small{font-size:14px;color:var(--muted);margin-left:3px}
nav.actions{display:flex;gap:6px;align-items:center}
nav.actions a,nav.actions button,.btn{font:inherit;font-size:13px;color:var(--fg);background:var(--panel);
border:1px solid var(--line);border-radius:10px;padding:6px 11px;text-decoration:none;cursor:pointer}
nav.actions a:hover,nav.actions button:hover,.btn:hover{border-color:var(--accent)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.danger{color:var(--bad)}
main.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;flex:1;align-content:start}
.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:18px 20px;min-width:0}
.card h2{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin:0 0 12px;font-weight:600}
.span2{grid-column:span 2}.span3{grid-column:span 3}
.now{position:relative;overflow:hidden;display:flex;flex-direction:column;gap:10px;min-height:260px}
.now::before{content:"";position:absolute;inset:-40% -10% auto auto;width:70%;height:180%;
background:radial-gradient(closest-side,var(--state-glow,var(--glow)),transparent);pointer-events:none}
.now>*{position:relative}
.state{display:inline-flex;align-items:center;gap:8px;font-weight:700;font-size:13px;letter-spacing:.06em;
text-transform:uppercase;padding:6px 12px;border-radius:999px;background:var(--panel2);border:1px solid var(--line);
align-self:flex-start;color:var(--state,var(--accent))}
.state i{width:9px;height:9px;border-radius:50%;background:var(--state,var(--accent))}
.state .timer{color:var(--fg);font-weight:600;letter-spacing:0}
.big{font-size:46px;font-weight:700;letter-spacing:-.03em;line-height:1.05;margin:4px 0 0;overflow-wrap:anywhere}
.sub{color:var(--muted);font-size:16px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.since{color:var(--muted);font-size:14px}.since b{color:var(--fg);font-weight:600}
.recent{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px}
.recent .lbl{color:var(--dim);font-size:12px;letter-spacing:.06em;text-transform:uppercase;margin-right:2px}
.rc{font-size:13px;padding:4px 9px;border-radius:999px;background:var(--panel2);border:1px solid var(--line);
white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.rc i{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:1px}
.rc em{font-style:normal;color:var(--muted)}
.focusbar{margin-top:auto;border-top:1px solid var(--line);padding-top:14px;display:flex;align-items:center;gap:10px;
flex-wrap:wrap}
.focusbar .lbl{color:var(--muted);font-size:13px;margin-right:4px}
.session{flex:1;min-width:240px}
.session .row{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.session .cd{font-size:30px;font-weight:700;letter-spacing:-.02em}
.track{height:8px;background:var(--panel2);border-radius:6px;overflow:hidden;border:1px solid var(--line)}
.track>i{display:block;height:100%;border-radius:6px;background:linear-gradient(90deg,var(--accent),var(--accent2))}
.score{display:flex;flex-direction:column;align-items:center;gap:14px}
.donut{position:relative;width:180px;height:180px}.donut svg{width:100%;height:100%;transform:rotate(-90deg)}
.donut .c{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.donut .pct{font-size:44px;font-weight:700;letter-spacing:-.03em}.donut .cap{color:var(--muted);font-size:12px;
letter-spacing:.08em;text-transform:uppercase}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:10px 18px;width:100%}
.stat .k{color:var(--muted);font-size:12px;letter-spacing:.06em;text-transform:uppercase}
.stat .v{font-size:22px;font-weight:650;letter-spacing:-.02em}.stat .s{color:var(--dim);font-size:12px}
.v.good{color:var(--good)}.v.bad{color:var(--bad)}
svg.tl{width:100%;height:auto;display:block}.tl .tick{stroke:var(--line)}.tl .lab{fill:var(--muted);font-size:12px}
.tl .now{stroke:var(--fg);stroke-width:1.5}.tl .nowl{fill:var(--fg);font-size:12px;font-weight:600}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:12px;margin-top:8px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.bars{display:flex;flex-direction:column;gap:11px}
.bar .h{display:flex;justify-content:space-between;gap:10px;font-size:14px}
.bar .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bar .d{color:var(--muted);white-space:nowrap}
.bar .t{height:6px;background:var(--panel2);border-radius:4px;overflow:hidden;margin-top:5px}
.bar .t i{display:block;height:100%;border-radius:4px}
.empty{color:var(--dim);font-size:14px}
ul.nudges{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
ul.nudges li{padding:10px 12px;border-radius:12px;background:var(--panel2);border:1px solid var(--line);
border-left:3px solid var(--tone,var(--accent));font-size:14px}
.kv{display:flex;flex-direction:column;gap:12px}
.kv .row{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.kv .row .k{color:var(--muted);font-size:14px}.kv .row .v{font-weight:600;text-align:right}
.meet .t{font-size:18px;font-weight:650}.meet .w{color:var(--muted);font-size:13px}
.meet .cd{font-size:26px;font-weight:700;letter-spacing:-.02em;color:var(--accent)}
a.inline{color:var(--accent);text-decoration:none}
.goal+.goal{margin-top:14px}.goal .h{display:flex;justify-content:space-between;font-size:14px;margin-bottom:6px}
.goal .h span{color:var(--muted)}
.lh{font-size:15px}.lh b{display:block;color:var(--muted);font-size:12px;letter-spacing:.06em;text-transform:uppercase;
margin-bottom:4px;font-weight:600}
.hourbar{display:flex;height:8px;border-radius:5px;overflow:hidden;background:var(--panel2);margin-top:8px}
.hourbar i{display:block;height:100%}
.bar .t.st{display:flex;gap:2px;background:var(--panel2)}.bar .t.st i{border-radius:3px}
.hour{grid-column:span 3}
.ytsum{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:8px}
.ytsum div{display:flex;flex-direction:column}.ytsum b{font-size:22px;font-weight:700;letter-spacing:-.01em}
.ytsum span{color:var(--muted);font-size:12px;letter-spacing:.05em;text-transform:uppercase}
.splitbar{display:flex;gap:2px;height:8px;border-radius:5px;overflow:hidden;background:var(--panel2);margin-bottom:12px}
.splitbar i{display:block;height:100%}
ul.vids{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
ul.vids li{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:12px;background:var(--panel2);
border:1px solid var(--line);border-left:3px solid var(--vc,var(--line))}
ul.vids li.ask{border-color:color-mix(in srgb,var(--unsure) 45%,var(--line));border-left-color:var(--unsure)}
.vt{flex:1;min-width:0}.vt .n{font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vt .w{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vt .w b{color:var(--unsure);font-weight:600}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;flex:none}
.seg button{font:inherit;font-size:12px;padding:5px 9px;background:var(--panel);color:var(--muted);border:0;cursor:pointer}
.seg button+button{border-left:1px solid var(--line)}
.seg button:hover{color:var(--fg)}
.seg button.on[data-label="informational"]{background:var(--good);color:#06281c;font-weight:600}
.seg button.on[data-label="waste"]{background:var(--bad);color:#3b0712;font-weight:600}
.nowvid{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:6px}
footer{color:var(--dim);font-size:12px;text-align:center;margin-top:12px}
@media (max-width:1100px){main.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.span3,.hour{grid-column:span 2}
.now{grid-column:span 2}.score{grid-column:span 2;display:grid;grid-template-columns:auto 1fr;align-items:center;gap:8px 28px}
.score h2{grid-column:span 2}}
@media (max-width:700px){main.grid{grid-template-columns:1fr}.span2,.span3,.now,.score,.hour{grid-column:span 1}
.score{display:flex}
.big{font-size:34px}.clock{font-size:24px}.wrap{padding:14px 16px}}
"""

JS = r"""
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let D=JSON.parse(document.getElementById('boot').textContent);
let skew=D.now*1000-Date.now();
const now=()=>(Date.now()+skew)/1000;
const COL={productive:'var(--good)',neutral:'var(--mid)',distracting:'var(--bad)',away:'var(--away)'};
const STATE={flow:['In flow','var(--good)','rgba(52,211,153,.16)'],focus:['Focused','var(--focus)','rgba(124,140,255,.16)'],
 comms:['Email & chat','var(--mid)','rgba(125,139,161,.14)'],drift:['Drifting','var(--bad)','rgba(251,113,133,.17)'],
 away:['Away','var(--dim)','rgba(91,102,118,.10)'],watch:['Watching','var(--unsure)','rgba(251,191,36,.12)'],paused:['Paused','var(--warn)','rgba(251,191,36,.12)']};
const TONE={good:'var(--good)',bad:'var(--bad)',warn:'var(--warn)',info:'var(--accent)'};
const VCOL={productive:'var(--good)',neutral:'var(--unsure)',distracting:'var(--bad)'};
const VLAB={informational:['Learning','var(--good)'],waste:['Waste','var(--bad)'],unclear:['Not sure','var(--unsure)']};
const VWHY={you:'you marked it',channel:'you marked this channel',ai:'local AI',learned:'like ones you marked',rules:'from the title',unsure:''};
function seg(v){return `<span class="seg">`+['informational','waste'].map(l=>`<button type="button" data-vid="${esc(v.id)}" data-label="${l}" class="${v.label===l?'on':''}" title="${l==='informational'?'Useful: learning or work':'Waste of time'}">${VLAB[l][0]}</button>`).join('')+`</span>`}
function dur(s){s=Math.max(0,Math.round(s));if(s<1)return '0m';if(s<60)return s+'s';const m=Math.round(s/60);if(m<60)return m+'m';
 return Math.floor(m/60)+'h '+String(m%60).padStart(2,'0')+'m'}
function clock(s){s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;
 return (h?h+':'+String(m).padStart(2,'0'):m)+':'+String(x).padStart(2,'0')}
function hm(ts){return new Date(ts*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}).replace(' ','').toLowerCase()}
function bar(name,s,max,color,sub){const w=max?Math.max(2,100*s/max):0;
 return `<div class="bar"><div class="h"><span class="n">${esc(name)}</span><span class="d">${dur(s)}${sub?' · '+esc(sub):''}</span></div>`+
 `<div class="t"><i style="width:${w.toFixed(1)}%;background:${color}"></i></div></div>`}
function sbar(p,max){const sp=p.split||{},cols=p.video?VCOL:COL,keys=['productive','neutral','distracting'].filter(k=>sp[k]>0);
 if(keys.length<2)return bar(p.name,p.s,max,(p.video?VCOL:COL)[p.cls]||'var(--mid)');
 const w=max?Math.max(2,100*p.s/max):0,tot=keys.reduce((a,k)=>a+sp[k],0)||1;
 return `<div class="bar"><div class="h"><span class="n">${esc(p.name)}</span><span class="d">${dur(p.s)}</span></div>`+
 `<div class="t st" style="width:${w.toFixed(1)}%">`+keys.map(k=>`<i style="width:${(100*sp[k]/tot).toFixed(1)}%;background:${cols[k]}"></i>`).join('')+`</div></div>`}
async function post(path,body){try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-OptiDay':'1'},
 body:JSON.stringify(body)});if(r.ok){const d=await r.json();if(d&&d.version!=null){apply(d)}}}catch(e){}}

function renderNow(d){
 const st=STATE[d.state]||STATE.focus, c=d.current, el=$('#now');
 el.style.setProperty('--state',st[1]);el.style.setProperty('--state-glow',st[2]);
 let title='Nothing yet',sub='OptiDay starts filling this in as you work.',extra='';
 if(d.state==='paused'){title='Recording paused';sub='Nothing is being recorded until you resume.'}
 else if(d.state==='away'){title='Away';sub=c&&!c.away?'Last: '+(c.project||c.place):'See you soon.'}
 else if(c){title=c.project||c.place;
  const bits=[];if(c.project&&c.place!==c.project)bits.push(c.place);if(c.title&&!c.private)bits.push(c.title);
  if(c.private)bits.push('Private window: details not recorded');
  sub=bits.join(' · ')||c.category;
  if(c.video){const v=c.video,L=VLAB[v.label]||VLAB.unclear;
   extra=`<div class="nowvid since"><span>Watching${v.channel?' '+esc(v.channel):''} · <b style="color:${L[1]}">${L[0]}</b>${v.label==='unclear'?': learning or waste?':(VWHY[v.source]?' · '+VWHY[v.source]:'')}</span>${seg(v)}</div>`}}
 const onFor=c&&!c.away&&d.state!=='away'&&d.state!=='paused'?`<div class="since">On this for <b class="timer" data-since="${c.since}"></b> · ${esc(c.category)}</div>`:'';
 const fs=d.focus_session;
 let focus='';
 if(fs){const pct=100*Math.min(1,(now()-fs.start)/(fs.end-fs.start));
  focus=`<div class="session"><div class="row"><span class="lbl">Focus session · ${esc(fs.label)}</span>
   <span class="cd timer" data-until="${fs.end}"></span><span class="since">${dur(fs.focused_s)} focused${fs.drift_s?` · <b style="color:var(--bad)">${dur(fs.drift_s)} drifting</b>`:''}</span></div>
   <div class="track"><i data-progress="${fs.start},${fs.end}" style="width:${pct.toFixed(1)}%"></i></div></div>
   <button class="btn danger" data-act="stop">Stop</button>`}
 else{const lf=d.last_focus;
  focus=`<span class="lbl">${lf?`Last session: ${dur(lf.focused_s)} focused, ${dur(lf.drift_s)} drifting. Again?`:'Start a focus session'}</span>
   <button class="btn" data-focus="25">25 min</button><button class="btn primary" data-focus="50">50 min</button>
   <button class="btn" data-focus="90">90 min</button>`}
 el.innerHTML=`<div class="state"><i></i>${st[0]}<span class="timer" data-since="${d.since}"></span></div>
  <div class="big">${esc(title)}</div><div class="sub">${esc(sub)}</div>${onFor}${extra}
  ${d.recent&&d.recent.length?'<div class="recent"><span class="lbl">Before this</span>'+d.recent.map(r=>`<span class="rc"><i style="background:${COL[r.cls]||'var(--mid)'}"></i>${hm(r.start)} ${esc(r.project&&r.project!==r.place?r.project+' · '+r.place:r.place)} <em>${dur(r.end-r.start)}</em></span>`).join('')+'</div>':''}
  <div class="focusbar">${focus}</div>`;
}
function renderScore(d){
 const t=d.totals, a=Math.max(1,t.productive_s+t.neutral_s+t.distracting_s), R=70, C=2*Math.PI*R;
 let off=0, arcs='';
 for(const [k,v] of [['productive',t.productive_s],['neutral',t.neutral_s],['distracting',t.distracting_s]]){
  const len=C*v/a; if(len>0.5){arcs+=`<circle r="${R}" cx="90" cy="90" fill="none" stroke="${COL[k]}" stroke-width="16" stroke-dasharray="${Math.max(0,len-2).toFixed(1)} ${C.toFixed(1)}" stroke-dashoffset="${(-off).toFixed(1)}" stroke-linecap="butt"/>`}
  off+=len}
 $('#score').innerHTML=`<h2 style="align-self:flex-start">Today</h2><div class="donut"><svg viewBox="0 0 180 180">
  <circle r="${R}" cx="90" cy="90" fill="none" stroke="var(--panel2)" stroke-width="16"/>${arcs}</svg>
  <div class="c"><div class="pct num">${t.active_s?t.score+'%':'–'}</div><div class="cap">productive</div></div></div>
  <div class="stats">
   <div class="stat"><div class="k">Active</div><div class="v num">${dur(t.active_s)}</div><div class="s">${t.first_ts?'since '+hm(t.first_ts):'&nbsp;'}</div></div>
   <div class="stat"><div class="k">Deep focus</div><div class="v num good">${dur(t.focus_s)}</div><div class="s">longest ${dur(t.longest_flow_s)}</div></div>
   <div class="stat"><div class="k">Distracted</div><div class="v num ${t.distracting_s>=d.goals.distraction_s&&d.goals.distraction_s?'bad':''}">${dur(t.distracting_s)}</div><div class="s">${a>1?Math.round(100*t.distracting_s/a):0}% of active</div></div>
   <div class="stat"><div class="k">Switches</div><div class="v num">${t.switches}</div><div class="s">${t.switches_10m} in last 10 min</div></div>
  </div>`;
}
function renderTimeline(d){
 const B=d.bucket, nowT=now(), first=d.totals.first_ts||nowT;
 const h0=new Date(first*1000);h0.setMinutes(0,0,0);
 const hEnd=new Date(nowT*1000);hEnd.setMinutes(0,0,0);hEnd.setHours(hEnd.getHours()+1);
 let t0=h0.getTime()/1000, t1=Math.max(hEnd.getTime()/1000,t0+3*3600);
 const W=Math.max(480,Math.round($('#tl').clientWidth||1200)),H=96,top=16,base=74,span=t1-t0,x=t=>(t-t0)/span*W, bw=B/span*W;
 let out='';
 for(let t=t0;t<=t1+1;t+=3600){const X=x(t);out+=`<line class="tick" x1="${X.toFixed(1)}" x2="${X.toFixed(1)}" y1="${top}" y2="${base+4}"/>`+
  `<text class="lab" x="${Math.min(Math.max(X,12),W-12).toFixed(1)}" y="${H-6}" text-anchor="middle">${hm(t).replace(':00','')}</text>`}
 out+=`<rect x="${x(nowT).toFixed(1)}" y="${top}" width="${Math.max(0,W-x(nowT)).toFixed(1)}" height="${base-top}" fill="var(--panel2)" opacity=".55"/>`;
 for(const [b,p,n,dd,aw,un] of d.timeline){const bx=x(d.day_start+b*B);let y=base;
  for(const [v,k] of [[p,'productive'],[n,'neutral'],[un||0,'unsure'],[dd,'distracting']]){if(v<=0)continue;const hh=(base-top)*v/B;y-=hh;
   out+=`<rect x="${(bx+0.6).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(0.8,bw-1.2).toFixed(1)}" height="${hh.toFixed(1)}" fill="${k==='unsure'?'var(--unsure)':COL[k]}" rx="1"/>`}
  if(aw>0){const hh=(base-top)*aw/B;out+=`<rect x="${(bx+0.6).toFixed(1)}" y="${(y-hh).toFixed(1)}" width="${Math.max(0.8,bw-1.2).toFixed(1)}" height="${hh.toFixed(1)}" fill="var(--away)" rx="1"/>`}}
 const nm=d.next_meeting;
 if(nm&&nm.start<t1){const a=x(Math.max(nm.start,nowT)),b=x(Math.min(nm.end,t1));
  out+=`<rect x="${a.toFixed(1)}" y="${top}" width="${Math.max(2,b-a).toFixed(1)}" height="${base-top}" fill="none" stroke="var(--accent)" stroke-dasharray="3 3" rx="3"/>`}
 const nx=x(nowT);
 out+=`<line class="now" x1="${nx.toFixed(1)}" x2="${nx.toFixed(1)}" y1="${top-1}" y2="${base+4}"/><text class="nowl" x="${Math.min(Math.max(nx,16),W-16).toFixed(1)}" y="${top-5}" text-anchor="middle">now</text>`;
 $('#tl').innerHTML=`<svg class="tl" viewBox="0 0 ${W} ${H}" role="img" aria-label="Your day so far, 5 minutes per bar">${out}</svg>`;
}
function renderLists(d){
 const pm=d.projects.length?d.projects[0].s:0;
 $('#projects').innerHTML='<h2>Projects today</h2>'+(d.projects.length?'<div class="bars">'+d.projects.map(p=>bar(p.name,p.s,pm,'linear-gradient(90deg,var(--accent),var(--accent2))')).join('')+'</div>':'<p class="empty">Projects appear as soon as OptiDay recognizes what you are working on.</p>');
 const am=d.places.length?d.places[0].s:0;
 $('#places').innerHTML='<h2>Apps &amp; sites</h2>'+(d.places.length?'<div class="bars">'+d.places.map(p=>sbar(p,am)).join('')+'</div>':'<p class="empty">Nothing yet.</p>');
 $('#nudges').innerHTML='<h2>Optimize now</h2><ul class="nudges">'+d.nudges.map(n=>`<li style="--tone:${TONE[n.tone]||'var(--accent)'}">${esc(n.text)}</li>`).join('')+'</ul>';
}
function renderYT(d){
 const y=d.youtube||{learn_s:0,waste_s:0,unsure_s:0,browse_s:0,shorts:{count:0,s:0},videos:[],unsure:0};
 const tot=y.learn_s+y.unsure_s+y.waste_s;let h='<h2>YouTube today</h2>';
 if(!tot&&!y.browse_s){$('#yt').innerHTML=h+'<p class="empty">No YouTube yet today. Each video you watch shows up here as learning, waste or not sure.</p>';return}
 h+=`<div class="ytsum"><div><b style="color:var(--good)">${dur(y.learn_s)}</b><span>Learning</span></div>`+
  `<div><b style="color:var(--unsure)">${dur(y.unsure_s)}</b><span>Not sure</span></div>`+
  `<div><b style="color:var(--bad)">${dur(y.waste_s)}</b><span>Waste</span></div></div>`;
 if(tot)h+='<div class="splitbar">'+[['learn_s','var(--good)'],['unsure_s','var(--unsure)'],['waste_s','var(--bad)']].filter(x=>y[x[0]]>0).map(x=>`<i style="width:${(100*y[x[0]]/tot).toFixed(1)}%;background:${x[1]}"></i>`).join('')+'</div>';
 h+='<ul class="vids">'+y.videos.slice(0,6).map(v=>{const ask=v.label==='unclear'&&v.source!=='you',L=VLAB[v.label]||VLAB.unclear;
  const why=ask?'<b>Not sure: learning or waste?</b>':`<span style="color:${L[1]}">${L[0]}</span>${VWHY[v.source]?' · '+VWHY[v.source]:''}`;
  return `<li class="${ask?'ask':''}" style="--vc:${L[1]}"><div class="vt"><div class="n" title="${esc(v.title)}">${v.now?'▶ ':''}${esc(v.title)}</div>`+
   `<div class="w">${dur(v.s)}${v.channel?' · '+esc(v.channel):''} · ${why}</div></div>${seg(v)}</li>`}).join('')+'</ul>';
 const more=[];if(y.videos.length>6)more.push(`${y.videos.length-6} more in the report`);
 if(y.shorts&&y.shorts.count)more.push(`${y.shorts.count} Short${y.shorts.count>1?'s':''} (${dur(y.shorts.s)}) counted as waste`);
 if(y.browse_s>=60)more.push(`${dur(y.browse_s)} browsing the feed and search`);
 if(more.length)h+=`<p class="empty" style="margin:10px 0 0">${more.map(esc).join(' · ')}</p>`;
 $('#yt').innerHTML=h;
}
function renderNext(d){
 const nm=d.next_meeting;let h='<h2>Up next</h2><div class="kv">';
 if(nm){h+=`<div class="meet"><div class="w">${nm.start>now()?'Next meeting in':'In a meeting now'}</div>
   <div class="cd timer" ${nm.start>now()?`data-until="${nm.start}"`:`data-until="${nm.end}"`}></div>
   <div class="t">${esc(nm.title)}</div><div class="w">${hm(nm.start)}–${hm(nm.end)}${nm.url?` · <a class="inline" href="${esc(nm.url)}" target="_blank" rel="noopener">join</a>`:''}${d.meetings_left>1?` · ${d.meetings_left-1} more today`:''}</div></div>`}
 else{h+='<div class="row"><span class="k">Meetings</span><span class="v">None left today</span></div>'}
 h+=`<div class="row"><span class="k">Emails waiting on you</span><span class="v"><a class="inline" href="/plan">${d.emails_waiting}</a></span></div>`;
 h+=`<div class="row"><span class="k">Chores an agent could do</span><span class="v"><a class="inline" href="/plan#agents">${d.agents_waiting}</a></span></div></div>`;
 $('#next').innerHTML=h;
}
function renderGoals(d){
 const t=d.totals,g=d.goals;let h='<h2>Goals</h2>';
 if(g.focus_s){const p=Math.min(100,100*t.focus_s/g.focus_s);
  h+=`<div class="goal"><div class="h">Deep focus<span>${dur(t.focus_s)} of ${dur(g.focus_s)}</span></div><div class="track"><i style="width:${p.toFixed(1)}%;background:var(--good)"></i></div></div>`}
 if(g.distraction_s){const p=Math.min(100,100*t.distracting_s/g.distraction_s),over=t.distracting_s>=g.distraction_s;
  h+=`<div class="goal"><div class="h">Distraction budget<span>${dur(t.distracting_s)} of ${dur(g.distraction_s)}</span></div><div class="track"><i style="width:${p.toFixed(1)}%;background:${over?'var(--bad)':'var(--warn)'}"></i></div></div>`}
 const th=d.this_hour, tot=th.productive+th.neutral+th.distracting+th.away;
 h+=`<div class="goal"><div class="h">This hour<span>${dur(th.productive)} productive · ${dur(th.distracting)} distracted</span></div><div class="hourbar">`+
  ['productive','neutral','distracting','away'].map(k=>`<i style="width:${(100*th[k]/3600).toFixed(1)}%;background:${COL[k]}"></i>`).join('')+'</div></div>';
 $('#goals').innerHTML=h;
}
function renderHour(d){
 const lh=d.last_hour;
 $('#hour').innerHTML='<h2>Last hour</h2>'+(lh?`<div class="lh"><b>${esc(lh.label)}</b>${esc(lh.text)}</div>`:'<p class="empty">A one-line summary appears here after each full hour.</p>')+
  `<p class="empty" style="margin-top:14px"><a class="inline" href="/today">Full report →</a> &nbsp; <a class="inline" href="/plan">Today's plan →</a></p>`;
}
function apply(d){
 D=d;skew=d.now*1000-Date.now();
 renderNow(d);renderScore(d);renderTimeline(d);renderLists(d);renderYT(d);renderNext(d);renderGoals(d);renderHour(d);
 $('#pauseBtn').textContent=d.paused?'Resume':'Pause';
 $('#livechip').classList.toggle('off',d.paused||d.state==='away');
 $('#livechip').lastChild.textContent=d.paused?'PAUSED':(d.live?'LIVE':'SYNCED');
 tick(true);
}
let lastMin=-1;
function tick(force){
 const n=now();
 document.querySelectorAll('[data-since]').forEach(e=>{e.textContent=clock(n-parseFloat(e.dataset.since))});
 document.querySelectorAll('[data-until]').forEach(e=>{e.textContent=clock(parseFloat(e.dataset.until)-n)});
 document.querySelectorAll('[data-progress]').forEach(e=>{const [a,b]=e.dataset.progress.split(',').map(Number);
  e.style.width=(100*Math.min(1,Math.max(0,(n-a)/(b-a)))).toFixed(1)+'%'});
 const dte=new Date(n*1000), m=dte.getMinutes();
 if(force||m!==lastMin){lastMin=m;
  const t=dte.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});const ap=t.match(/\s?(am|pm)$/i);
  $('#clock').innerHTML=esc(ap?t.slice(0,ap.index):t)+(ap?`<small>${ap[1].toLowerCase()}</small>`:'');
  $('#date').textContent=dte.toLocaleDateString([],{weekday:'long',month:'long',day:'numeric'});
  if(!force&&m%5===0)renderTimeline(D)}
}
let timer=setInterval(tick,1000);
let rz;window.addEventListener('resize',()=>{clearTimeout(rz);rz=setTimeout(()=>renderTimeline(D),150)});
document.addEventListener('visibilitychange',()=>{clearInterval(timer);if(!document.hidden){tick(true);timer=setInterval(tick,1000)}});
async function loop(){
 for(;;){
  try{const r=await fetch('/api/live?v='+D.version,{cache:'no-store',headers:{'X-OptiDay':'1'}});
   if(!r.ok)throw new Error(r.status);const d=await r.json();$('#updated').textContent='Up to date';
   if(d.version!==D.version||d.state!==D.state||d.paused!==D.paused)apply(d);else skew=d.now*1000-Date.now();}
  catch(e){$('#updated').textContent='Reconnecting to OptiDay…';await new Promise(r=>setTimeout(r,5000))}
 }
}
document.addEventListener('click',e=>{
 const lb=e.target.closest('[data-label]');if(lb){const cur=lb.classList.contains('on');
  document.querySelectorAll(`[data-vid="${CSS.escape(lb.dataset.vid)}"]`).forEach(b=>b.classList.toggle('on',!cur&&b===lb));
  post('/api/label',{video_id:lb.dataset.vid,label:cur?'unclear':lb.dataset.label});return}
 const f=e.target.closest('[data-focus]');if(f){post('/api/focus',{action:'start',minutes:+f.dataset.focus});return}
 if(e.target.closest('[data-act="stop"]')){post('/api/focus',{action:'stop'});return}
});
$('#pauseBtn').addEventListener('click',()=>post('/api/pause',{paused:!D.paused}));
function setTheme(t){document.documentElement.dataset.theme=t;try{localStorage.setItem('optiday-theme',t)}catch(e){}}
try{const t=localStorage.getItem('optiday-theme');if(t)document.documentElement.dataset.theme=t}catch(e){}
$('#themeBtn').addEventListener('click',()=>setTheme(document.documentElement.dataset.theme==='light'?'dark':'light'));
function fs(){if(document.fullscreenElement)document.exitFullscreen();else document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen()}
$('#fsBtn').addEventListener('click',fs);
document.addEventListener('keydown',e=>{if(e.target.closest('input,textarea'))return;
 if(e.key==='f'||e.key==='F')fs();if(e.key==='t'||e.key==='T')$('#themeBtn').click()});
apply(D);loop();
"""


def render_live(state: dict) -> str:
    boot = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#07090d"><meta name="color-scheme" content="dark light">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="OptiDay Live">
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/icon-512.png">
<title>OptiDay Live</title><style>{CSS}</style></head>
<body><div class="wrap">
<header class="top">
  <div class="brand"><span class="logo">◉</span>OptiDay<span class="chip" id="livechip"><i></i>LIVE</span></div>
  <div class="date" id="date"></div><div class="spacer"></div>
  <div class="clock" id="clock"></div>
  <nav class="actions"><button id="pauseBtn" type="button">Pause</button><a href="/plan">Today's plan</a>
    <a href="/today">Report</a><button id="themeBtn" type="button" title="Light / dark (T)" aria-label="Light or dark"><svg width="15" height="15" viewBox="0 0 16 16" style="vertical-align:-3px"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 1.5a6.5 6.5 0 0 1 0 13z" fill="currentColor"/></svg></button>
    <button id="fsBtn" type="button" title="Full screen (F)" aria-label="Full screen"><svg width="15" height="15" viewBox="0 0 16 16" style="vertical-align:-3px" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4"/></svg></button></nav>
</header>
<main class="grid">
  <section class="card now span2" id="now"></section>
  <section class="card score" id="score"></section>
  <section class="card span3"><h2>Your day so far</h2><div id="tl"></div>
    <div class="legend"><span><i style="background:var(--good)"></i>Productive</span>
    <span><i style="background:var(--mid)"></i>Email, chat, meetings</span>
    <span><i style="background:var(--bad)"></i>Distracting</span><span><i style="background:var(--away)"></i>Away</span>
    <span><i style="background:var(--unsure)"></i>YouTube: not sure</span>
    <span><i style="background:none;border:1px dashed var(--accent)"></i>Next meeting</span></div></section>
  <section class="card" id="places"></section>
  <section class="card" id="yt"></section>
  <section class="card" id="nudges"></section>
  <section class="card" id="projects"></section>
  <section class="card" id="next"></section>
  <section class="card" id="goals"></section>
  <section class="card hour" id="hour"></section>
</main>
<footer>Live from this Mac · nothing leaves it · <span id="updated">Up to date</span> · F full screen · T light/dark</footer>
</div>
<script type="application/json" id="boot">{boot}</script>
<script>{JS}</script></body></html>"""


MANIFEST = json.dumps({
    "name": "OptiDay Live", "short_name": "OptiDay", "start_url": "/live", "scope": "/", "display": "standalone",
    "background_color": "#07090d", "theme_color": "#07090d",
    "icons": [{"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
              {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
})

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7c8cff"/>
<stop offset="1" stop-color="#22d3ee"/></linearGradient></defs>
<rect width="512" height="512" rx="112" fill="#0b0e14"/>
<circle cx="256" cy="256" r="150" fill="none" stroke="#1e2631" stroke-width="44"/>
<circle cx="256" cy="256" r="150" fill="none" stroke="url(#g)" stroke-width="44" stroke-linecap="round"
 stroke-dasharray="660 943" transform="rotate(-90 256 256)"/>
<circle cx="256" cy="256" r="58" fill="url(#g)"/></svg>"""
