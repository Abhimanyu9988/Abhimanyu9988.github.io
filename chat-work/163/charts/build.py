# Charts for issue 163, in the team-admin/visuals standard. Series only from apm-data run 42124200881; every other value from replicate run 42119202877.
import asyncio,base64,json,pathlib,datetime as dt
from playwright.async_api import async_playwright
fs=pathlib.Path("../fig3/node_modules/@fontsource")
def font(pkg,fam,w):
    b=base64.b64encode((fs/pkg/f"files/{pkg}-latin-{w}-normal.woff2").read_bytes()).decode()
    return f"@font-face{{font-family:'{fam}';font-weight:{w};src:url(data:font/woff2;base64,{b}) format('woff2')}}"
F="".join([font("space-grotesk","Space Grotesk",500),font("space-grotesk","Space Grotesk",700),font("ibm-plex-mono","Plex",400),font("ibm-plex-mono","Plex",600)])
rows=json.load(open("../fig163/series.json"))
LOGO=json.load(open("/home/claude/team-admin/visuals/logos.json"))
def logo(n,size,color=None):
    l=LOGO[n]; return f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" style="flex:none"><path fill="{color or "#"+l["hex"]}" d="{l["path"]}"/></svg>'
def nlogo(n,x,y,size):
    l=LOGO[n]; return f'<svg x="{x}" y="{y}" viewBox="0 0 24 24" width="{size}" height="{size}"><path fill="#{l["hex"]}" d="{l["path"]}"/></svg>'
S={(r["metric"],r["service"]):dict(map(tuple,r["points"])) for r in rows}
BLUE,PINK,TEAL="#1436E0","#D61F7A","#0B7A75"; INK,MID,GRID="#101426","#596078","#E6E8F0"
clock=lambda ms:dt.datetime.fromtimestamp(ms/1000,dt.timezone.utc).strftime("%H:%M:%S")
STEP=30000     # the series report every 10 to 20 s. Only a longer stretch counts as no reading
def lines(w,h,series,top,label=True):
    """Real points only, each one marked. A dotted join crosses the stretch with no load, so it can't read as measured traffic."""
    ts=[t for _,d,_,_ in series for t in d]; t0,t1=min(ts),max(ts)
    L,T,B,R=34,8,20,(82 if label else 22); X=lambda t:L+(t-t0)/(t1-t0)*(w-L-R); Y=lambda v:h-B-(v/top)*(h-B-T); s=""
    for i in range(5):
        v=top*i/4; y=Y(v)
        s+=f'<line x1="{L}" x2="{w-R}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}"/><text x="{L-6}" y="{y+4:.1f}" text-anchor="end" fill="{MID}" font-size="11">{v:.0f}</text>'
    ends=[]
    for name,d,c,dash in series:
        pts=sorted(d.items())
        for (a,va),(b,vb) in zip(pts,pts[1:]):
            gap=' stroke-dasharray="2 5"' if b-a>STEP else dash
            s+=f'<line x1="{X(a):.1f}" y1="{Y(va):.1f}" x2="{X(b):.1f}" y2="{Y(vb):.1f}" stroke="{c}" stroke-width="{1.4 if b-a>STEP else 2.2}"{gap}/>'
        for t,v in pts: s+=f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="2.8" fill="{c}"/>'
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
def req(w,h): return lines(w,h,[("orders",S[("requests","orders")],BLUE,""),("inventory",S[("requests","inventory")],PINK,DASH)],400,label=False)
def lat(w,h,sv="orders",top=80): return lines(w,h,[("p99",ms("latency_p99",sv),PINK,""),("p90",ms("latency_p90",sv),BLUE,""),("p50",ms("latency_p50",sv),TEAL,"")],top)
key=f'<span style="color:{BLUE}">&#9632;</span> orders &nbsp;<span style="color:{PINK}">&#9632;</span> inventory'
def nd(x,y,w,h,n,sub,lg=None,c=BLUE):
    tx=x+w/2+(22 if lg else 0)
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#fff" stroke="{c}" stroke-width="2.5"/>{nlogo(lg,x+14,y+h/2-19,38) if lg else ""}<text x="{tx}" y="{y+h/2-2}" text-anchor="middle" fill="{INK}" font-family="Space Grotesk" font-weight="700" font-size="18">{n}</text><text x="{tx}" y="{y+h/2+16}" text-anchor="middle" fill="{MID}" font-family="Plex" font-size="11.5">{sub}</text>'
def smap(W):
    """Exactly what the Splunk topology returned with the first value on: load to orders, orders to inventory."""
    nw=int(W*.27); g=(W-3*nw-6)/2; x=[3,3+nw+g,3+2*(nw+g)]; y,h=6,68; my=y+h/2
    arrow=lambda x1,x2:f'<line x1="{x1}" x2="{x2-9}" y1="{my}" y2="{my}" stroke="{INK}" stroke-width="2"/><path d="M{x2-10} {my-6}l10 6-10 6z" fill="{INK}"/>'
    a=arrow(x[0]+nw,x[1])+arrow(x[1]+nw,x[2])
    return f'<svg viewBox="0 0 {W} {y+h+4}" width="{W}" height="{y+h+4}">{a}{nd(x[0],y,nw,h,"load","test client",None,MID)}{nd(x[1],y,nw,h,"orders","Spring Boot",lg="spring")}{nd(x[2],y,nw,h,"inventory","Spring Boot",lg="spring")}</svg>'
