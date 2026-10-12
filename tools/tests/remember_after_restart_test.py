"""The app must remember the chosen files (and all work) after it is closed and opened again: the page keeps them
in browser storage, which belongs to its address, so the engine must come back on the same port.
Run (Linux, needs xvfb + test media in /tmp/uimedia): uv run --project engine --with playwright python tools/tests/remember_after_restart_test.py"""
import subprocess,time,json,os,sys
from playwright.sync_api import sync_playwright
D='/home/user/studio-pro/desktop'
M=json.loads(open('/tmp/uimedia/media.json').read())
env={**os.environ,'STUDIO_ENGINE_HOME':'/tmp/rs_home'}
def launch():
    return subprocess.Popen(['xvfb-run','-a',D+'/node_modules/.bin/electron','--no-sandbox','--remote-debugging-port=9333','.'],cwd=D,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
def page(p):
    for _ in range(120):
        try:
            b=p.chromium.connect_over_cdp('http://127.0.0.1:9333')
            for c in b.contexts:
                for pg in c.pages:
                    if '127.0.0.1:' in pg.url: return b,pg
            b.close()
        except Exception: pass
        time.sleep(1)
    raise SystemExit('no page')
with sync_playwright() as p:
    for run in (1,2):
        e=launch()
        b,pg=page(p)
        for _ in range(90):
            if 'Engine connected' in (pg.inner_text('#engStatus') or ''): break
            time.sleep(1)
        print(run,'url',pg.url.split('#')[0])
        if run==1:
            pg.evaluate(f"async()=>{{STU.settings.separate=true;engSetFile('video',{json.dumps(M['video'])});engSetFile('audio',{json.dumps(M['audio'])});await stuSave()}}")
            time.sleep(1)
        print(run,'remembered video:',pg.evaluate("()=>STU.engVideo||''"),'| on screen:',pg.inner_text('#engVideoPath')[:60])
        b.close()
        os.killpg(e.pid,15);e.wait();time.sleep(3)
