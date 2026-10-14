
(function(){
const data = window.SP_DATA || [];
const $ = s => document.querySelector(s);
const list = $('#list'), q = $('#q'), sort = $('#sort'), count = $('#count');
const filters = [...document.querySelectorAll('input[data-f]')];
const picked = new Set();
const fmt = n => n==null ? '—' : new Intl.NumberFormat('nb-NO',{maximumFractionDigits:0}).format(n);
const esc = s => String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function matches(c, term){ if(!term) return true; const t = term.toLowerCase(); return c.search.includes(t); }
function render(){
  const term = q.value.trim(); const active = filters.filter(f=>f.checked).map(f=>f.dataset.f);
  let rows = data.filter(c => matches(c, term) && active.every(f => c.flags[f]));
  const key = sort.value;
  rows.sort((a,b)=> key==='name' ? a.name.localeCompare(b.name,'nb') : key==='revenue' ? (b.revenue??-1e18)-(a.revenue??-1e18) : key==='jobs' ? b.jobs-a.jobs : b.score-a.score || a.name.localeCompare(b.name,'nb'));
  count.textContent = rows.length + ' of ' + data.length + ' companies';
  list.innerHTML = rows.slice(0,400).map(c => `
   <article class="card co">
     <div><h3><a href="c/${c.org}.html">${esc(c.name)}</a></h3>
       <div class="small muted">${c.org} · ${esc(c.form||'')} · ${esc(c.municipality||'')}${c.brand?` · brand “${esc(c.brand)}”`:''}</div>
       <div class="small">${esc(c.industry||'')}</div>
       <div>${c.website?`<span class="chip ok">🌐 ${esc(c.website.replace(/^https?:\/\//,''))}</span>`:''}${c.jobs?`<span class="chip ok">${c.jobs} job${c.jobs>1?'s':''}</span>`:''}${c.news?`<span class="chip">${c.news} news</span>`:''}${c.socials?`<span class="chip">${c.socials} profiles</span>`:''}${c.risk.map(r=>`<span class="chip bad">${esc(r)}</span>`).join('')}</div>
       <div class="dots" title="information areas found">${c.families.map(f=>`<span class="dot ${f[1]?'on':''}" title="${esc(f[0])}: ${f[1]?'found':'not found'}"></span>`).join('')}</div>
     </div>
     <div class="small" style="text-align:right">
       ${c.revenue!=null?`<div><span class="muted">Revenue ${esc(c.period||'')}</span><br><b>${fmt(c.revenue)}</b> ${esc(c.currency||'')}</div>`:'<div class="muted">No filed revenue</div>'}
       <label class="filter" style="margin-top:6px"><input type="checkbox" data-pick="${c.org}" ${picked.has(c.org)?'checked':''}> Compare</label>
     </div>
   </article>`).join('') + (rows.length>400?`<p class="muted">Showing the first 400 — refine the search.</p>`:'');
}
function renderCompare(){
  const box = $('#compare'); const rows = data.filter(c=>picked.has(c.org));
  if(!rows.length){ box.classList.remove('show'); return; }
  box.classList.add('show');
  const fields = [['Organisation no.',c=>c.org],['Legal form',c=>c.form],['Municipality',c=>c.municipality],['Industry',c=>c.industry],['Founded',c=>c.founded],['Employees (register)',c=>c.employees==null?'not registered':fmt(c.employees)],['Revenue',c=>c.revenue==null?'—':fmt(c.revenue)+' '+(c.currency||'')+' ('+(c.period||'')+')'],['Annual result',c=>c.result==null?'—':fmt(c.result)],['Equity',c=>c.equity==null?'—':fmt(c.equity)],['CEO',c=>c.ceo],['Chair',c=>c.chair],['Website',c=>c.website],['Social profiles',c=>c.socials],['Active jobs',c=>c.jobs],['News items',c=>c.news],['Workplaces',c=>c.workplaces]];
  box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><b>Compare (${rows.length}/4)</b><button id="clr">Clear</button></div>
   <div class="tablewrap"><table class="cmp small"><tr><th></th>${rows.map(c=>`<th><a href="c/${c.org}.html">${esc(c.name)}</a></th>`).join('')}</tr>
   ${fields.map(([l,f])=>`<tr><th>${l}</th>${rows.map(c=>`<td>${esc(f(c)??'—')}</td>`).join('')}</tr>`).join('')}</table></div>`;
  $('#clr').onclick = ()=>{picked.clear(); render(); renderCompare();};
}
list.addEventListener('change', e => { const o = e.target.dataset.pick; if(!o) return;
  if(e.target.checked){ if(picked.size>=4){ e.target.checked=false; return;} picked.add(o);} else picked.delete(o); renderCompare(); });
[q, sort, ...filters].forEach(el => el.addEventListener('input', render));
const params = new URLSearchParams(location.search); if(params.get('q')) q.value = params.get('q');
render();
document.addEventListener('keydown', e => { if(e.key==='/' && document.activeElement!==q){ e.preventDefault(); q.focus(); }});
})();