BASE=f"""*{{box-sizing:border-box;margin:0;padding:0}}{F}body{{background:#1436E0;-webkit-font-smoothing:antialiased;font-family:'Space Grotesk';color:#fff}}
.p{{background:#fff;color:{INK};padding:12px 14px}}.h{{font:600 12.5px 'Plex';color:{INK};margin-bottom:6px}}.u{{color:{MID};font-weight:400}}
.s b{{display:block;font:700 34px/1 'Space Grotesk';color:{BLUE};white-space:nowrap}}.s span{{font:400 11.5px 'Plex';color:{MID}}}
.k{{font:400 11.5px 'Plex';color:{MID}}}"""
chip=lambda n,t,z=30:f'<div style="display:flex;flex-direction:column;align-items:center;gap:3px;background:#fff;color:{INK};width:92px;height:66px;justify-content:center;font:600 10px Plex;overflow:hidden"><div style="height:30px;display:flex;align-items:center">{logo(n,z)}</div>{t}</div>'
FOOT="Real series read from Splunk for my lab run (UTC), charted by me. Not a Splunk screen. Logos belong to their owners."
COVER=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:1200px;height:630px;padding:28px 36px 24px;display:flex;flex-direction:column;gap:12px;overflow:hidden">
<div style="display:flex;justify-content:space-between;align-items:flex-end;gap:16px"><div><div style="font:400 13.5px 'Plex';color:#FFE14D">OPENTELEMETRY eBPF INSTRUMENTATION + SPLUNK APM</div><div style="font:700 42px/1.08 'Space Grotesk';letter-spacing:-.02em;margin-top:3px">One Helm value. <span style="color:#FFE14D">Zero pod restarts.</span></div></div>
<div style="display:flex;gap:6px">{chip("otel","OpenTelemetry")}{chip("k8s","Kubernetes")}{chip("splunk","Splunk APM",72)}</div></div>
<div style="display:flex;gap:12px"><div class=p style="flex:1"><div class=h>Service map <span class=u>after the first value</span></div>{smap(730)}</div>
<div class=p style="width:366px;display:flex;justify-content:space-around;align-items:center;padding:12px 20px"><div class=s><b>2 of 2</b><span>services on the map</span></div><div class=s><b>0</b><span>restarts, 3 pods</span></div></div></div>
<div style="display:flex;gap:12px;flex:1;min-height:0">
<div class=p style="flex:1;min-width:0"><div class=h>Requests <span class=u>per 10 s</span> <span style="float:right;font-weight:400">{key}</span></div>{req(340,272)}</div>
<div class=p style="flex:1;min-width:0"><div class=h>orders latency <span class=u>ms, dotted: no load</span></div>{lat(340,272)}</div>
<div class=p style="flex:1;min-width:0"><div class=h>inventory latency <span class=u>ms, dotted: no load</span></div>{lat(340,272,"inventory",24)}</div></div>
<div style="display:flex;justify-content:space-between;font:400 12.5px 'Plex'"><span>{FOOT}</span><span>showmerit.com/abhimanyu</span></div></body>"""
WIDE=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:900px;padding:16px;display:flex;flex-direction:column;gap:10px">
<div class=p><div class=h>Service map <span class=u>what Splunk APM returned with the first value on</span></div>{smap(836)}</div>
<div class=p><div class=h>Requests <span class=u>per 10 s. The dotted stretch is the pause between load runs.</span> <span style="float:right;font-weight:400">{key}</span></div>{req(842,190)}</div>
<div style="display:flex;gap:10px"><div class=p style="flex:1;min-width:0"><div class=h>orders latency <span class=u>ms</span></div>{lat(400,190)}</div><div class=p style="flex:1;min-width:0"><div class=h>inventory latency <span class=u>ms</span></div>{lat(400,190,"inventory",24)}</div></div>
<div style="font:400 12px 'Plex'">{FOOT}</div></body>"""
spans=[("load",0,"GET /orders/*","the test client",MID,None),("orders",1,"GET /orders/{id}","the request arrives",BLUE,"spring"),("orders",2,"GET /stock/{id}","the call leaves for the other node",BLUE,"spring"),("inventory",3,"GET /stock/{id}","the call arrives",PINK,"spring")]
row=lambda s,d,op,note,c,lg:f'''<div style="display:flex;align-items:center;height:46px;margin-left:{d*30}px;border-left:5px solid {c};padding-left:12px;margin-bottom:8px;background:#F5F6FB"><div style="width:124px;color:{MID};font:400 13px 'Plex';display:flex;align-items:center;gap:7px">{logo(lg,18) if lg else ""}{s}</div><div style="font:600 14px 'Plex';color:{INK};width:190px">{op}</div><div style="font:400 12.5px 'Plex';color:{MID}">{note}</div></div>'''
TRACE=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:900px;padding:16px;display:flex;flex-direction:column;gap:10px">
<div class=p style="padding:16px 18px"><div class=h>One order across two nodes <span class=u>with both Helm values on</span></div><div class=k style="margin-bottom:12px">trace 07515449a2bc2d3973c9a54e05c5a3c3, order from each span's parent</div>{"".join(row(*x) for x in spans)}</div>
<div style="display:flex;gap:10px"><div class=p style="flex:1;padding:14px 18px"><div class=s><b>39 of 39</b><span>traces found in Splunk held both services, 48 IDs searched, both values on</span></div></div>
<div class=p style="flex:1;padding:14px 18px"><div class=s><b style="color:{MID}">Unmeasured</b><span>first value alone: 0 of 56 sampled IDs came back in the 90-second wait</span></div></div></div>
<div style="font:400 12px 'Plex'">Spans from the Splunk trace API, charted by me. Not a Splunk screen. Logos belong to their owners.</div></body>"""
pods=[("orders","spring","f0c67713-a216-4a19-a520-9d124ff13fd2","22:26:49Z"),("inventory","spring","ca653889-b1a6-4447-936a-93d5e12206a2","22:26:50Z"),("postgres","postgres","d2e39469-51ee-4d5a-ad27-b8f3ed5abfc2","22:26:55Z")]
th="".join(f'<th style="text-align:left;font:600 11.5px Plex;color:{MID};padding:8px 10px;border-bottom:2px solid {INK}">{x}</th>' for x in ("Pod","UID, identical at all three checks","Started","Restarts"))
tr="".join(f'<tr><td style="padding:11px 10px;font:600 14px Plex;white-space:nowrap"><span style="display:inline-flex;align-items:center;gap:8px">{logo(l,20)}{n}</span></td><td style="padding:11px 10px;font:400 12.5px Plex">{u}</td><td style="padding:11px 10px;font:400 12.5px Plex">{t}</td><td style="padding:11px 10px;font:700 24px Space Grotesk;color:{BLUE}">0</td></tr>' for n,l,u,t in pods)
PODS=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:900px;padding:16px;display:flex;flex-direction:column;gap:10px"><div class=p style="padding:14px 16px"><div class=h>The same pods before OBI, after the first value and after the second</div><table style="border-collapse:collapse;width:100%">{th}{tr}</table></div>
<div style="font:400 12px 'Plex'">Values printed by the lab's kubectl snapshots, charted by me. Logos belong to their owners.</div></body>"""
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for n,h,w,hh in (("cover",COVER,1200,630),("step-2",WIDE,900,100),("step-3",TRACE,900,100),("step-4",PODS,900,100)):
            pg=await b.new_page(viewport={"width":w,"height":hh},device_scale_factor=2)
            await pg.set_content(h); await pg.evaluate("document.fonts.ready"); await pg.wait_for_timeout(300)
            over=await pg.evaluate("[...document.querySelectorAll('.p,body')].filter(e=>e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1).length")
            print(n,"overflowing boxes:",over)
            await pg.screenshot(path=f"{n}.png",full_page=n!="cover")
        await b.close()
asyncio.run(main())
