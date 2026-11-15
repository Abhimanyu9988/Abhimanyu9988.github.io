# Cover and step image for the SQS 3.0 preview article. Every number comes from replicate run 43765072819 (issue 201).
import base64, pathlib
from playwright.sync_api import sync_playwright
fs = pathlib.Path("node_modules/@fontsource")
def font(pkg, fam, w):
    b = base64.b64encode((fs/pkg/f"files/{pkg}-latin-{w}-normal.woff2").read_bytes()).decode()
    return f"@font-face{{font-family:'{fam}';font-weight:{w};src:url(data:font/woff2;base64,{b}) format('woff2')}}"
F = "".join([font("space-grotesk","SG",500), font("space-grotesk","SG",700), font("ibm-plex-mono","Plex",400), font("ibm-plex-mono","Plex",600)])
img = lambda f: "data:image/png;base64," + base64.b64encode(open(f,"rb").read()).decode()
BLUE, PINK, INK, MID, LINE, SOFT = "#1436E0", "#D61F7A", "#101426", "#596078", "#C9CEE0", "#F5F6FB"
BASE = f"""*{{box-sizing:border-box;margin:0;padding:0}}{F}body{{background:#fff;font-family:'SG';color:{INK};-webkit-font-smoothing:antialiased}}
.p{{border:1.5px solid {LINE};padding:14px 18px;background:#fff}}.h{{font:600 13px 'Plex';margin-bottom:8px}}.u{{color:{MID};font-weight:400}}
.tile{{width:100px;height:80px;border:1.5px solid {LINE};display:flex;align-items:center;justify-content:center;background:#fff}}.tile img{{max-width:94px;max-height:72px}}
.s b{{display:block;font:700 40px/1 'SG';color:{BLUE};white-space:nowrap}}.s span{{font:400 12px 'Plex';color:{MID}}}"""
Q = "&lt;queue&gt;"
def node(n, sub, icon, dashed=False):
    c = PINK if dashed else BLUE
    return f'''<div style="display:flex;align-items:center;gap:10px;border:2.5px {'dashed' if dashed else 'solid'} {c};padding:10px 12px;width:196px;background:#fff">
<img src="{img(icon)}" style="height:40px"><div><div style="font:700 18px 'SG';white-space:nowrap">{n}</div><div style="font:400 12px 'Plex';color:{MID}">{sub}</div></div></div>'''
def arrow(label):
    return f'''<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;padding:0 6px"><div style="font:600 11.5px 'Plex';color:{INK};white-space:nowrap">{label}</div>
<div style="width:100%;height:2px;background:{INK};position:relative"><div style="position:absolute;right:-1px;top:-5px;border-left:10px solid {INK};border-top:6px solid transparent;border-bottom:6px solid transparent"></div></div></div>'''
SMAP = f'''<div style="position:relative;padding-top:30px">
<div style="position:absolute;left:110px;right:110px;top:6px;height:20px;border:2px solid {INK};border-bottom:none"></div>
<div style="position:absolute;left:50%;transform:translateX(-50%);top:-3px;background:#fff;padding:0 8px;font:400 12px 'Plex';color:{MID}">one trace, receive telemetry on</div>
<div style="display:flex;align-items:center">{node("orders","Spring Boot","spring.png")}{arrow("send "+Q)}{node("Amazon SQS","inferred","sqsicon.png",True)}{arrow("process "+Q)}{node("fulfilment","Spring Boot","spring.png")}</div></div>'''
def bar(label, n, of, c):
    pct = n*100/of
    return f'''<div style="display:flex;align-items:center;gap:10px;margin:7px 0"><div style="width:120px;font:400 12.5px 'Plex';color:{MID}">{label}</div>
<div style="flex:1;height:22px;background:{SOFT};position:relative"><div style="width:{pct:.1f}%;height:100%;background:{c}"></div></div>
<div style="width:96px;font:600 13px 'Plex';text-align:right">{n} of {of}</div></div>'''
BARS = f'''<div class=h>Messages in the producer's trace <span class=u>joined on SQS message ID</span></div>
<div style="font:600 12px 'Plex';margin-top:6px">Defaults</div>{bar("today",400,400,BLUE)}{bar("3.0 preview",400,400,PINK)}
<div style="font:600 12px 'Plex';margin-top:10px">Receive telemetry on</div>{bar("today",0,100,BLUE)}{bar("3.0 preview",100,100,PINK)}'''
def ren(a, b):
    return f'''<div style="display:flex;align-items:center;gap:8px;margin:7px 0;font:400 13px 'Plex'"><span style="flex:1;color:{MID}">{a}</span><span style="color:{PINK};font-weight:600">&rarr;</span><span style="flex:1;font-weight:600">{b}</span></div>'''
