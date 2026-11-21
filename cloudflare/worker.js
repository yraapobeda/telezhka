// TelezhkaProxy — склейка подписок + страница подписки (Cloudflare Worker)
//
// VPN-приложение по ссылке получает ключи со всех стран (base64),
// браузер — красивую страницу со статусом, трафиком и кнопками импорта.

const SOURCES = {
  pl: "https://s-25434.pl17.h1clayd.shop/ngqgzl7saqmhl9ph",
  de: "https://s-25471.de20.h1clayd.click/xvs2kk0qul7gnmz9",
};
const BRAND = "TelezhkaProxy";
const SUPPORT = "https://t.me/TelezhkaProxy";
const BOT = "https://t.me/TelezhkaProxyBot"; // ← поменяй на юзернейм своего бота

const CLIENT_UA = /happ|v2ray|hiddify|streisand|clash|sing-?box|nekobox|nekoray|shadowrocket|foxray|karing|throne|incy|mihomo|loon|surge|quantumult/i;

const b64e = s => btoa(String.fromCharCode(...new TextEncoder().encode(s)));
const b64d = s => new TextDecoder().decode(Uint8Array.from(atob(s.trim()), c => c.charCodeAt(0)));
const parseInfo = h => Object.fromEntries((h || "").split(";").map(p => p.trim().split("="))
  .filter(([k, v]) => k && /^\d+$/.test(v)).map(([k, v]) => [k, +v]));
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function collect(token) {
  const res = await Promise.all(Object.entries(SOURCES).map(async ([k, url]) => {
    try {
      const r = await fetch(`${url}/${token}${k}`, { headers: { "User-Agent": "v2rayN/7.0" } });
      if (!r.ok) return null;
      let t = await r.text();
      if (!t.includes("://")) t = b64d(t);
      const links = t.split("\n").map(l => l.trim()).filter(l => l.includes("://"));
      return links.length ? { links, i: parseInfo(r.headers.get("subscription-userinfo")) } : null;
    } catch { return null; }
  }));
  const ok = res.filter(Boolean);
  if (!ok.length) return null;
  const sum = k => ok.reduce((a, r) => a + (r.i[k] || 0), 0);
  return {
    links: ok.flatMap(r => r.links),
    up: sum("upload"), down: sum("download"),
    total: ok.some(r => !r.i.total) ? 0 : sum("total"),
    expire: Math.max(...ok.map(r => r.i.expire || 0)),
  };
}

const gb = b => (b / 1024 ** 3).toFixed(b < 10 * 1024 ** 3 ? 2 : 1) + " ГБ";
const remark = l => { try { return decodeURIComponent(l.split("#")[1] || "").replace(/\s*\|\s*TelezhkaProxy\s*$/i, ""); } catch { return ""; } };

