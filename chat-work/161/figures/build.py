# Charts for issue 161, in the team-admin/visuals standard. Series only from apm-data run 42241622745; every other value from replicate run 42236070544.
import asyncio,base64,json,pathlib,datetime as dt
from playwright.async_api import async_playwright
fs=pathlib.Path("/tmp/claude-0/-home-claude/4cd2f0cf-51b3-5484-a5d0-75fb880e4226/scratchpad/fig2/node_modules/@fontsource")
def font(pkg,fam,w):
    b=base64.b64encode((fs/pkg/f"files/{pkg}-latin-{w}-normal.woff2").read_bytes()).decode()
    return f"@font-face{{font-family:'{fam}';font-weight:{w};src:url(data:font/woff2;base64,{b}) format('woff2')}}"
F="".join([font("space-grotesk","Space Grotesk",500),font("space-grotesk","Space Grotesk",700),font("ibm-plex-mono","Plex",400),font("ibm-plex-mono","Plex",600)])
rows=json.load(open("series.json"))
LOGO=json.load(open("/home/claude/team-admin/visuals/logos.json"))
def logo(n,size,color=None):
    l=LOGO[n]; return f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" style="flex:none"><path fill="{color or "#"+l["hex"]}" d="{l["path"]}"/></svg>'
def nlogo(n,x,y,size):
    l=LOGO[n]; return f'<svg x="{x}" y="{y}" viewBox="0 0 24 24" width="{size}" height="{size}"><path fill="#{l["hex"]}" d="{l["path"]}"/></svg>'
