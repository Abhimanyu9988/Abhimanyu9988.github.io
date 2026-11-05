# Charts for the SQS article, in the APM-style look of fig3/apm.py. Series come only from apm-data run 42120334499; every number in text comes from replicate run 42116738351.
import asyncio,base64,json,pathlib,datetime as dt
from playwright.async_api import async_playwright
fs=pathlib.Path("node_modules/@fontsource")
def font(pkg,fam,w):
    b=base64.b64encode((fs/pkg/f"files/{pkg}-latin-{w}-normal.woff2").read_bytes()).decode()
    return f"@font-face{{font-family:'{fam}';font-weight:{w};src:url(data:font/woff2;base64,{b}) format('woff2')}}"
F="".join([font("space-grotesk","Space Grotesk",500),font("space-grotesk","Space Grotesk",700),font("ibm-plex-mono","Plex",400),font("ibm-plex-mono","Plex",600)])
rows=json.load(open("../series/series.json"))
S={(r["metric"],r["service"]):dict(map(tuple,r["points"])) for r in rows}
BLUE,PINK,GOLD="#1436E0","#D61F7A","#C98500"; INK,MID,GRID="#101426","#596078","#E6E8F0"
clock=lambda ms:dt.datetime.fromtimestamp(ms/1000,dt.timezone.utc).strftime("%H:%M:%S")
def bars(w,h,top=240):
    """Requests per 10 s. One pair of bars per real bucket. A bucket with no data stays empty, so idle time looks idle."""
    o_,f_=S[("requests","orders")],S[("requests","fulfilment")]
    t0,t1=min([*o_,*f_]),max([*o_,*f_]); ts=list(range(t0,t1+1,10000))
    L,T,B,R=38,8,22,6; bw=(w-L-R)/len(ts); Y=lambda v:h-B-(v/top)*(h-B-T); s=""
    for i in range(5):
        v=top*i/4; y=Y(v)
        s+=f'<line x1="{L}" x2="{w-R}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}"/><text x="{L-7}" y="{y+4:.1f}" text-anchor="end" fill="{MID}" font-size="11">{v:.0f}</text>'
    for i,t in enumerate(ts):
        x=L+i*bw; g=bw*.06; one=(bw-3*g)/2
        for j,(d,c) in enumerate(((o_,BLUE),(f_,PINK))):
            v=d.get(t)
            if v is None: continue
            bh=max((v/top)*(h-B-T),1.5)
            s+=f'<rect x="{x+g+j*(one+g):.1f}" y="{h-B-bh:.1f}" width="{one:.1f}" height="{bh:.1f}" fill="{c}"/>'
    for t,a in ((ts[0],"start"),(ts[len(ts)//2],"middle"),(ts[-1],"end")):
        x={"start":L,"middle":L+ts.index(t)*bw+bw/2,"end":w-R}[a]
        s+=f'<text x="{x:.1f}" y="{h-5}" text-anchor="{a}" fill="{MID}" font-size="11">{clock(t)}</text>'
    return f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="Plex">{s}</svg>'
key=f'<span style="color:{BLUE}">&#9632;</span> orders &nbsp;<span style="color:{PINK}">&#9632;</span> fulfilment'
def nd(x,y,w,h,n,sub,inf=False):
    c=PINK if inf else BLUE; dash=' stroke-dasharray="7 5"' if inf else ''
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#fff" stroke="{c}" stroke-width="2.5"{dash}/><text x="{x+w/2}" y="{y+h/2-2}" text-anchor="middle" fill="{INK}" font-family="Space Grotesk" font-weight="700" font-size="17">{n}</text><text x="{x+w/2}" y="{y+h/2+16}" text-anchor="middle" fill="{MID}" font-family="Plex" font-size="11.5">{sub}</text>'
def smap(W,direct=True):
    """The edges the Splunk topology returned: orders to sqs, sqs to fulfilment, orders to fulfilment, fulfilment to sqs."""
    nw=int(W*.25); g=(W-3*nw-6)/2; x=[3,3+nw+g,3+2*(nw+g)]; y=44 if direct else 6; h=68; my=y+h/2
    arrow=lambda x1,x2,yy:f'<line x1="{x1}" x2="{x2-9}" y1="{yy}" y2="{yy}" stroke="{INK}" stroke-width="2"/><path d="M{x2-10} {yy-6}l10 6-10 6z" fill="{INK}"/>'
    a=arrow(x[0]+nw,x[1],my-9)+arrow(x[1]+nw,x[2],my-9)
    a+=f'<line x1="{x[2]}" x2="{x[1]+nw+9}" y1="{my+11}" y2="{my+11}" stroke="{MID}" stroke-width="2"/><path d="M{x[1]+nw+10} {my+5}l-10 6 10 6z" fill="{MID}"/>'
    if direct:
        cx0,cx1=x[0]+nw/2,x[2]+nw/2
        a+=f'<path d="M{cx0} {y} V14 H{cx1} V{y-9}" fill="none" stroke="{INK}" stroke-width="2"/><path d="M{cx1-6} {y-10}l6 10 6-10z" fill="{INK}"/><text x="{(cx0+cx1)/2}" y="9" text-anchor="middle" fill="{MID}" font-family="Plex" font-size="11.5">one trace, across the queue</text>'
    return f'<svg viewBox="0 0 {W} {y+h+4}" width="{W}" height="{y+h+4}">{a}{nd(x[0],y,nw,h,"orders","service")}{nd(x[1],y,nw,h,"sqs","inferred",True)}{nd(x[2],y,nw,h,"fulfilment","service")}</svg>'
BASE=f"""*{{box-sizing:border-box;margin:0;padding:0}}{F}body{{background:#1436E0;-webkit-font-smoothing:antialiased;font-family:'Space Grotesk';color:#fff}}
.p{{background:#fff;color:{INK};padding:12px 14px}}.h{{font:600 12.5px 'Plex';color:{INK};margin-bottom:6px}}.u{{color:{MID};font-weight:400}}
.s b{{display:block;font:700 34px/1 'Space Grotesk';color:{BLUE};white-space:nowrap}}.s span{{font:400 11.5px 'Plex';color:{MID}}}
.m{{font:400 15px 'Plex';color:{INK}}}.k{{font:400 11.5px 'Plex';color:{MID};margin-bottom:2px}}.t{{background:#FFE14D;font-weight:600;padding:0 2px}}"""
HDR="354df141-feaef1cc58d93649bca2af18"
def proof(size=14.5):
    a,b=HDR.split("-"); m=f"font:400 {size}px 'Plex'"
    return f'''<div class=k>AWSTraceHeader on the waiting message</div><div style="{m}">Root=1-<span class=t>{a}</span>-<span class=t>{b}</span></div>
<div class=k style="margin-top:12px">trace ID on the producer span</div><div style="{m}"><span class=t>{a}{b}</span></div>'''
COVER=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:1200px;height:630px;padding:30px 36px 26px;display:flex;flex-direction:column;gap:12px;overflow:hidden">
<div><div style="font:400 13.5px 'Plex';color:#FFE14D">OPENTELEMETRY JAVA AGENT + AMAZON SQS IN SPLUNK APM</div><div style="font:700 46px/1.1 'Space Grotesk';letter-spacing:-.02em;margin-top:3px">One queue. One trace. <span style="color:#FFE14D">Zero code.</span></div></div>
<div style="display:flex;gap:12px"><div class=p style="flex:1"><div class=h>Service map</div>{smap(700)}</div>
<div class=p style="width:396px;display:flex;justify-content:space-between;align-items:center;padding:12px 20px"><div class=s><b>400/400</b><span>messages in one trace</span></div><div class=s><b>10/10</b><span>traces read in Splunk</span></div></div></div>
<div style="display:flex;gap:12px;flex:1;min-height:0">
<div class=p style="width:540px"><div class=h>Requests <span class=u>per 10 s</span> <span style="float:right;font-weight:400">{key}</span></div>{bars(510,236)}</div>
<div class=p style="flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center;padding:14px 20px"><div style="font:700 25px/1.15 'Space Grotesk';margin-bottom:18px">The trace travels<br>on the message</div>{proof()}</div></div>
<div style="display:flex;justify-content:space-between;font:400 12.5px 'Plex'"><span>Real data from my lab run (UTC), charted by me. Not a Splunk screen.</span><span>showmerit.com/abhimanyu</span></div></body>"""
WIDE=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:900px;padding:16px;display:flex;flex-direction:column;gap:10px">
<div class=p><div class=h>Service map <span class=u>from Splunk APM data, charted by me</span></div>{smap(836)}</div>
<div class=p><div class=h>Requests <span class=u>per 10 s, 400 orders then 100 orders</span> <span style="float:right;font-weight:400">{key}</span></div>{bars(842,220)}</div></body>"""
Q="&hellip;-orders"
step=lambda svc,c,name,note:f'''<div style="display:flex;align-items:center;height:46px;border-left:5px solid {c};padding-left:12px;margin-bottom:8px;background:#F5F6FB"><div style="width:110px;color:{MID};font:400 13px 'Plex'">{svc}</div><div style="flex:1;font:600 14px 'Plex'">{name}</div><div style="color:{MID};font:400 12px 'Plex';padding-right:12px">{note}</div></div>'''
TRACE=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:900px;padding:16px;display:flex;flex-direction:column;gap:10px"><div class=p style="padding:16px 18px"><div class=h style="margin-bottom:12px">One order, one trace <span class=u>names from the Splunk trace API</span></div>
{step("orders",BLUE,"POST /orders/{id}","the request")}{step("orders",BLUE,f"{Q} publish","into SQS")}{step("fulfilment",PINK,f"{Q} process","out of SQS")}</div>
<div class=p style="padding:16px 18px"><div class=h style="margin-bottom:12px">How it crossed the queue <span class=u>read from real SQS, with fulfilment paused</span></div>{proof(16)}</div></body>"""
def tree(rows):
    return "".join(f'''<div style="margin-left:{d*26}px;height:38px;display:flex;align-items:center;border-left:5px solid {c};padding-left:10px;margin-bottom:6px;background:#F5F6FB;font:400 13px 'Plex'"><b style="font-weight:600">{n}</b><span style="margin-left:auto;padding-right:10px;color:{MID};font-size:12px">{x}</span></div>''' for d,c,n,x in rows)
TWO=f"""<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:900px;padding:16px;display:flex;gap:10px">
<div class=p style="flex:1;padding:16px 18px"><div class=h>Defaults <span class=u>400 orders</span></div><div class=s style="margin:10px 0 14px"><b>400/400</b><span>share the producer's trace</span></div>
<div class=k style="margin-bottom:6px">one trace, names from Splunk</div>{tree([(0,BLUE,f"{Q} publish","orders"),(0,PINK,f"{Q} process","fulfilment")])}<div class=k style="margin-top:14px;line-height:1.5">Both spans carry the same trace ID, joined on the SQS message ID for every message.</div></div>
<div class=p style="flex:1.25;padding:16px 18px"><div class=h>Receive telemetry on <span class=u>100 orders, experimental</span></div><div class=s style="margin:10px 0 14px;display:flex;gap:26px"><div><b style="color:{PINK}">100/100</b><span>process spans linked</span></div><div><b style="color:{PINK}">76</b><span>receive spans</span></div></div>
<div class=k style="margin-bottom:6px">one consumer trace, from the agent's OTLP output</div>{tree([(0,PINK,f"{Q} receive","no link"),(1,PINK,f"{Q} process","link"),(1,PINK,f"{Q} process","link"),(1,MID,"Sqs.DeleteMessageBatch","no link")])}</div></body>"""
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for n,h,w,hh in (("cover",COVER,1200,630),("step-2",WIDE,900,100),("step-3",TRACE,900,100),("step-4",TWO,900,100)):
            pg=await b.new_page(viewport={"width":w,"height":hh},device_scale_factor=2)
            await pg.set_content(h); await pg.evaluate("document.fonts.ready"); await pg.wait_for_timeout(300)
            await pg.screenshot(path=f"{n}.png",full_page=n!="cover")
        await b.close()
asyncio.run(main())