function page(subUrl, d) {
  const now = Date.now() / 1000;
  const active = !d.expire || d.expire > now;
  const daysLeft = d.expire ? Math.max(0, Math.ceil((d.expire - now) / 86400)) : null;
  const used = d.up + d.down;
  const pct = d.total ? Math.min(100, used / d.total * 100) : 0;
  const date = d.expire ? new Date(d.expire * 1000).toLocaleDateString("ru-RU", { timeZone: "Europe/Moscow", day: "numeric", month: "long", year: "numeric" }) : "бессрочно";
  const servers = d.links.map(remark).filter(Boolean);
  const enc = encodeURIComponent(subUrl);
  const apps = [
    ["Happ", `happ://add/${subUrl}`, "iOS · Android · Windows · macOS"],
    ["v2RayTun", `v2raytun://import/${subUrl}`, "iOS · Android"],
    ["Hiddify", `hiddify://import/${subUrl}#${encodeURIComponent(BRAND)}`, "Android · Windows · macOS"],
  ];

  return `<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>${BRAND} — подписка</title>
<style>
:root{--bg:#0B1220;--card:#121B2E;--line:#1E2A44;--txt:#E8EEFA;--mut:#8C9AB8;--blue:#2F80FF;--cyan:#22D3EE;--org:#FF8A3D;--red:#FF5C6C;--grn:#34D399}
*{box-sizing:border-box;margin:0}
html{background:var(--bg)}
body{background:radial-gradient(1200px 600px at 50% -200px,#16305f 0%,var(--bg) 60%) fixed;color:var(--txt);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;min-height:100vh;padding:28px 16px 48px}
.wrap{max-width:480px;margin:0 auto;display:flex;flex-direction:column;gap:14px}
.head{display:flex;align-items:center;gap:12px;margin-bottom:6px}
.logo{width:44px;height:44px;border-radius:12px;background:var(--card);display:grid;place-items:center;border:1px solid var(--line)}
.head h1{font-size:20px;font-weight:700;letter-spacing:.2px}.head span{color:var(--cyan)}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px}
.status{display:flex;justify-content:space-between;align-items:center}
.pill{padding:5px 12px;border-radius:99px;font-size:13px;font-weight:600}
.on{background:rgba(52,211,153,.12);color:var(--grn)}.off{background:rgba(255,92,108,.12);color:var(--red)}
.big{font-size:30px;font-weight:800;margin-top:10px}.mut{color:var(--mut);font-size:13px}
.bar{height:8px;background:var(--line);border-radius:99px;overflow:hidden;margin:12px 0 6px}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--cyan));border-radius:99px}
.row{display:flex;justify-content:space-between;font-size:13px;color:var(--mut)}
h2{font-size:14px;font-weight:600;color:var(--mut);margin-bottom:10px;text-transform:uppercase;letter-spacing:.6px}
.srv{display:flex;flex-wrap:wrap;gap:8px}.srv div{background:#0E1626;border:1px solid var(--line);padding:7px 11px;border-radius:10px;font-size:14px}
.app{display:flex;align-items:center;justify-content:space-between;padding:13px 14px;border-radius:14px;background:#0E1626;border:1px solid var(--line);color:var(--txt);text-decoration:none;margin-bottom:8px;transition:.15s}
.app:hover{border-color:var(--blue);transform:translateY(-1px)}.app b{display:block}.app small{color:var(--mut);font-size:12px}
.app em{font-style:normal;background:var(--blue);padding:6px 12px;border-radius:9px;font-size:13px;font-weight:600}
.copy{width:100%;border:0;cursor:pointer;padding:14px;border-radius:14px;font:600 15px system-ui;color:#fff;background:linear-gradient(90deg,var(--blue),#4C8DFF)}
.copy.done{background:var(--grn)}
#qr{display:flex;justify-content:center;padding:14px;background:#fff;border-radius:14px;margin-top:12px}
.foot{display:flex;gap:10px}.foot a{flex:1;text-align:center;padding:12px;border-radius:14px;background:var(--card);border:1px solid var(--line);color:var(--txt);text-decoration:none;font-size:14px}
.foot a.acc{border-color:var(--org);color:var(--org)}
</style></head><body><div class="wrap">

<div class="head"><div class="logo"><svg width="26" height="26" viewBox="0 0 64 64"><g fill="none" stroke-linecap="round" stroke-width="5"><path stroke="#22D3EE" d="M6 26h14M10 34h12M14 42h8"/><path stroke="#2F80FF" stroke-linejoin="round" d="M24 14h6l7 26h18l5-18H32"/></g><circle cx="38" cy="50" r="4" fill="#FF8A3D"/><circle cx="52" cy="50" r="4" fill="#FF8A3D"/></svg></div>
<h1>Telezhka<span>Proxy</span></h1></div>

<div class="card">
  <div class="status"><span class="mut">Подписка</span><span class="pill ${active ? "on" : "off"}">${active ? "● активна" : "● истекла"}</span></div>
  <div class="big">${daysLeft === null ? "∞" : daysLeft + " " + (daysLeft % 10 === 1 && daysLeft % 100 !== 11 ? "день" : [2,3,4].includes(daysLeft % 10) && ![12,13,14].includes(daysLeft % 100) ? "дня" : "дней")}</div>
  <div class="mut">до ${esc(date)}</div>
  ${d.total ? `<div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div>
  <div class="row"><span>Использовано ${gb(used)}</span><span>из ${gb(d.total)}</span></div>`
  : `<div class="row" style="margin-top:12px"><span>Использовано ${gb(used)}</span><span>безлимит ∞</span></div>`}
</div>

${servers.length ? `<div class="card"><h2>Серверы</h2><div class="srv">${servers.map(s => `<div>${esc(s)}</div>`).join("")}</div></div>` : ""}

<div class="card"><h2>Добавить в приложение</h2>
${apps.map(([n, href, os]) => `<a class="app" href="${esc(href)}"><span><b>${n}</b><small>${os}</small></span><em>Открыть</em></a>`).join("")}
<button class="copy" id="cp">Скопировать ссылку подписки</button>
<div id="qr"></div>
<p class="mut" style="margin-top:10px;text-align:center">Нет нужного приложения? Скопируй ссылку и вставь её через «+» → «Импорт из буфера»</p>
</div>

<div class="foot"><a class="acc" href="${BOT}">${active ? "Продлить" : "Купить"}</a><a href="${SUPPORT}">Поддержка</a></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js"></script>
<script>
const url=${JSON.stringify(subUrl)};
try{new QRCode(document.getElementById("qr"),{text:url,width:200,height:200,correctLevel:QRCode.CorrectLevel.M})}catch(e){document.getElementById("qr").remove()}
const b=document.getElementById("cp");
b.onclick=async()=>{try{await navigator.clipboard.writeText(url)}catch(e){const t=document.createElement("textarea");t.value=url;document.body.appendChild(t);t.select();document.execCommand("copy");t.remove()}
b.textContent="Скопировано ✓";b.classList.add("done");setTimeout(()=>{b.textContent="Скопировать ссылку подписки";b.classList.remove("done")},1800)};
</script></body></html>`;
}

export default {
  async fetch(req) {
    const u = new URL(req.url);
    const m = u.pathname.match(/^\/sub\/([A-Za-z0-9_-]{8,64})$/);
    if (!m) return new Response("not found", { status: 404 });

    const d = await collect(m[1]);
    if (!d) return new Response("not found", { status: 404 });

    const ua = req.headers.get("user-agent") || "";
    const browser = /mozilla/i.test(ua) && !CLIENT_UA.test(ua)
      && (req.headers.get("accept") || "").includes("text/html") && u.searchParams.get("raw") === null;

    if (browser) {
      return new Response(page(`${u.origin}${u.pathname}`, d), { headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Robots-Tag": "noindex",
      }});
    }

    return new Response(b64e(d.links.join("\n")), { headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Profile-Title": "base64:" + b64e(BRAND),
      "Subscription-Userinfo": `upload=${d.up}; download=${d.down}; total=${d.total}; expire=${d.expire}`,
      "Profile-Update-Interval": "12",
      "Support-Url": SUPPORT,
      "Profile-Web-Page-Url": `${u.origin}${u.pathname}`,
      "Cache-Control": "no-store",
    }});
  },
};