S={(r["metric"],r["service"]):dict(map(tuple,r["points"])) for r in rows}
BLUE,PINK,TEAL="#1436E0","#D61F7A","#0B7A75"; INK,MID,GRID="#101426","#596078","#E6E8F0"
clock=lambda ms:dt.datetime.fromtimestamp(ms/1000,dt.timezone.utc).strftime("%H:%M:%S")
STEP=30000     # the series report every 10 to 20 s. Only a longer stretch counts as no reading
def lines(w,h,series,top,label=True,STEP=STEP):
    """Real points only, each one marked. A dotted join crosses the stretch with no load, so it can't read as measured traffic."""
    ts=[t for _,d,_,_ in series for t in d]; t0,t1=min(ts),max(ts)
    L,T,B,R=34,8,20,(120 if label else 22); X=lambda t:L+(t-t0)/(t1-t0)*(w-L-R); Y=lambda v:max(T,h-B-(v/top)*(h-B-T)); s=""
    for i in range(5):
        v=top*i/4; y=Y(v)
        s+=f'<line x1="{L}" x2="{w-R}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}"/><text x="{L-6}" y="{y+4:.1f}" text-anchor="end" fill="{MID}" font-size="11">{v:.0f}</text>'
    ends=[]
    for name,d,c,dash in series:
        pts=sorted(d.items())
        for (a,va),(b,vb) in zip(pts,pts[1:]):
            gap=' stroke-dasharray="2 5"' if b-a>STEP else dash
            s+=f'<line x1="{X(a):.1f}" y1="{Y(va):.1f}" x2="{X(b):.1f}" y2="{Y(vb):.1f}" stroke="{c}" stroke-width="{1.4 if b-a>STEP else 2.2}"{gap}/>'
        for t,v in pts: s+=f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="2" fill="{c}"/>'
        ends.append([name,Y(pts[-1][1]),c])
    ends.sort(key=lambda e:e[1])
    for i in range(1,len(ends)): ends[i][1]=max(ends[i][1],ends[i-1][1]+14)
    if ends and ends[-1][1]>h-B-2:
        over=ends[-1][1]-(h-B-2); ends=[[n,y-over,c] for n,y,c in ends]
    for name,y,c in (ends if label else []): s+=f'<text x="{w-R+7}" y="{y+4:.1f}" fill="{INK}" font-size="12">{name}</text>'
    for t,a in ((t0,"start"),(t0+(t1-t0)//2,"middle"),(t1,"end")):
        s+=f'<text x="{X(t):.1f}" y="{h-4}" text-anchor="{a}" fill="{MID}" font-size="11">{clock(t)}</text>'
    return f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="Plex">{s}</svg>'
ms=lambda m,sv:{t:v/1e6 for t,v in S[(m,sv)].items()}
DASH=' stroke-dasharray="7 4"'
ms=lambda m,sv:{t:v/1e6 for t,v in S[(m,sv)].items()}
mib=lambda sv:{t:v/1048576 for t,v in S[("jvm_memory",sv)].items()}
DASH=' stroke-dasharray="7 4"'
def req(w,h): return lines(w,h,[("orders",S[("requests","orders")],BLUE,""),("inventory",S[("requests","inventory")],PINK,DASH)],240,label=False)
def lat(w,h,sv="inventory",top=120): return lines(w,h,[("p99",ms("latency_p99",sv),PINK,""),("p90",ms("latency_p90",sv),BLUE,""),("p50",ms("latency_p50",sv),TEAL,"")],top)
def mem(w,h): return lines(w,h,[("inventory",mib("inventory"),PINK,DASH),("orders",mib("orders"),BLUE,"")],400)
key=f'<span style="color:{BLUE}">&#9632;</span> orders &nbsp;<span style="color:{PINK}">&#9632;</span> inventory'
def nd(x,y,w,h,n,sub,lg=None,c=BLUE):
    tx=x+w/2+(22 if lg else 0)
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#fff" stroke="{c}" stroke-width="2.5"/>{nlogo(lg,x+14,y+h/2-19,38) if lg else ""}<text x="{tx}" y="{y+h/2-2}" text-anchor="middle" fill="{INK}" font-family="Space Grotesk" font-weight="700" font-size="18">{n}</text><text x="{tx}" y="{y+h/2+16}" text-anchor="middle" fill="{MID}" font-family="Plex" font-size="11.5">{sub}</text>'
def smap(W):
    """The lab's three parts, as the lab deploys them. This is the design, not a map read from Splunk."""
    nw=int(W*.27); g=(W-3*nw-6)/2; x=[3,3+nw+g,3+2*(nw+g)]; y,h=6,68; my=y+h/2
    arrow=lambda x1,x2:f'<line x1="{x1}" x2="{x2-9}" y1="{my}" y2="{my}" stroke="{INK}" stroke-width="2"/><path d="M{x2-10} {my-6}l10 6-10 6z" fill="{INK}"/>'
    a=arrow(x[0]+nw,x[1])+arrow(x[1]+nw,x[2])
    return f'<svg viewBox="0 0 {W} {y+h+4}" width="{W}" height="{y+h+4}">{a}{nd(x[0],y,nw,h,"orders","Spring Boot",lg="spring")}{nd(x[1],y,nw,h,"inventory","Spring Boot",lg="spring")}{nd(x[2],y,nw,h,"Postgres","16.4",lg="postgres",c=TEAL)}</svg>'
BASE=f"""*{{box-sizing:border-box;margin:0;padding:0}}{F}body{{background:#1436E0;-webkit-font-smoothing:antialiased;font-family:'Space Grotesk';color:#fff}}
.p{{background:#fff;color:{INK};padding:12px 14px}}.h{{font:600 12.5px 'Plex';color:{INK};margin-bottom:6px}}.u{{color:{MID};font-weight:400}}
.s b{{display:block;font:700 34px/1 'Space Grotesk';color:{BLUE};white-space:nowrap}}.s span{{font:400 11.5px 'Plex';color:{MID}}}
.k{{font:400 11.5px 'Plex';color:{MID}}}"""
chip=lambda n,t,z=30:f'<div style="display:flex;flex-direction:column;align-items:center;gap:3px;background:#fff;color:{INK};width:92px;height:66px;justify-content:center;font:600 10px Plex;overflow:hidden"><div style="height:30px;display:flex;align-items:center">{logo(n,z)}</div>{t}</div>'
FOOT="Real series read from Splunk for my lab run (UTC), charted by me. Not a Splunk screen. Logos belong to their owners."
COVER=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:1200px;height:630px;padding:28px 36px 24px;display:flex;flex-direction:column;gap:12px;overflow:hidden">
<div style="display:flex;justify-content:space-between;align-items:flex-end;gap:16px"><div><div style="font:400 13.5px 'Plex';color:#FFE14D">OPENTELEMETRY JAVA AGENT + SPLUNK OBSERVABILITY CLOUD</div><div style="font:700 42px/1.08 'Space Grotesk';letter-spacing:-.02em;margin-top:3px">One setting. <span style="color:#FFE14D">Postgres p95 in Splunk.</span></div></div>
<div style="display:flex;gap:6px">{chip("otel","OpenTelemetry")}{chip("eks","Amazon EKS")}{chip("splunk","Splunk",72)}</div></div>
<div style="display:flex;gap:12px"><div class=p style="flex:1"><div class=h>The lab <span class=u>same jar and image before and after, no code changes</span></div>{smap(730)}</div>
<div class=p style="width:366px;display:flex;justify-content:space-around;align-items:center;padding:12px 20px"><div class=s><b>0 to 14</b><span>series per statement</span></div><div class=s><b>36</b><span>dimensions per series</span></div></div></div>
<div style="display:flex;gap:12px;flex:1;min-height:0">
<div class=p style="flex:1;min-width:0"><div class=h>Requests <span class=u>per 10 s</span> <span style="float:right;font-weight:400">{key}</span></div>{req(340,272)}</div>
<div class=p style="flex:1;min-width:0"><div class=h>inventory latency <span class=u>ms, cut at 120, dotted: no load</span></div>{lat(340,272)}</div>
<div class=p style="flex:1;min-width:0"><div class=h>JVM memory used <span class=u>MiB</span></div>{mem(340,272)}</div></div>
<div style="display:flex;justify-content:space-between;font:400 12.5px 'Plex'"><span>{FOOT}</span><span>showmerit.com/abhimanyu</span></div></body>"""
WIDE=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:900px;padding:16px;display:flex;flex-direction:column;gap:10px">
<div class=p><div class=h>Requests <span class=u>per 10 s: no opt-in, then the opt-in alone, then the main load</span> <span style="float:right;font-weight:400">{key}</span></div>{req(844,190)}</div>
<div class=p><div class=h>inventory latency <span class=u>ms, Splunk APM's series for the service, axis cut at 120, dotted: no load</span></div>{lat(844,230)}</div>
<div style="font:400 12px 'Plex'">{FOOT}</div></body>"""
def bar(name,dims,kept,note):
    W=460; x=lambda d:d/40*W; c=BLUE if kept else PINK
    return f"""<div style="display:flex;align-items:center;gap:10px;height:34px"><div style="width:262px;flex:none;font:400 12px 'Plex';color:{INK}">{name}</div>
<svg viewBox="0 0 {W} 26" width="{W}" height="26" style="flex:none"><rect x="0" y="5" width="{x(dims):.1f}" height="16" fill="{c}"/><line x1="{x(36):.1f}" x2="{x(36):.1f}" y1="0" y2="26" stroke="{INK}" stroke-width="2" stroke-dasharray="3 3"/></svg>
<div style="font:600 12px 'Plex';color:{c};white-space:nowrap">{note}</div></div>"""
LIMIT=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:900px;padding:16px;display:flex;flex-direction:column;gap:10px">
<div class=p style="padding:16px 18px"><div class=h style="margin-bottom:10px">Dimensions per series <span class=u>dashed line: Splunk's 36. Blue: Splunk held it. Pink: no series held</span></div>
<div class=k style="margin:0 0 4px">the metric, on real EKS with the opt-in alone</div>
{bar("db.client.operation.duration_bucket",36,True,"36, 80 series")}
{bar("db.client.operation.duration_count",35,True,"35, 8 series")}
<div class=k style="margin:12px 0 4px">my test points, sent straight to Splunk</div>
{bar("30 dimensions",30,True,"held")}{bar("36 dimensions",36,True,"held")}{bar("37 dimensions",37,False,"not held")}{bar("40 dimensions",40,False,"not held")}
</div><div style="font:400 12px 'Plex'">Counts read from Splunk's metric time series API for my lab run, leaving out sf_metric, charted by me. Not a Splunk screen.</div></body>"""

MIN=[1793977380000+i*60000 for i in range(6)]    # the six minutes in the lab's output, ending 15:03 to 15:08 UTC
PROG=dict(zip(MIN,[48.27956989247313,48.5239852398524,48.07560137457045,47.972972972972975,47.99331103678931,47.986577181208055]))
TIMER=dict(zip(MIN,[38.03,42.17,37.42,36.33,33.64,32.55]))
EDGE=' stroke-dasharray="3 5"'
def p95(w,h): return lines(w,h,[("le 50 ms",{t:50 for t in MIN},MID,EDGE),("SignalFlow p95",PROG,BLUE,""),("timer p95",TIMER,PINK,""),("le 10 ms",{t:10 for t in MIN},MID,EDGE)],60,STEP=90000).replace('r="2"','r="3"')
P95=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:900px;padding:16px;display:flex;flex-direction:column;gap:10px">
<div class=p><div class=h>The pg_sleep(0.03) statement, one reading per minute <span class=u>ms. Grey: the stored bucket edges</span></div>{p95(844,270).replace('width="844"','width="844"')}</div>
<div class=p style="display:flex;gap:30px;padding:12px 18px"><div class=s><b style="font-size:26px">47.99</b><span>SignalFlow p95, median, min 47.97, max 48.52</span></div><div class=s><b style="font-size:26px;color:{PINK}">36.33</b><span>timer p95, median, min 32.55, max 42.17</span></div></div>
<div style="font:400 12px 'Plex'">Values printed by my lab run, from the SignalFlow stream and a test timer around one JDBC call. Charted by me. Not a Splunk screen.</div></body>"""
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for n,h,w,hh in (("cover",COVER,1200,630),("step-2",WIDE,900,100),("step-3",LIMIT,900,100),("step-4",P95,900,100)):
            pg=await b.new_page(viewport={"width":w,"height":hh},device_scale_factor=2)
            await pg.set_content(h); await pg.evaluate("document.fonts.ready"); await pg.wait_for_timeout(300)
            over=await pg.evaluate("[...document.querySelectorAll('.p,body')].filter(e=>e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1).length")
            print(n,"overflowing boxes:",over)
            await pg.screenshot(path=f"{n}.png",full_page=n!="cover")
        await b.close()
asyncio.run(main())