RENAME = f'''<div class=h>Operation names in Splunk APM</div>{ren(Q+" publish","send "+Q)}{ren(Q+" process","process "+Q)}{ren("Sqs.DeleteMessageBatch","delete "+Q)}{ren("POST /orders/{id}","POST /orders/{id}")}<div style="font:400 12px 'Plex';color:{MID};margin-top:10px">5 default and 10 preview traces read back through Splunk's trace API</div>'''
COVER = f'''<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:1200px;height:630px;overflow:hidden">
<div style="height:10px;background:{BLUE}"></div><div style="padding:22px 36px 20px;display:flex;flex-direction:column;gap:12px;height:620px">
<div style="display:flex;justify-content:space-between;align-items:flex-end"><div><div style="font:400 13.5px 'Plex';color:{BLUE}">OPENTELEMETRY JAVA AGENT 3.0 PREVIEW + AMAZON SQS IN SPLUNK APM</div>
<div style="font:700 40px/1.05 'SG';white-space:nowrap;letter-spacing:-.02em;margin-top:6px">Receive spans on. <span style="color:{BLUE}">Still one trace.</span></div></div>
<div style="display:flex;gap:8px">{"".join(f'<div class=tile><img src="{img(f)}"></div>' for f in ("otel.png","sqs.png","eks.png","splunk.png"))}</div></div>
<div style="display:flex;gap:12px"><div class=p style="flex:1"><div class=h>Service map <span class=u>preview environment, from Splunk APM data</span></div>{SMAP}</div>
<div class=p style="width:300px;display:flex;flex-direction:column;justify-content:center;gap:14px"><div class=s><b>0 &rarr; 100</b><span>of 100 in one trace, receive on</span></div><div class=s><b>400/400</b><span>process spans linked to producer</span></div></div></div>
<div style="display:flex;gap:12px;flex:1;min-height:0"><div class=p style="flex:1.25">{BARS}</div><div class=p style="flex:1">{RENAME}</div></div>
<div style="display:flex;justify-content:space-between;font:400 12px 'Plex'"><span>Real counts from my lab run on agent 2.33.1 with the preview flag, charted by me. Not a Splunk screen. Logos belong to their owners.</span><span>showmerit.com/abhimanyu</span></div></div></body>'''
def tree(rows):
    return "".join(f'''<div style="margin-left:{d*28}px;display:flex;align-items:center;height:40px;border-left:5px solid {c};padding:0 12px;margin-bottom:7px;background:{SOFT};font:400 13px 'Plex'">
<b style="font-weight:600;width:210px">{n}</b><span style="color:{MID};width:90px">{k}</span><span style="margin-left:auto;color:{MID};font-size:12px;white-space:nowrap">{x}</span></div>''' for d,c,n,k,x in rows)
STEP = f'''<!doctype html><meta charset=utf-8><style>{BASE}</style><body style="width:960px;padding:16px;display:flex;gap:12px">
<div class=p style="flex:1"><div class=h>Today's agent <span class=u>receive telemetry on, 100 orders</span></div><div class=s style="margin:8px 0 14px"><b>0 of 100</b><span>in the producer's trace</span></div>
<div style="font:400 12px 'Plex';color:{MID};margin-bottom:8px">one consumer trace, from the agent's output</div>
{tree([(0,BLUE,Q+" receive","CONSUMER","root"),(1,BLUE,Q+" process","CONSUMER","link to producer"),(1,MID,"Sqs.DeleteMessageBatch","CLIENT","")])}</div>
<div class=p style="flex:1"><div class=h>3.0 preview <span class=u>receive telemetry on, 100 orders</span></div><div class=s style="margin:8px 0 14px"><b style="color:{PINK}">100 of 100</b><span>in the producer's trace</span></div>
<div style="font:400 12px 'Plex';color:{MID};margin-bottom:8px">consumer spans, from the agent's output</div>
{tree([(0,MID,"send "+Q,"PRODUCER","in orders"),(1,PINK,"process "+Q,"CONSUMER","link to producer"),(1,MID,"delete "+Q,"CLIENT","")])}
<div style="font:400 12px 'Plex';color:{MID};margin-top:8px">receive {Q} is now CLIENT: 72 spans, linked to all 100 producer traces</div></div></body>'''
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, html, w, h in (("cover.png", COVER, 1200, 630), ("step-1.png", STEP, 960, None)):
        pg = b.new_page(viewport={"width": w, "height": h or 200}, device_scale_factor=2)
        pg.set_content(html); pg.wait_for_timeout(300)
        pg.screenshot(path=name, full_page=h is None)
    b.close()
